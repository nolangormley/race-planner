"""The user's races, and the race-courses catalog that a new race can be picked from."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
import logging

from src.pacing import LENGTH_TOLERANCE
from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User, Race
from src.api.routers.analytics import RACE_DISTANCES_M
from src.api.routers.pacing import course_summary
from src.api.schemas import RaceCreate
from src.api.services import course_library as library

logger = logging.getLogger("race_planner.api")
router = APIRouter()


def race_length_for(distance_m):
    """The race length this app plans for whose distance is within LENGTH_TOLERANCE of distance_m, or None."""
    return next((length for length, metres in RACE_DISTANCES_M.items()
                 if distance_m and abs(distance_m - metres) <= LENGTH_TOLERANCE * metres), None)


@router.get("/api/race-catalog")
def race_catalog(current_user: User = Depends(get_current_user)):
    """Known races from the race-courses dataset at a distance this app plans for, biggest first.
    has_course says whether the dataset has the event's course file."""
    try:
        entries = library.entries()
    except library.LibraryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    races = []
    for entry in sorted(entries, key=lambda entry: -(entry.get("finishers") or 0)):
        length = race_length_for(entry.get("distance_m"))
        if length:
            races.append({
                "race": entry["race"], "event": entry["event"], "name": entry.get("name", ""),
                "event_name": entry.get("event_name", ""), "city": entry.get("city", ""),
                "state": entry.get("state", ""), "month": entry.get("month"), "race_length": length,
                "has_course": library.has_course(entry),
            })
    return {"races": races}


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
                "goal_time": r.goal_time,
                "has_course": bool(r.course_gpx),
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
        course = library.attach(new_race, race_data.course.race, race_data.course.event) if race_data.course else None
        db.add(new_race)
        db.commit()
        db.refresh(new_race)
        return {"message": "Race created", "id": new_race.id, "course": course_summary(course)}
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
