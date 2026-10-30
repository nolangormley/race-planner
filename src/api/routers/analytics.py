"""Training status, VO2 max, pace zones, race predictions, aerobic efficiency and the dashboard analytics."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import date, timedelta
import logging

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, Race, User
from src.pacing import parse_clock
from src.metrics import race_time_for_vdot, riegel_prediction, BEST_EFFORT_DISTANCES_M
from src.api.services.coach import get_ai_insight
from src.api.services.llm import cached_call
from src.api.deps import get_db_connection
from src.api.services.metrics import (
    calculate_training_status_logic,
    calculate_vo2max,
    calculate_pace_zones,
    detected_max_hr,
    resolve_resting_hr,
)

logger = logging.getLogger("race_planner.api")
router = APIRouter()


@router.get("/status/{user_id}")
def get_status(user_id: int, current_user: User = Depends(get_current_user)):
    # NOTE: user_id here is the warehouse athlete id (User.athlete_id), not User.id (see
    # agent_prompting/improvement_plan.md — the two are used inconsistently
    # across this file's routes).
    if user_id != current_user.athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's status")

    status = calculate_training_status_logic(user_id, hr_max=current_user.max_hr, hr_rest=current_user.resting_hr)
    if not status:
        raise HTTPException(status_code=404, detail="No training data found for user")

    status['race_type'] = current_user.race_length
    status['race_date'] = str(current_user.race_date) if current_user.race_date else None
    status['goal_time'] = current_user.race_goal_time

    # Cached for an hour — this route is hit on every dashboard load, and
    # was previously a fresh, unbounded billed LLM call every single time
    # (see agent_prompting/improvement_plan.md Section 3). CTL/ATL/TSB don't
    # meaningfully change within an hour, so a little staleness is an
    # acceptable trade for not re-billing/re-latency-ing every page view.
    status['ai_insight'] = cached_call(
        f"ai_insight:status:{user_id}",
        ttl_seconds=3600,
        compute_fn=lambda: get_ai_insight(status, context="status"),
    )
    return status

@router.get("/vo2max/{user_id}")
def get_vo2max(user_id: int, current_user: User = Depends(get_current_user)):
    # user_id here is the warehouse athlete id (see /status/{user_id} above).
    if user_id != current_user.athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's VO2max")
    vo2max_data = calculate_vo2max(user_id, hr_max=current_user.max_hr, hr_rest=current_user.resting_hr)
    if not vo2max_data:
        raise HTTPException(status_code=404, detail="No stream data found to calculate VO2 Max for this user.")
    return vo2max_data

@router.get("/pace_zones/{user_id}")
def get_pace_zones(user_id: int, current_user: User = Depends(get_current_user)):
    # user_id here is the warehouse athlete id (see /status/{user_id} above).
    if user_id != current_user.athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's pace zones")
    zones = calculate_pace_zones(user_id, hr_max=current_user.max_hr)
    if not zones:
        raise HTTPException(status_code=404, detail="No stream data found to calculate Pace Zones for this user.")
    return zones

@router.get("/analytics/{user_id}")
def get_analytics(user_id: int, current_user: User = Depends(get_current_user)):
    # user_id here is the warehouse athlete id (see /status/{user_id} above).
    if user_id != current_user.athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's analytics")
    con = get_db_connection()
    try:
        hr_max = float(current_user.max_hr or detected_max_hr(con, user_id))
        hr_rest = resolve_resting_hr(con, current_user)

        # Bounded to the same 30-day window as load_dist below, and
        # percentages are computed here rather than left for the frontend to
        # derive — previously this returned a raw COUNT(*) across the
        # athlete's *entire* history with no date bound, which is what
        # produced the "Y-axis to 120,000" chart bug. That got fixed only in
        # the frontend (a client-side percentage conversion); this endpoint
        # itself still returned unbounded raw seconds, so any other consumer
        # (or a future frontend change that forgets to renormalize) would
        # reproduce the same bug. See
        # agent_prompting/improvement_plan.md Section 3.
        # Zones are shares of heart rate reserve (Karvonen), the same reserve
        # the TRIMP in src/pipeline.py is built on.
        reserve = hr_max - hr_rest
        zone_edges = [hr_rest + share * reserve for share in (0.60, 0.70, 0.80, 0.90)]
        hr_query = """
        SELECT
            CASE
                WHEN value < ? THEN 'Zone 1 (Recovery)'
                WHEN value < ? THEN 'Zone 2 (Aerobic)'
                WHEN value < ? THEN 'Zone 3 (Tempo)'
                WHEN value < ? THEN 'Zone 4 (Threshold)'
                ELSE 'Zone 5 (Anaerobic)'
            END as zone,
            COUNT(*) as secs
        FROM stream_heartrate h
        JOIN dim_activity a ON h.activity_id = a.activity_id
        WHERE a.athlete_id = ? AND a.start_date_local >= current_date - interval '30 days'
        GROUP BY 1 ORDER BY 1
        """
        hr_data = con.execute(hr_query, [*zone_edges, user_id]).fetchall()
        total_hr_secs = sum(row[1] for row in hr_data)

        load_query = """
        SELECT type, SUM(distance) as total_dist, SUM(moving_time) as total_time, COUNT(*) as activities
        FROM dim_activity
        WHERE athlete_id = ? AND start_date_local >= current_date - interval '30 days'
        GROUP BY type
        ORDER BY total_time DESC
        """
        load_data = con.execute(load_query, [user_id]).fetchall()

        # Run volume per week for the last 12 weeks (Monday-based), with
        # empty weeks filled in so the chart's x-axis is continuous.
        weekly_rows = con.execute("""
        SELECT date_trunc('week', start_date_local)::DATE as week_start,
               SUM(distance) as distance, SUM(moving_time) as moving_time, COUNT(*) as runs
        FROM dim_activity
        WHERE athlete_id = ? AND type = 'Run'
          AND start_date_local >= date_trunc('week', current_date) - interval '11 weeks'
        GROUP BY 1
        """, [user_id]).fetchall()
        by_week = {row[0]: row for row in weekly_rows}
        this_week = date.today() - timedelta(days=date.today().weekday())
        weekly_volume = []
        for weeks_back in range(11, -1, -1):
            week_start = this_week - timedelta(weeks=weeks_back)
            row = by_week.get(week_start)
            weekly_volume.append({
                "week_start": str(week_start),
                "distance": row[1] if row else 0,
                "moving_time": row[2] if row else 0,
                "runs": row[3] if row else 0,
            })

        return {
            "hr_zones": [
                {
                    "zone": row[0],
                    "time_seconds": row[1],
                    "percentage": round((row[1] / total_hr_secs) * 100, 1) if total_hr_secs > 0 else 0.0,
                }
                for row in hr_data
            ],
            "load_dist": [
                {"type": row[0], "distance": row[1], "moving_time": row[2], "activities": row[3]}
                for row in load_data
            ],
            "weekly_volume": weekly_volume,
            "hr_max": hr_max,
            "hr_rest": hr_rest,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        con.close()


RACE_DISTANCES_M = {
    "5k": 5000.0, "5 mile": 8046.72, "10k": 10000.0, "10 mile": 16093.44,
    "half marathon": 21097.5, "marathon": 42195.0,
}
STANDARD_DISTANCES = [("5K", 5000.0), ("10K", 10000.0), ("Half marathon", 21097.5), ("Marathon", 42195.0)]


def recent_best_efforts(con, athlete_id, days=180):
    """The fastest effort per standard distance in the last days, as dicts
    with distance_m, label, seconds, date, activity_id and name."""
    try:
        rows = con.execute("""
            SELECT be.distance_m, be.seconds, da.start_date_local::DATE, da.activity_id, da.name
            FROM best_efforts be JOIN dim_activity da ON da.activity_id = be.activity_id
            WHERE da.athlete_id = ? AND da.start_date_local >= ?
            QUALIFY row_number() OVER (PARTITION BY be.distance_m ORDER BY be.seconds) = 1
            ORDER BY be.distance_m
        """, [athlete_id, date.today() - timedelta(days=days)]).fetchall()
    except Exception:
        rows = []  # no best_efforts table until the first sync after this feature
    labels = {distance: label for label, distance in BEST_EFFORT_DISTANCES_M.items()}
    return [
        {"distance_m": distance, "label": labels.get(distance, f"{distance:.0f} m"), "seconds": round(seconds),
         "date": str(day), "activity_id": activity_id, "name": name}
        for distance, seconds, day, activity_id, name in rows
    ]


def predict_from_efforts(efforts, target_m):
    """Riegel prediction for target_m from the best-suited effort: those
    between a quarter of the distance and the distance itself, else all.
    Returns (seconds, effort)."""
    candidates = [effort for effort in efforts if target_m / 4 <= effort["distance_m"] <= target_m] or efforts
    best = min(candidates, key=lambda effort: riegel_prediction(effort["seconds"], effort["distance_m"], target_m))
    return round(riegel_prediction(best["seconds"], best["distance_m"], target_m)), best


@router.get("/api/predictions/{user_id}")
def get_predictions(user_id: int, current_user: User = Depends(get_current_user)):
    """Race time predictions from the athlete's fastest real efforts in the
    last six months (Riegel's formula), for the standard distances and each
    upcoming race. The VO2 max (VDOT) prediction rides along as the aerobic
    ceiling: what the engine could do with race-specific fitness.

    For a target distance the candidates are best efforts between a quarter
    of the distance and the distance itself; the fastest extrapolation wins,
    since a runner who never races only has training efforts to go on.
    """
    # user_id here is the warehouse athlete id (see /status/{user_id} above).
    if user_id != current_user.athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's predictions")

    con = get_db_connection()
    try:
        efforts = recent_best_efforts(con, user_id)
    finally:
        con.close()
    if not efforts:
        raise HTTPException(status_code=404, detail="Predictions need runs with pace data. Sync Strava.")

    vo2 = calculate_vo2max(user_id, hr_max=current_user.max_hr, hr_rest=current_user.resting_hr)
    vdot = vo2["latest_vo2_max"] if vo2 and vo2.get("latest_vo2_max") else None

    def row_for(label, distance_m):
        seconds, source = predict_from_efforts(efforts, distance_m)
        return {
            "label": label, "distance_m": distance_m, "seconds": seconds,
            "from": {"label": source["label"], "seconds": source["seconds"], "date": source["date"], "name": source["name"]},
            "ceiling_seconds": round(race_time_for_vdot(vdot, distance_m)) if vdot else None,
        }

    db = SessionLocal()
    try:
        races = db.query(Race).filter(Race.user_id == current_user.id, Race.race_date >= date.today()).order_by(Race.race_date).all()
        race_rows = []
        for race in races:
            distance = RACE_DISTANCES_M.get((race.race_length or "").lower())
            prediction = row_for(race.name or race.race_length, distance) if distance else {}
            race_rows.append({
                "id": race.id, "name": race.name, "race_length": race.race_length, "race_date": str(race.race_date),
                "goal_time": race.goal_time, "goal_seconds": parse_clock(race.goal_time), **prediction,
            })
    finally:
        db.close()

    return {
        "vdot": vdot,
        "efforts": efforts,
        "predictions": [row_for(label, distance) for label, distance in STANDARD_DISTANCES],
        "races": race_rows,
    }


@router.get("/api/efficiency/{user_id}")
def get_efficiency(user_id: int, days: int = 180, current_user: User = Depends(get_current_user)):
    """Aerobic efficiency on easy runs: meters per minute per heartbeat, per
    run, with a rolling median. Easy means 60-82% of max heart rate and at
    least 20 minutes. A rising line at the same heart rate is real fitness."""
    # user_id here is the warehouse athlete id (see /status/{user_id} above).
    if user_id != current_user.athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's efficiency")
    days = max(30, min(days, 730))
    con = get_db_connection()
    try:
        hr_max = float(current_user.max_hr or detected_max_hr(con, user_id))
        hr_rest = resolve_resting_hr(con, current_user)
        rows = con.execute("""
            SELECT da.start_date_local::DATE AS day, da.activity_id, da.name, ae.efficiency_factor,
                   da.average_heartrate, da.average_speed, da.moving_time
            FROM dim_activity da
            JOIN activity_effectiveness ae ON ae.activity_id = da.activity_id
            WHERE da.athlete_id = ? AND da.type = 'Run' AND da.moving_time >= 1200
              AND ae.efficiency_factor > 0 AND ae.intensity_factor BETWEEN 0.60 AND 0.82
              AND da.start_date_local >= current_date - to_days(?)
            ORDER BY da.start_date_local
        """, [user_id, days]).fetchall()
    finally:
        con.close()

    points = []
    window = []
    for day, activity_id, name, ef, avg_hr, avg_speed, moving_time in rows:
        window.append(ef)
        window = window[-7:]
        points.append({
            "date": str(day), "activity_id": activity_id, "name": name,
            "ef": round(ef, 3), "trend": round(sorted(window)[len(window) // 2], 3),
            "avg_hr": round(avg_hr) if avg_hr else None, "avg_speed": avg_speed, "moving_time": moving_time,
        })
    return {
        "points": points,
        "hr_max": hr_max,
        "hr_rest": hr_rest,
        # Middle of zone 2, the heart rate the UI converts efficiency to a pace at.
        "reference_hr": round(hr_rest + 0.65 * (hr_max - hr_rest)),
    }


@router.get("/api/records/{user_id}")
def get_records(user_id: int, current_user: User = Depends(get_current_user)):
    """Personal records: the fastest mile, 5K, 10K and half marathon inside
    any run, all time and this year, from the best_efforts table."""
    # user_id here is the warehouse athlete id (see /status/{user_id} above).
    if user_id != current_user.athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's records")
    labels = {distance: label for label, distance in BEST_EFFORT_DISTANCES_M.items()}

    def rows_to_records(rows):
        return [
            {"distance_m": distance, "label": labels.get(distance, f"{distance:.0f} m"), "seconds": round(seconds),
             "date": str(day), "activity_id": activity_id, "name": name}
            for distance, seconds, day, activity_id, name in rows
        ]

    con = get_db_connection()
    try:
        query = """
            SELECT be.distance_m, be.seconds, da.start_date_local::DATE, da.activity_id, da.name
            FROM best_efforts be JOIN dim_activity da ON da.activity_id = be.activity_id
            WHERE da.athlete_id = ? {extra}
            QUALIFY row_number() OVER (PARTITION BY be.distance_m ORDER BY be.seconds) = 1
            ORDER BY be.distance_m
        """
        all_time = con.execute(query.format(extra=""), [user_id]).fetchall()
        this_year = con.execute(
            query.format(extra="AND date_part('year', da.start_date_local) = date_part('year', current_date)"), [user_id]
        ).fetchall()
    finally:
        con.close()
    return {"all_time": rows_to_records(all_time), "this_year": rows_to_records(this_year)}
