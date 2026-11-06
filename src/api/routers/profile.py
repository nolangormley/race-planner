"""Profile updates, the heart rate profile and account deletion."""
from fastapi import APIRouter, HTTPException, Depends, Request
from datetime import datetime
import logging

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User
from src.api.deps import get_db_connection, DEFAULT_MAX_HR, DEFAULT_REST_HR
from src.api.schemas import AccountDelete, ProfileUpdate
from src.api.services.account import WrongPassword, delete_account
from src.api.services.metrics import recorded_max_hr
from src.api.services.sync import queue_recompute, registry as sync_registry

logger = logging.getLogger("race_planner.api")
router = APIRouter()


@router.get("/api/hr-profile/{user_id}")
def hr_profile(user_id: int, current_user: User = Depends(get_current_user)):
    """The resting and max heart rate in use, and where each comes from.
    detected_max_hr is null when no activity recorded a heart rate."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this profile")
    con = get_db_connection()
    try:
        detected = recorded_max_hr(con, current_user.athlete_id)
    finally:
        con.close()
    return {
        "resting_hr": current_user.resting_hr,
        "max_hr": current_user.max_hr,
        "default_resting_hr": DEFAULT_REST_HR,
        "coros_resting_hr": current_user.coros_resting_hr,
        "detected_max_hr": detected,
        "default_max_hr": DEFAULT_MAX_HR,
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
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()

    # Every stored zone, TRIMP and VO2 estimate depends on these two numbers,
    # so a change recomputes the warehouse in the background, after any job
    # already running. The UI shows it with the same progress panel as a sync.
    if hr_changed:
        queue_recompute(user_id)
    return {"message": "Profile updated", "recompute_started": hr_changed}


@router.delete("/api/account")
def delete_own_account(body: AccountDelete, request: Request, current_user: User = Depends(get_current_user)):
    """Deletes the signed-in user's account and everything stored for them
    (services/account.py), then ends the session. Refused while a sync or
    recompute runs for the user, because that job would write rows back."""
    job = sync_registry.snapshot(current_user.id)
    if job and job["status"] == "running":
        raise HTTPException(status_code=409, detail="A sync is running. Wait for it to finish, then try again.")
    db, con = SessionLocal(), get_db_connection()
    try:
        user = db.query(User).filter(User.id == current_user.id).first()
        try:
            delete_account(db, con, user, body.password)
        except WrongPassword:
            raise HTTPException(status_code=400, detail="The password is incorrect.")
    finally:
        con.close()
        db.close()
    request.session.clear()
    return {"deleted": True}
