"""Profile updates and the heart rate profile."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
import logging

from src.pipeline import recompute_metrics
from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User
from src.api.deps import get_db_connection, DEFAULT_REST_HR
from src.api.schemas import ProfileUpdate
from src.api.services.metrics import detected_max_hr, coros_resting_hr, resolve_resting_hr
from src.api.services.sync import registry as sync_registry, SyncAlreadyRunning

logger = logging.getLogger("race_planner.api")
router = APIRouter()


@router.get("/api/hr-profile/{user_id}")
def hr_profile(user_id: int, current_user: User = Depends(get_current_user)):
    """The resting and max heart rate in use, and where each comes from."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this profile")
    con = get_db_connection()
    try:
        detected = detected_max_hr(con, current_user.athlete_id)
        from_coros = coros_resting_hr(con, current_user.athlete_id)
    finally:
        con.close()
    return {
        "resting_hr": current_user.resting_hr,
        "max_hr": current_user.max_hr,
        "default_resting_hr": DEFAULT_REST_HR,
        "coros_resting_hr": from_coros,
        "detected_max_hr": detected,
    }


@router.post("/profile/{user_id}")
def update_profile(user_id: int, profile: ProfileUpdate, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this profile")
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="User not found")
    try:
        # Check email uniqueness
        if profile.email != user.email:
            existing = db.query(User).filter(User.email == profile.email).first()
            if existing:
                raise HTTPException(status_code=400, detail="Email already in use by another account.")
        user.name = profile.name
        user.email = profile.email
        if profile.date_of_birth:
            user.date_of_birth = datetime.strptime(profile.date_of_birth, '%Y-%m-%d').date()
        if profile.gender:
            user.gender = profile.gender
        if profile.height is not None:
            user.height = profile.height
        if profile.weight is not None:
            user.weight = profile.weight

        # Null is a real value here: it means "back to the detected/default".
        hr_changed = False
        for field in ("resting_hr", "max_hr"):
            if field in profile.model_fields_set and getattr(profile, field) != getattr(user, field):
                setattr(user, field, getattr(profile, field))
                hr_changed = True
        db.commit()
        max_hr = user.max_hr
        con = get_db_connection()
        try:
            resting_hr = resolve_resting_hr(con, user)
        finally:
            con.close()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()

    # Every stored zone, TRIMP and VO2 estimate depends on these two numbers,
    # so a change recomputes the warehouse in the background. The UI shows it
    # with the same progress panel as a sync.
    recompute_started = False
    if hr_changed:
        try:
            sync_registry.start(
                user_id,
                run_sync=lambda progress: recompute_metrics(athlete_id, progress=progress, resting_hr=resting_hr, max_hr=max_hr),
                on_success=lambda new_token: None,
                kind="recompute",
            )
            recompute_started = True
        except SyncAlreadyRunning:
            pass
    return {"message": "Profile updated", "recompute_started": recompute_started}
