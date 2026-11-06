"""What to work on: gathers one user's values from the warehouse and Postgres
and runs the rules of src/insights.py over them (the dashboard card)."""
import re
from datetime import date, timedelta

from src import insights as rules
from src.api.database import Race, TrainingPlan, TrainingPlanEntry
from src.api.deps import get_db_connection
from src.api.services.digest import runs_on_foot, session_done
from src.api.services.metrics import EASY_RUN_SQL, detected_max_hr, load_history, resolve_resting_hr

EASY_DAYS = 28
EFFICIENCY_DAYS = rules.EF_RECENT_DAYS + rules.EF_BASELINE_DAYS
NIGHT_DAYS = rules.NIGHTS_RECENT + rules.NIGHTS_BASELINE
PLAN_DAYS = 14
RACE_NAME = re.compile(r"\brace\b", re.IGNORECASE)
# Climb in m per km; a treadmill run has none.
CLIMB_SQL = "COALESCE(da.total_elevation_gain / NULLIF(da.distance, 0) * 1000, 0)"


def easy_runs(con, athlete_id, since, today, race_days):
    """The dashboard's easy runs after since, without detected tempo runs,
    treadmill runs (no climb) and races (a day of one of the user's races, or
    "race" in the name). Detected intervals stay: see src/insights.py."""
    rows = con.execute(f"""
        SELECT da.start_date_local::DATE, da.name, da.average_heartrate, {CLIMB_SQL}
        FROM dim_activity da
        JOIN activity_effectiveness ae ON ae.activity_id = da.activity_id
        LEFT JOIN activity_structure ast ON ast.activity_id = da.activity_id
        WHERE da.athlete_id = ? AND {EASY_RUN_SQL} AND da.average_heartrate > 0 AND da.total_elevation_gain > 0
          AND COALESCE(ast.kind, '') <> 'tempo' AND da.start_date_local::DATE > ? AND da.start_date_local::DATE <= ?
    """, [athlete_id, since, today]).fetchall()
    return [
        {"avg_hr": avg_hr, "name": name, "climb": climb}
        for day, name, avg_hr, climb in rows
        if day not in race_days and not RACE_NAME.search(name or "")
    ]


def zone_seconds(con, athlete_id, since, today):
    row = con.execute("""
        SELECT SUM(ae.zone_1_sec), SUM(ae.zone_2_sec), SUM(ae.zone_3_sec), SUM(ae.zone_4_sec), SUM(ae.zone_5_sec)
        FROM dim_activity da JOIN activity_effectiveness ae ON ae.activity_id = da.activity_id
        WHERE da.athlete_id = ? AND da.type = 'Run' AND da.start_date_local::DATE > ? AND da.start_date_local::DATE <= ?
    """, [athlete_id, since, today]).fetchone()
    return [value or 0 for value in row]


def efficiency_points(con, athlete_id, since, today):
    """The dashboard's easy-run efficiency points with their climb."""
    rows = con.execute(f"""
        SELECT da.start_date_local::DATE, da.name, ae.efficiency_factor, {CLIMB_SQL}
        FROM dim_activity da JOIN activity_effectiveness ae ON ae.activity_id = da.activity_id
        WHERE da.athlete_id = ? AND {EASY_RUN_SQL} AND da.start_date_local::DATE > ? AND da.start_date_local::DATE <= ?
    """, [athlete_id, since, today]).fetchall()
    return [{"day": day, "name": name, "ef": ef, "climb": climb} for day, name, ef, climb in rows]


def coros_nights(con, athlete_id, since, today):
    rows = con.execute(
        "SELECT day, hrv, rhr FROM coros_daily WHERE athlete_id = ? AND day > ? AND day <= ?", [athlete_id, since, today]
    ).fetchall()
    return [{"day": day, "hrv": hrv, "rhr": rhr} for day, hrv, rhr in rows]


def planned_sessions(db, user_id, start, end):
    """The planned sessions from start up to end, rest days left out."""
    plan_ids = [row[0] for row in db.query(TrainingPlan.id).filter(TrainingPlan.user_id == user_id).all()]
    if not plan_ids:
        return []
    entries = db.query(TrainingPlanEntry).filter(
        TrainingPlanEntry.plan_id.in_(plan_ids), TrainingPlanEntry.date >= start, TrainingPlanEntry.date < end
    ).all()
    return [entry for entry in entries if (entry.workout_type or "").lower() != "rest"]


def insights_for(db, user, unit="mi", today=None):
    """The ranked insights for one user as of today (default: the real today);
    unit is "mi" or "km" for the paces in the text."""
    today = today or date.today()
    athlete_id = user.athlete_id
    race_days = {race.race_date for race in db.query(Race).filter(Race.user_id == user.id).all()}
    plan_start = today - timedelta(days=PLAN_DAYS)
    # Today's session may still be ahead, so the plan window ends yesterday.
    sessions = planned_sessions(db, user.id, plan_start, today)

    con = get_db_connection()
    try:
        hr_max = float(user.max_hr or detected_max_hr(con, athlete_id))
        hr_rest = float(resolve_resting_hr(user))
        easy_since = today - timedelta(days=EASY_DAYS)
        easy = easy_runs(con, athlete_id, easy_since, today, race_days)
        zones = zone_seconds(con, athlete_id, easy_since, today)
        efficiency = efficiency_points(con, athlete_id, today - timedelta(days=EFFICIENCY_DAYS), today)
        nights = coros_nights(con, athlete_id, today - timedelta(days=NIGHT_DAYS), today)
        history = load_history(con, athlete_id, today)
        runs = runs_on_foot(con, athlete_id, plan_start) + runs_on_foot(con, athlete_id, plan_start + timedelta(days=7))
    finally:
        con.close()

    load = None
    if history is not None and len(history) > 7:
        ctl, acwr = history["CTL"], history["ACWR"]
        load = rules.load_ramp(float(ctl.iloc[-1]), float(ctl.iloc[-8]), float(acwr.iloc[-1]), len(history))
    done = sum(1 for entry in sessions if session_done(entry, [run for run in runs if run["day"] == entry.date]))
    # The dashboard's reference heart rate: the middle of zone 2.
    hr_ref = round(hr_rest + 0.65 * (hr_max - hr_rest))
    return rules.rank([
        load,
        rules.recovery_trend(nights, today),
        rules.easy_effort(easy, zones, hr_max, hr_rest),
        rules.efficiency_terrain(efficiency, today, hr_ref, unit),
        rules.plan_slipping(len(sessions), done),
    ])
