"""The user's races."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
import logging

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User, Race
from src.api.schemas import RaceCreate

logger = logging.getLogger("race_planner.api")
router = APIRouter()


@router.get("/api/races/{user_id}")
def get_user_races(user_id: int, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view these races")
    db = SessionLocal()
    try:
        races = db.query(Race).filter(Race.user_id == user_id).order_by(Race.race_date.asc()).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "race_length": r.race_length,
                "race_date": str(r.race_date),
                "goal_time": r.goal_time
            }
            for r in races
        ]
    finally:
        db.close()

@router.post("/api/races/{user_id}")
def create_user_race(user_id: int, race_data: RaceCreate, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to create a race for this user")
    db = SessionLocal()
    try:
        r_date = datetime.strptime(race_data.race_date, '%Y-%m-%d').date()
        new_race = Race(
            user_id=user_id,
            name=race_data.name,
            race_length=race_data.race_length,
            race_date=r_date,
            goal_time=race_data.goal_time
        )
        db.add(new_race)
        db.commit()
        db.refresh(new_race)
        return {"message": "Race created", "id": new_race.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()

@router.delete("/api/races/{race_id}")
def delete_race(race_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        race = db.query(Race).filter(Race.id == race_id).first()
        # 404 for both "doesn't exist" and "isn't yours" so this endpoint
        # doesn't confirm the existence of other users' race ids.
        if not race or race.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Race not found")
        db.delete(race)
        db.commit()
        return {"message": "Race deleted"}
    finally:
        db.close()
