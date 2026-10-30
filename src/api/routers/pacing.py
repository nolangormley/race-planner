"""Race-day pacing: a course file per race and split targets for a goal."""
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
import logging

from src.pacing import (
    METERS_PER_MILE, STRATEGIES, course_profile, format_clock, parse_clock, parse_gpx_course, plan_splits,
)
from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User, Race
from src.api.deps import get_db_connection
from src.api.routers.analytics import RACE_DISTANCES_M, recent_best_efforts, predict_from_efforts

logger = logging.getLogger("race_planner.api")
router = APIRouter()

MAX_GPX_BYTES = 5 * 1024 * 1024
UNITS_M = {"mi": METERS_PER_MILE, "km": 1000.0}


def own_race(db, race_id, current_user):
    race = db.query(Race).filter(Race.id == race_id).first()
    # 404 for both "doesn't exist" and "isn't yours", like the races router.
    if not race or race.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Race not found")
    return race


def race_distance(race):
    distance = RACE_DISTANCES_M.get((race.race_length or "").lower())
    if not distance:
        raise HTTPException(status_code=400, detail="Pacing plans cover 5K, 5 mile, 10K, 10 mile, half marathon and marathon.")
    return distance


def course_of(race):
    """The parsed course, or None without a file (or with one that no
    longer parses)."""
    if not race.course_gpx:
        return None
    try:
        return parse_gpx_course(race.course_gpx)
    except ValueError:
        return None


def course_summary(course):
    if not course:
        return None
    return {"length_m": course["length_m"], "ascent_m": course["ascent_m"], "descent_m": course["descent_m"]}


def predicted_goal(user, distance_m):
    """Riegel prediction from recent best efforts, or None without runs."""
    if not user.strava_athlete_id:
        return None
    con = get_db_connection()
    try:
        efforts = recent_best_efforts(con, user.strava_athlete_id)
    finally:
        con.close()
    if not efforts:
        return None
    seconds, _ = predict_from_efforts(efforts, distance_m)
    return seconds


@router.post("/api/races/{race_id}/course")
def upload_course(race_id: int, file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        race = own_race(db, race_id, current_user)
        data = file.file.read(MAX_GPX_BYTES + 1)
        if len(data) > MAX_GPX_BYTES:
            raise HTTPException(status_code=413, detail="Course files up to 5 MB.")
        text = data.decode("utf-8", errors="replace")
        try:
            course = parse_gpx_course(text)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        race.course_gpx = text
        db.commit()
    finally:
        db.close()
    return {"course": course_summary(course)}


@router.delete("/api/races/{race_id}/course")
def remove_course(race_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        race = own_race(db, race_id, current_user)
        race.course_gpx = None
        db.commit()
    finally:
        db.close()
    return {"course": None}


@router.get("/api/races/{race_id}/pacing")
def pacing_plan(race_id: int, goal: str = None, strategy: str = "even", unit: str = "mi", current_user: User = Depends(get_current_user)):
    """Split targets for the race. The goal is the query's, else the race's
    goal time, else the prediction from recent best efforts."""
    if strategy not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"strategy must be one of {', '.join(STRATEGIES)}")
    if unit not in UNITS_M:
        raise HTTPException(status_code=400, detail="unit must be mi or km")
    db = SessionLocal()
    try:
        race = own_race(db, race_id, current_user)
    finally:
        db.close()
    distance = race_distance(race)

    goal_source = "query"
    goal_seconds = parse_clock(goal) if goal else None
    if goal and goal_seconds is None:
        raise HTTPException(status_code=400, detail="Goal time should look like 1:45:00 or 45:00.")
    if goal_seconds is None and race.goal_time:
        goal_seconds, goal_source = parse_clock(race.goal_time), "race"
    if goal_seconds is None:
        goal_seconds, goal_source = predicted_goal(current_user, distance), "prediction"
    if not goal_seconds:
        raise HTTPException(status_code=400, detail="Set a goal time on the race, or type one above.")

    course = course_of(race)
    profile, warnings = course_profile(course, distance) if course else ([], [])
    split_m = UNITS_M[unit]
    return {
        "race": {"id": race.id, "name": race.name, "race_length": race.race_length, "race_date": str(race.race_date), "goal_time": race.goal_time},
        "distance_m": distance,
        "goal_seconds": goal_seconds,
        "goal": format_clock(goal_seconds),
        "goal_source": goal_source,
        "strategy": strategy,
        "unit": unit,
        "split_m": split_m,
        "average_seconds_per_km": goal_seconds / distance * 1000.0,
        "average_seconds_per_mile": goal_seconds / distance * METERS_PER_MILE,
        "splits": plan_splits(distance, goal_seconds, split_m, strategy, profile),
        "profile": profile,
        "course": course_summary(course),
        "warnings": warnings,
    }
