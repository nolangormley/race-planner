"""Race predictions for one athlete (src/predictions.py): their best efforts
and recent training from the warehouse, and a race's stored course. The
predictions card (routers/analytics.py) and the pacing page's default goal
(routers/pacing.py) both go through here, so they agree."""
from datetime import timedelta

from src import predictions as model
from src.metrics import BEST_EFFORT_DISTANCES_M
from src.pacing import course_profile, parse_gpx_course

LABELS = {distance: label for label, distance in BEST_EFFORT_DISTANCES_M.items()}


def best_efforts(con, athlete_id, since):
    """Every best effort of the athlete's activities since a date, as dicts
    with distance_m, label, seconds, date, activity_id and name."""
    rows = con.execute("""
        SELECT be.distance_m, be.seconds, da.start_date_local::DATE, da.activity_id, da.name
        FROM best_efforts be JOIN dim_activity da ON da.activity_id = be.activity_id
        WHERE da.athlete_id = ? AND da.start_date_local >= ?
        ORDER BY da.start_date_local, be.distance_m
    """, [athlete_id, since]).fetchall()
    return [
        {"distance_m": distance, "label": LABELS.get(distance, f"{distance:.0f} m"), "seconds": round(seconds),
         "date": day, "activity_id": activity_id, "name": name}
        for distance, seconds, day, activity_id, name in rows
    ]


def training(con, athlete_id, today):
    """Mean weekly running distance over the last TRAINING_DAYS, and the mean
    pace of the runs under HILLY_CLIMB, or None without such runs."""
    since = today - timedelta(days=model.TRAINING_DAYS)
    total_m, flat_s, flat_m = con.execute("""
        SELECT SUM(distance),
               SUM(moving_time) FILTER (WHERE total_elevation_gain / distance * 1000 < ?),
               SUM(distance) FILTER (WHERE total_elevation_gain / distance * 1000 < ?)
        FROM dim_activity
        WHERE athlete_id = ? AND type = 'Run' AND distance > 0 AND moving_time > 0
          AND start_date_local::DATE > ? AND start_date_local::DATE <= ?
    """, [model.HILLY_CLIMB, model.HILLY_CLIMB, athlete_id, since, today]).fetchone()
    if not flat_m:
        return None
    return {"weekly_km": total_m / 1000 / (model.TRAINING_DAYS / 7), "pace_s_per_km": flat_s / flat_m * 1000}


def gather(con, user, today):
    """What the predictions need as of today: {"efforts", "old", "training"}.
    The efforts are the last RECENT_DAYS', or with old True those of the last
    OLD_DAYS when there are none; empty without runs with pace."""
    efforts = best_efforts(con, user.athlete_id, today - timedelta(days=model.RECENT_DAYS))
    old = not efforts
    if old:
        efforts = best_efforts(con, user.athlete_id, today - timedelta(days=model.OLD_DAYS))
    return {
        "efforts": efforts,
        "old": old,
        "training": training(con, user.athlete_id, today),
    }


def predict(inputs, distance_m, course=None):
    """The prediction for one distance from gather's inputs, over the
    course's profile when there is a course."""
    profile = course_profile(course, distance_m)[0] if course else None
    return model.predict(inputs["efforts"], distance_m, inputs["old"], inputs["training"], profile)


def course_of(race):
    """The race's parsed course, or None without a file (or with one that no
    longer parses)."""
    if not race.course_gpx:
        return None
    try:
        return parse_gpx_course(race.course_gpx)
    except ValueError:
        return None
