"""Profile updates."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
import logging

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User
from src.api.schemas import ProfileUpdate

logger = logging.getLogger("race_planner.api")
router = APIRouter()


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
        db.commit()
        return {"message": "Profile updated"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
