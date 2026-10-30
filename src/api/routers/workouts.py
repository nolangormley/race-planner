"""The workout library (dim_workouts in the warehouse)."""
from fastapi import APIRouter, HTTPException, Depends
import logging

from src.api.auth_routes import get_current_user
from src.api.database import User
from src.api.schemas import WorkoutCreate
from src.api.deps import get_db_connection

logger = logging.getLogger("race_planner.api")
router = APIRouter()


@router.get("/api/workouts")
def get_workouts(current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        workouts = con.execute("SELECT * FROM dim_workouts ORDER BY workout_id DESC").fetchall()
        columns = [desc[0] for desc in con.description]
        return [dict(zip(columns, row)) for row in workouts]
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@router.post("/api/workouts")
def create_workout(workout: WorkoutCreate, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        # Auto-incrementing workout_id via MAX + 1
        max_id_row = con.execute("SELECT MAX(workout_id) FROM dim_workouts").fetchone()
        next_id = (max_id_row[0] or 0) + 1 if max_id_row and max_id_row[0] is not None else 1
        
        con.execute(
            "INSERT INTO dim_workouts (workout_id, name, description, category, tags, url, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [next_id, workout.name, workout.description, workout.category, workout.tags, workout.url, workout.source]
        )
        return {"message": "Workout created successfully", "workout_id": next_id}
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@router.put("/api/workouts/{workout_id}")
def update_workout(workout_id: int, workout: WorkoutCreate, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        con.execute(
            "UPDATE dim_workouts SET name=?, description=?, category=?, tags=?, url=?, source=? WHERE workout_id=?",
            [workout.name, workout.description, workout.category, workout.tags, workout.url, workout.source, workout_id]
        )
        return {"message": "Workout updated successfully"}
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@router.delete("/api/workouts/{workout_id}")
def delete_workout(workout_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        con.execute("DELETE FROM dim_workouts WHERE workout_id=?", [workout_id])
        return {"message": "Workout deleted successfully"}
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()
