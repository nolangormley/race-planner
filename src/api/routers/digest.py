"""Weekly digest email settings, preview and send-now."""
from fastapi import APIRouter, HTTPException, Depends
import logging

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User
from src.api.schemas import DigestSettings
from src.api.services.digest import build_digest, send_digest
from src.api.services.email import smtp_configured

logger = logging.getLogger("race_planner.api")
router = APIRouter()


def own_user(db, user_id, current_user):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return db.query(User).filter(User.id == user_id).first()


@router.get("/api/digest/{user_id}")
def digest_settings(user_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        user = own_user(db, user_id, current_user)
        return {
            "enabled": bool(user.weekly_digest),
            "last_sent_at": user.digest_sent_at.isoformat() if user.digest_sent_at else None,
            "smtp_configured": smtp_configured(),
        }
    finally:
        db.close()


@router.post("/api/digest/{user_id}")
def update_digest_settings(user_id: int, body: DigestSettings, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        user = own_user(db, user_id, current_user)
        user.weekly_digest = body.enabled
        db.commit()
        return {"enabled": body.enabled}
    finally:
        db.close()


@router.get("/api/digest/{user_id}/preview")
def preview_digest(user_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        user = own_user(db, user_id, current_user)
        subject, body = build_digest(db, user)
        return {"subject": subject, "body": body}
    finally:
        db.close()


@router.post("/api/digest/{user_id}/send")
def send_digest_now(user_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        user = own_user(db, user_id, current_user)
        try:
            subject = send_digest(db, user)
        except Exception as error:
            db.rollback()
            logger.exception("Digest send failed")
            raise HTTPException(status_code=502, detail=f"Could not send the digest: {error}")
        return {"sent": True, "delivered": smtp_configured(), "to": user.email, "subject": subject}
    finally:
        db.close()
