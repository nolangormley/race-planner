"""Today's energy and call for one athlete: reads the COROS nights and the
TRIMP of each activity from the warehouse, then runs src.energy and
src.readiness. Feeds GET /api/energy, GET /api/readiness and the digest."""
from datetime import datetime, timedelta

from src.energy import BASELINE_NIGHTS, DEFAULT_DAYS, energy, first_day
from src.readiness import call, session_hint

RECENT_NIGHTS = 2        # a call needs a reading from today or one of the two days before


def energy_for(con, athlete_id, now=None, days=DEFAULT_DAYS, planned_workout=None):
    """The energy curve over the last days with today's call, or
    {"available": False} without a COROS reading in that window. The call
    is None when the newest reading is too old to speak for today.

    now defaults to the server's local time; activity start times are local
    to the athlete, so the two are taken to match, as the digest's send hour does."""
    now = now or datetime.now()
    today = now.date()
    since = first_day(today, days)
    nights = [
        {"day": row[0], "hrv": row[1], "rhr": row[2], "sleep_min": row[3]}
        for row in con.execute("""
            SELECT day, hrv, rhr, time_in_bed_min FROM coros_daily
            WHERE athlete_id = ? AND day >= ? AND day <= ?
            ORDER BY day
        """, [athlete_id, since - timedelta(days=BASELINE_NIGHTS), today]).fetchall()
    ]
    readings = [night["day"] for night in nights if night["hrv"] or night["rhr"]]
    if not readings or readings[-1] <= today - timedelta(days=days):
        return {"available": False}
    activities = [
        {"activity_id": row[0], "name": row[1], "type": row[2], "start": row[3], "seconds": row[4], "trimp": row[5]}
        for row in con.execute("""
            SELECT da.activity_id, da.name, da.type, da.start_date_local, COALESCE(da.elapsed_time, da.moving_time), ae.trimp_banister
            FROM dim_activity da JOIN activity_effectiveness ae ON ae.activity_id = da.activity_id
            WHERE da.athlete_id = ? AND da.start_date_local >= ? AND da.start_date_local <= ? AND ae.trimp_banister > 0
            ORDER BY da.start_date_local
        """, [athlete_id, since, now]).fetchall()
    ]
    result = energy(nights, activities, now, days)
    today_call = None
    if readings[-1] >= today - timedelta(days=RECENT_NIGHTS):
        today_call = call(result["morning"], result["trend"])
        result["reasons"] += today_call.pop("reasons")
        today_call["hint"] = session_hint(today_call["label"], planned_workout)
    return {"available": True, **result, "call": today_call}
