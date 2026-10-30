"""Weekly digest email: last week's running next to the week before, how
the plan went, fitness and form, new best efforts, this week's sessions
and upcoming races. Sent on Monday mornings to users who opted in.

compose_digest turns plain values into the email text so it can be tested
without a database; build_digest gathers those values for one user.
send_due_digests is the pass the scheduler (services/scheduler.py) runs.
"""
import logging
import os
from datetime import datetime, timedelta

from src.api.database import SessionLocal, User, Race, TrainingPlan, TrainingPlanEntry
from src.api.deps import get_db_connection, DEFAULT_MAX_HR
from src.api.services.email import send_email
from src.api.services.metrics import calculate_training_status_logic, detected_max_hr, resolve_resting_hr
from src.api.services.readiness import energy_for
from src.metrics import planned_distance_km

logger = logging.getLogger("race_planner.digest")

METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.28084
SEND_HOUR = 6               # server local time, on Mondays
DISTANCE_LABELS = {1609: "1 mile", 3219: "2 miles", 5000: "5K", 8047: "5 miles", 10000: "10K", 16093: "10 miles", 21097: "Half marathon", 42195: "Marathon"}


def week_start(day):
    return day - timedelta(days=day.weekday())


def digest_due(user, now):
    """True when the user opted in, it is Monday after SEND_HOUR, and this
    week's digest has not gone out yet."""
    if not user.weekly_digest or not user.email:
        return False
    if now.weekday() != 0 or now.hour < SEND_HOUR:
        return False
    return user.digest_sent_at is None or user.digest_sent_at.date() < week_start(now.date())


# ---- Formatting ----

def miles(meters):
    return f"{meters / METERS_PER_MILE:.1f} mi"


def duration(seconds):
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def race_time(seconds):
    hours, rest = divmod(int(round(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def day_label(day):
    return day.strftime("%b %-d")


def distance_label(meters):
    return DISTANCE_LABELS.get(int(round(meters)), f"{meters / 1000:g} km")


def form_label(tsb):
    if tsb > 15:
        return "fresh"
    if tsb > 5:
        return "rested"
    if tsb >= -10:
        return "neutral"
    if tsb >= -30:
        return "productive"
    return "overreaching"


def compose_digest(name, today, week, previous, plan, sessions, races, records, status, app_url=None, recovery=None, energy=None):
    """The subject and body of the digest as of today; last week is the
    Monday to Sunday before today's week.

    week and previous: {"runs", "distance_m", "moving_s", "climb_m"}.
    plan: {"planned", "done", "planned_m"} for last week, or None without a plan.
    sessions: this week's (date, workout_type, approximate_distance), rests left out.
    races: upcoming (name, race_length, race_date). records: (label, seconds, date)
    for best efforts set last week. status: the training status dict, or None.
    recovery: last week's nightly averages from COROS {"rhr", "hrv", "hrv_baseline", "sleep_min"}, or None.
    energy: this morning's energy and today's call from services.readiness.energy_for
    ({"morning", "reasons", "call": {"label", "hint", ...}, ...}), or None.
    """
    last_week = week_start(today) - timedelta(days=7)
    first_name = (name or "there").split()[0]
    subject = f"Your week in running: {miles(week['distance_m'])} over {week['runs']} run{'s' if week['runs'] != 1 else ''}"
    delta_m = week["distance_m"] - previous["distance_m"]
    lines = [
        f"Hi {first_name},",
        "",
        f"Last week ({day_label(last_week)} to {day_label(last_week + timedelta(days=6))})",
        f"  Runs       {week['runs']} ({previous['runs']} the week before)",
        f"  Distance   {miles(week['distance_m'])} ({'+' if delta_m >= 0 else '-'}{miles(abs(delta_m))} on the week before)",
        f"  Time       {duration(week['moving_s'])}",
        f"  Climb      {round(week['climb_m'] * FEET_PER_METER):,} ft",
    ]
    if plan and plan["planned"]:
        planned = f", {miles(week['distance_m'])} of {miles(plan['planned_m'])} planned" if plan["planned_m"] else ""
        lines.append(f"  Plan       {plan['done']} of {plan['planned']} planned sessions done{planned}")

    if status:
        lines += [
            "",
            "Fitness",
            f"  Fitness    {status['fitness_ctl']:.0f}",
            f"  Fatigue    {status['fatigue_atl']:.0f}",
            f"  Form       {status['form_tsb']:+.0f}  ({form_label(status['form_tsb'])})",
        ]

    if recovery or energy:
        lines += ["", "Recovery (nightly averages)"]
    if energy:
        lines.append(f"  Today      {energy['call']['label']}, energy {energy['morning']} of 100 on waking")
        lines += [f"             {reason}" for reason in energy["reasons"]]
        if energy["call"]["hint"]:
            lines.append(f"             {energy['call']['hint']}")
    if recovery:
        if recovery.get("rhr"):
            lines.append(f"  Resting HR {recovery['rhr']:.0f} bpm")
        if recovery.get("hrv"):
            baseline = f" (baseline {recovery['hrv_baseline']:.0f})" if recovery.get("hrv_baseline") else ""
            lines.append(f"  HRV        {recovery['hrv']:.0f} ms{baseline}")
        if recovery.get("sleep_min"):
            lines.append(f"  In bed     {duration(recovery['sleep_min'] * 60)} a night")

    if records:
        lines += ["", "New bests"]
        lines += [f"  {label:<14} {race_time(seconds)} on {day_label(day)}" for label, seconds, day in records]

    lines += ["", "This week"]
    if sessions:
        lines += [f"  {day.strftime('%a')}  {kind}{' · ' + distance if distance else ''}" for day, kind, distance in sessions]
    else:
        lines.append("  Nothing planned yet. Build a plan on the calendar page.")

    if races:
        lines += ["", "Races"]
        for race_name, race_length, race_day in races:
            days_to_go = (race_day - today).days
            when = "today" if days_to_go == 0 else f"in {days_to_go} day{'s' if days_to_go != 1 else ''}"
            lines.append(f"  {race_name or race_length} · {race_length} · {when} ({day_label(race_day)})")

    lines += ["", "Race Planner" + (f"  {app_url}" if app_url else "")]
    return subject, "\n".join(lines)


# ---- Gathering ----

def week_totals(con, athlete_id, start):
    row = con.execute("""
        SELECT COUNT(*), COALESCE(SUM(distance), 0), COALESCE(SUM(moving_time), 0), COALESCE(SUM(total_elevation_gain), 0)
        FROM dim_activity
        WHERE athlete_id = ? AND type = 'Run' AND start_date_local >= ? AND start_date_local < ?
    """, [athlete_id, start, start + timedelta(days=7)]).fetchone()
    return {"runs": row[0], "distance_m": float(row[1]), "moving_s": float(row[2]), "climb_m": float(row[3])}


def new_records(con, athlete_id, start):
    """Best efforts set in the week from start that beat everything before."""
    rows = con.execute("""
        WITH efforts AS (
            SELECT be.distance_m, be.seconds, da.start_date_local::DATE AS day
            FROM best_efforts be JOIN dim_activity da ON da.activity_id = be.activity_id
            WHERE da.athlete_id = ?
        )
        SELECT distance_m, seconds, day FROM efforts this_week
        WHERE day >= ? AND day < ?
          AND seconds <= (SELECT MIN(seconds) FROM efforts WHERE efforts.distance_m = this_week.distance_m)
        ORDER BY distance_m
    """, [athlete_id, start, start + timedelta(days=7)]).fetchall()
    return [(distance_label(row[0]), row[1], row[2]) for row in rows]


def recovery_week(con, athlete_id, start):
    """Nightly averages from COROS for the week from start, or None without readings."""
    row = con.execute("""
        SELECT AVG(rhr), AVG(hrv), AVG(hrv_baseline), AVG(time_in_bed_min)
        FROM coros_daily WHERE athlete_id = ? AND day >= ? AND day < ?
    """, [athlete_id, start, start + timedelta(days=7)]).fetchone()
    if not row or (row[0] is None and row[1] is None and row[3] is None):
        return None
    return {"rhr": row[0], "hrv": row[1], "hrv_baseline": row[2], "sleep_min": row[3]}


def plan_week(db, user_id, start):
    """Planned sessions, completed count and planned distance in the week from start."""
    plan_ids = [row[0] for row in db.query(TrainingPlan.id).filter(TrainingPlan.user_id == user_id).all()]
    if not plan_ids:
        return None, []
    entries = (
        db.query(TrainingPlanEntry)
        .filter(TrainingPlanEntry.plan_id.in_(plan_ids), TrainingPlanEntry.date >= start, TrainingPlanEntry.date < start + timedelta(days=7))
        .order_by(TrainingPlanEntry.date)
        .all()
    )
    sessions = [item for item in entries if (item.workout_type or "").lower() != "rest"]
    planned_km = sum(planned_distance_km(item.approximate_distance) or 0.0 for item in sessions)
    summary = {"planned": len(sessions), "done": sum(1 for item in sessions if item.is_completed), "planned_m": planned_km * 1000}
    return summary, sessions


def build_digest(db, user, now=None):
    """Subject and body for one user as of now (default: the real now)."""
    now = now or datetime.now()
    today = now.date()
    this_week = week_start(today)
    last_week = this_week - timedelta(days=7)
    athlete_id = user.athlete_id

    week = previous = {"runs": 0, "distance_m": 0.0, "moving_s": 0.0, "climb_m": 0.0}
    records, status, recovery, today_energy = [], None, None, None
    if athlete_id:
        con = get_db_connection()
        try:
            week = week_totals(con, athlete_id, last_week)
            previous = week_totals(con, athlete_id, last_week - timedelta(days=7))
            records = new_records(con, athlete_id, last_week)
            recovery = recovery_week(con, athlete_id, last_week)
            hr_max = user.max_hr or detected_max_hr(con, athlete_id) or DEFAULT_MAX_HR
            hr_rest = resolve_resting_hr(con, user)
        finally:
            con.close()
        status = calculate_training_status_logic(athlete_id, hr_max, hr_rest)
        today_entries = plan_week(db, user.id, today)[1]
        planned_today = next((item.workout_type for item in today_entries if item.date == today), None)
        con = get_db_connection()
        try:
            result = energy_for(con, athlete_id, now, planned_workout=planned_today)
        finally:
            con.close()
        today_energy = result if result["available"] and result["call"] else None

    plan, _ = plan_week(db, user.id, last_week)
    _, this_week_entries = plan_week(db, user.id, this_week)
    sessions = [(item.date, item.workout_type, item.approximate_distance) for item in this_week_entries]
    races = [
        (race.name, race.race_length, race.race_date)
        for race in db.query(Race).filter(Race.user_id == user.id, Race.race_date >= today).order_by(Race.race_date).limit(3).all()
    ]
    return compose_digest(user.name, today, week, previous, plan, sessions, races, records, status, app_url=os.getenv("APP_URL"), recovery=recovery, energy=today_energy)


# ---- Sending ----

def send_digest(db, user, now=None):
    """Builds and sends the digest and stamps the user. Commits."""
    now = now or datetime.now()
    subject, body = build_digest(db, user, now)
    send_email(user.email, subject, body)
    user.digest_sent_at = now
    db.commit()
    return subject


def send_due_digests(now=None):
    """Sends to every opted-in user whose digest is due. Returns how many went out."""
    now = now or datetime.now()
    sent = 0
    db = SessionLocal()
    try:
        for user in db.query(User).filter(User.weekly_digest.is_(True)).all():
            if not digest_due(user, now):
                continue
            try:
                send_digest(db, user, now)
                sent += 1
            except Exception:
                db.rollback()
                logger.exception("Weekly digest to %s failed", user.email)
    finally:
        db.close()
    return sent
