"""Marks planned sessions done when a synced activity matches them. Runs
after every Strava sync (routers/sync.py record_sync_result).

For each planned entry of the last seven days that is not done and not a
rest day, an activity on the same day completes it when:
- Intervals / Tempo: a run whose detected structure (src/intervals.py) is
  that kind;
- Long Run: the day's longest run covers 80% of the planned distance, or is
  the longest run of that Monday-to-Sunday week;
- Easy Run / Race: any run;
- Cross Training / Strength: any activity, preferring one that is not a run.
An activity completes at most one entry; other workout types are left alone.
"""
from datetime import date, timedelta

from src.api.database import SessionLocal, TrainingPlan, TrainingPlanEntry
from src.api.deps import get_db_connection
from src.metrics import planned_distance_km

LOOKBACK_DAYS = 7
LONG_RUN_SHARE = 0.8
RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
STRUCTURED = {"intervals", "tempo"}
ANY_RUN = {"easy run", "race"}
ANY_ACTIVITY = {"cross training", "strength"}


def week_start(day):
    return day - timedelta(days=day.weekday())


def warehouse_activities(con, athlete_id, first_day, last_day):
    rows = con.execute("""
        SELECT da.activity_id, da.start_date_local::DATE, da.type, COALESCE(da.distance, 0), ast.kind
        FROM dim_activity da
        LEFT JOIN activity_structure ast ON ast.activity_id = da.activity_id
        WHERE da.athlete_id = ? AND da.start_date_local >= ? AND da.start_date_local < ?
        ORDER BY da.start_date_local
    """, [athlete_id, first_day, last_day + timedelta(days=1)]).fetchall()
    return [{"activity_id": row[0], "day": row[1], "type": row[2], "distance": float(row[3]), "kind": row[4]} for row in rows]


def matching_activity(entry, activities, used):
    """The activity that completes `entry`, or None. `activities` covers the
    entry's whole week; ids in `used` are already taken."""
    workout = (entry.workout_type or "").strip().lower()
    that_day = [activity for activity in activities if activity["day"] == entry.date and activity["activity_id"] not in used]
    runs = [activity for activity in that_day if activity["type"] in RUN_TYPES]
    longest = max(runs, key=lambda run: run["distance"], default=None)
    if workout in STRUCTURED:
        return next((run for run in runs if run["kind"] == workout), None)
    if workout == "long run":
        if longest is None:
            return None
        planned_km = planned_distance_km(entry.approximate_distance)
        if planned_km and longest["distance"] >= LONG_RUN_SHARE * planned_km * 1000:
            return longest
        week = [activity["distance"] for activity in activities
                if activity["type"] in RUN_TYPES and week_start(activity["day"]) == week_start(entry.date)]
        return longest if longest["distance"] >= max(week) else None
    if workout in ANY_RUN:
        return longest
    if workout in ANY_ACTIVITY:
        others = [activity for activity in that_day if activity["type"] not in RUN_TYPES]
        return (others or that_day or [None])[0]
    return None


def complete_matching_entries(db, con, user_id, athlete_id, today=None):
    """Marks the user's matching entries of the last week done and links the
    activity. Commits. Returns the ids of the entries it marked."""
    today = today or date.today()
    first_day = today - timedelta(days=LOOKBACK_DAYS - 1)
    entries = (
        db.query(TrainingPlanEntry)
        .join(TrainingPlan, TrainingPlan.id == TrainingPlanEntry.plan_id)
        .filter(TrainingPlan.user_id == user_id, TrainingPlanEntry.date >= first_day, TrainingPlanEntry.date <= today)
        .order_by(TrainingPlanEntry.date, TrainingPlanEntry.id)
        .all()
    )
    used = {entry.strava_activity_id for entry in entries if entry.strava_activity_id}
    open_entries = [entry for entry in entries if not entry.is_completed and (entry.workout_type or "").strip().lower() != "rest"]
    if not open_entries:
        return []
    activities = warehouse_activities(con, athlete_id, week_start(first_day), today)
    marked = []
    for entry in open_entries:
        match = matching_activity(entry, activities, used)
        if match:
            entry.is_completed = True
            entry.strava_activity_id = match["activity_id"]
            used.add(match["activity_id"])
            marked.append(entry.id)
    if marked:
        db.commit()
    return marked


def auto_complete_plan(user_id, athlete_id):
    db = SessionLocal()
    con = get_db_connection()
    try:
        return complete_matching_entries(db, con, user_id, athlete_id)
    finally:
        con.close()
        db.close()
