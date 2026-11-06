"""What to work on: the dashboard's rule-based notes (services/insights.py)."""
from fastapi import APIRouter, HTTPException, Depends

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User
from src.api.services.insights import insights_for

router = APIRouter()


@router.get("/api/insights/{user_id}")
def get_insights(user_id: int, unit: str = "mi", current_user: User = Depends(get_current_user)):
    """At most four notes, serious first; unit ("mi" or "km") is for the paces in the text."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if unit not in ("mi", "km"):
        raise HTTPException(status_code=400, detail="unit is mi or km")
    db = SessionLocal()
    try:
        return {"insights": insights_for(db, current_user, unit)}
    finally:
        db.close()
