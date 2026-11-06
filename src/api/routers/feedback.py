"""Feedback notes from the top bar's Feedback button; the admin page lists them."""
from fastapi import APIRouter, Depends, HTTPException

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, Feedback, User
from src.api.schemas import FeedbackCreate

router = APIRouter()


@router.post("/api/feedback")
def send_feedback(note: FeedbackCreate, current_user: User = Depends(get_current_user)):
    message = note.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Write something first.")
    db = SessionLocal()
    try:
        db.add(Feedback(user_id=current_user.id, page=note.page or None, message=message, contact_ok=note.contact_ok))
        db.commit()
    finally:
        db.close()
    return {"message": "Feedback sent"}
