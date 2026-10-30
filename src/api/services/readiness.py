"""Gathers last night's COROS reading, the usual resting heart rate and
today's form for src.readiness, for the dashboard and the digest."""
from datetime import date, timedelta

from src.readiness import readiness, session_hint
from src.api.services.metrics import coros_resting_hr

RECENT_NIGHTS = 2        # a reading older than this is stale for "today"


def latest_night(con, athlete_id, today):
    row = con.execute("""
        SELECT day, hrv, hrv_baseline, rhr, time_in_bed_min FROM coros_daily
        WHERE athlete_id = ? AND day >= ? AND (hrv IS NOT NULL OR rhr IS NOT NULL)
        ORDER BY day DESC LIMIT 1
    """, [athlete_id, today - timedelta(days=RECENT_NIGHTS)]).fetchone()
    if not row:
        return None
    return {"day": row[0], "hrv": row[1], "hrv_baseline": row[2], "rhr": row[3], "sleep_min": row[4]}


def readiness_for(con, athlete_id, tsb=None, today=None, planned_workout=None):
    """Today's call with the inputs behind it, or {"available": False}."""
    today = today or date.today()
    night = latest_night(con, athlete_id, today) if athlete_id else None
    if not night:
        return {"available": False}
    usual = coros_resting_hr(con, athlete_id)
    call = readiness(hrv=night["hrv"], hrv_baseline=night["hrv_baseline"], rhr=night["rhr"], rhr_usual=usual, sleep_min=night["sleep_min"], tsb=tsb)
    if not call:
        return {"available": False}
    return {
        "available": True,
        "night": night["day"].isoformat(),
        **call,
        "hint": session_hint(call["label"], planned_workout),
        "inputs": {"hrv": night["hrv"], "hrv_baseline": night["hrv_baseline"], "rhr": night["rhr"], "rhr_usual": usual, "sleep_min": night["sleep_min"], "tsb": tsb},
    }
