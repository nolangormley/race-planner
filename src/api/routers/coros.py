"""Link a COROS Training Hub account, sync it, and read the nightly readings."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import date, datetime, timedelta
import logging

from src.coros import login, CorosAuthError, CorosError
from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User, TrainingPlan, TrainingPlanEntry
from src.api.deps import get_db_connection
from src.api.schemas import CorosLink
from src.api.services.coros_sync import sync_coros, unlink_coros, client_for, recompute_trigger
from src.api.services.metrics import coros_resting_hr
from src.api.services.readiness import energy_for
from src.energy import DEFAULT_DAYS

logger = logging.getLogger("race_planner.api")
router = APIRouter()


def own_user(db, user_id, current_user):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return db.query(User).filter(User.id == user_id).first()


def status_of(user, con):
    linked = bool(user.coros_access_token)
    nights = 0
    if linked:
        nights = con.execute("SELECT COUNT(*) FROM coros_daily WHERE athlete_id = ?", [user.athlete_id]).fetchone()[0]
    return {
        "linked": linked,
        "region": user.coros_region,
        "linked_at": user.coros_linked_at.isoformat() if user.coros_linked_at else None,
        "synced_at": user.coros_synced_at.isoformat() if user.coros_synced_at else None,
        "error": user.coros_error,
        "nights": nights,
        "resting_hr": coros_resting_hr(con, user.athlete_id) if linked else None,
    }


def run_sync(db, user, con):
    try:
        return sync_coros(db, user, client_for(user), con, on_resting_hr_change=recompute_trigger(user))
    except CorosAuthError as error:
        raise HTTPException(status_code=401, detail=str(error))
    except CorosError as error:
        raise HTTPException(status_code=502, detail=str(error))


@router.get("/api/coros/{user_id}")
def coros_status(user_id: int, current_user: User = Depends(get_current_user)):
    db, con = SessionLocal(), get_db_connection()
    try:
        return status_of(own_user(db, user_id, current_user), con)
    finally:
        con.close()
        db.close()


@router.post("/api/coros/{user_id}/link")
def link_coros(user_id: int, body: CorosLink, current_user: User = Depends(get_current_user)):
    """Logs in to COROS, keeps the session token (never the password) and
    pulls the last year of nights."""
    db, con = SessionLocal(), get_db_connection()
    try:
        user = own_user(db, user_id, current_user)
        try:
            session = login(body.email, body.password, body.region)
        except CorosAuthError as error:
            raise HTTPException(status_code=401, detail=str(error))
        except CorosError as error:
            raise HTTPException(status_code=502, detail=str(error))
        user.coros_access_token = session["access_token"]
        user.coros_user_id = session["user_id"]
        user.coros_region = body.region
        user.coros_linked_at = datetime.now()
        user.coros_synced_at = None
        user.coros_error = None
        db.commit()
        summary = run_sync(db, user, con)
        return {"status": status_of(user, con), "sync": summary}
    finally:
        con.close()
        db.close()


@router.post("/api/coros/{user_id}/sync")
def sync_now(user_id: int, current_user: User = Depends(get_current_user)):
    db, con = SessionLocal(), get_db_connection()
    try:
        user = own_user(db, user_id, current_user)
        if not user.coros_access_token:
            raise HTTPException(status_code=400, detail="COROS is not connected.")
        summary = run_sync(db, user, con)
        return {"status": status_of(user, con), "sync": summary}
    finally:
        con.close()
        db.close()


@router.delete("/api/coros/{user_id}")
def unlink(user_id: int, current_user: User = Depends(get_current_user)):
    db, con = SessionLocal(), get_db_connection()
    try:
        user = own_user(db, user_id, current_user)
        unlink_coros(db, user, con, on_resting_hr_change=recompute_trigger(user))
        return status_of(user, con)
    finally:
        con.close()
        db.close()


@router.get("/api/recovery/{user_id}")
def recovery(user_id: int, days: int = 42, current_user: User = Depends(get_current_user)):
    """Nightly HRV, resting heart rate and time in bed for the dashboard."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    empty = {"days": [], "latest": None, "resting_hr_7d": None, "hrv_7d": None, "sleep_7d_min": None}
    athlete_id = current_user.athlete_id
    if not current_user.coros_access_token:
        return empty
    days = max(7, min(days, 365))
    con = get_db_connection()
    try:
        rows = con.execute("""
            SELECT day, rhr, hrv, hrv_baseline, time_in_bed_min, training_load
            FROM coros_daily
            WHERE athlete_id = ? AND day >= ?
              AND (rhr IS NOT NULL OR hrv IS NOT NULL OR time_in_bed_min IS NOT NULL)
            ORDER BY day
        """, [athlete_id, date.today() - timedelta(days=days)]).fetchall()
        week = con.execute(
            "SELECT AVG(rhr), AVG(hrv), AVG(time_in_bed_min) FROM coros_daily WHERE athlete_id = ? AND day >= ?",
            [athlete_id, date.today() - timedelta(days=7)],
        ).fetchone()
    finally:
        con.close()
    nights = [
        {"day": row[0].isoformat(), "rhr": row[1], "hrv": row[2], "hrv_baseline": row[3], "time_in_bed_min": row[4], "training_load": row[5]}
        for row in rows
    ]
    return {
        "days": nights,
        "latest": nights[-1] if nights else None,
        "resting_hr_7d": round(week[0]) if week and week[0] else None,
        "hrv_7d": round(week[1]) if week and week[1] else None,
        "sleep_7d_min": round(week[2]) if week and week[2] else None,
    }


def planned_workout_today(db, user_id, today):
    entry = (
        db.query(TrainingPlanEntry)
        .join(TrainingPlan, TrainingPlan.id == TrainingPlanEntry.plan_id)
        .filter(TrainingPlan.user_id == user_id, TrainingPlanEntry.date == today)
        .first()
    )
    return entry.workout_type if entry else None


@router.get("/api/readiness/{user_id}")
def readiness_today(user_id: int, current_user: User = Depends(get_current_user)):
    """Today's go / normal / easy / rest call from this morning's energy and
    its trend, with the reasons and a hint when a hard session is planned."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    athlete_id = current_user.athlete_id
    if not current_user.coros_access_token:
        return {"available": False}
    db = SessionLocal()
    try:
        planned = planned_workout_today(db, user_id, date.today())
    finally:
        db.close()
    con = get_db_connection()
    try:
        result = energy_for(con, athlete_id, planned_workout=planned)
    finally:
        con.close()
    if not result["available"] or not result["call"]:
        return {"available": False}
    return {
        "available": True,
        **result["call"],
        "morning": result["morning"],
        "current": result["current"],
        "trend": result["trend"],
        "reasons": result["reasons"],
    }


@router.get("/api/energy/{user_id}")
def energy_curve(user_id: int, days: int = DEFAULT_DAYS, current_user: User = Depends(get_current_user)):
    """Energy hour by hour over the last days (1 to 14) with the activities
    that drained it, the nights that charged it and today's call."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    athlete_id = current_user.strava_athlete_id
    if not athlete_id or not current_user.coros_access_token:
        return {"available": False}
    con = get_db_connection()
    try:
        return energy_for(con, athlete_id, days=max(1, min(days, 14)))
    finally:
        con.close()
