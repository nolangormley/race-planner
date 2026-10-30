"""Training plan entries."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
import logging

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User, TrainingPlan, TrainingPlanEntry
from src.api.schemas import PlanEntryCreate, PlanEntryUpdate, BulkEntriesCreate

logger = logging.getLogger("race_planner.api")
router = APIRouter()


def _plan_to_dict(plan):
    return {
        "id": plan.id,
        "user_id": plan.user_id,
        "name": plan.name,
        "race_id": plan.race_id,
        "created_at": str(plan.created_at),
        "updated_at": str(plan.updated_at),
    }


def _entry_to_dict(e):
    return {
        "id": e.id,
        "plan_id": e.plan_id,
        "date": str(e.date),
        "workout_type": e.workout_type,
        "training_focus": e.training_focus,
        "approximate_distance": e.approximate_distance,
        "description": e.description,
        "is_completed": e.is_completed,
        "strava_activity_id": e.strava_activity_id,
    }


@router.get("/api/training-plan/{user_id}")
def get_training_plan(user_id: int, current_user: User = Depends(get_current_user)):
    """Get the active training plan for a user including all entries and chat history."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this training plan")
    db = SessionLocal()
    try:
        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()
        if not plan:
            return {"plan": None, "entries": []}
        entries = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).order_by(TrainingPlanEntry.date).all()
        return {
            "plan": _plan_to_dict(plan),
            "entries": [_entry_to_dict(e) for e in entries],
        }
    finally:
        db.close()


@router.post("/api/training-plan/{user_id}")
def create_or_replace_training_plan(user_id: int, body: BulkEntriesCreate, current_user: User = Depends(get_current_user)):
    """Replace the user's training plan with a new set of entries (AI or manual bulk-add)."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this training plan")
    db = SessionLocal()
    try:
        old_plans = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).all()
        for p in old_plans:
            db.delete(p)
        db.commit()

        plan = TrainingPlan(user_id=user_id, name=body.name or "My Training Plan", race_id=body.race_id)
        db.add(plan)
        db.commit()
        db.refresh(plan)

        for entry_data in body.entries:
            entry = TrainingPlanEntry(
                plan_id=plan.id,
                date=datetime.strptime(entry_data.date, '%Y-%m-%d').date(),
                workout_type=entry_data.workout_type,
                training_focus=entry_data.training_focus,
                approximate_distance=entry_data.approximate_distance,
                description=entry_data.description,
                is_completed=entry_data.is_completed or False,
                strava_activity_id=entry_data.strava_activity_id,
            )
            db.add(entry)
        db.commit()
        return {"message": "Plan created", "plan_id": plan.id, "entry_count": len(body.entries)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.post("/api/training-plan/{user_id}/entry")
def add_plan_entry(user_id: int, entry_data: PlanEntryCreate, current_user: User = Depends(get_current_user)):
    """Add a single entry (day) to the user's plan."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this training plan")
    db = SessionLocal()
    try:
        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()
        if not plan:
            plan = TrainingPlan(user_id=user_id, name="My Training Plan")
            db.add(plan)
            db.commit()
            db.refresh(plan)
        entry = TrainingPlanEntry(
            plan_id=plan.id,
            date=datetime.strptime(entry_data.date, '%Y-%m-%d').date(),
            workout_type=entry_data.workout_type,
            training_focus=entry_data.training_focus,
            approximate_distance=entry_data.approximate_distance,
            description=entry_data.description,
            is_completed=entry_data.is_completed or False,
            strava_activity_id=entry_data.strava_activity_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return _entry_to_dict(entry)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.put("/api/training-plan/entry/{entry_id}")
def update_plan_entry(entry_id: int, update: PlanEntryUpdate, current_user: User = Depends(get_current_user)):
    """Update a specific entry."""
    db = SessionLocal()
    try:
        # Ownership check via TrainingPlanEntry -> TrainingPlan.user_id. 404
        # (not 403) for a mismatch too, so this doesn't confirm the existence
        # of another user's entry ids.
        entry = (
            db.query(TrainingPlanEntry)
            .join(TrainingPlan, TrainingPlanEntry.plan_id == TrainingPlan.id)
            .filter(TrainingPlanEntry.id == entry_id, TrainingPlan.user_id == current_user.id)
            .first()
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        if update.workout_type is not None:
            entry.workout_type = update.workout_type
        if update.training_focus is not None:
            entry.training_focus = update.training_focus
        if update.approximate_distance is not None:
            entry.approximate_distance = update.approximate_distance
        if update.description is not None:
            entry.description = update.description
        if update.is_completed is not None:
            entry.is_completed = update.is_completed
        # Sent explicitly as null to detach an activity, so test for the
        # field being present rather than non-null.
        if 'strava_activity_id' in update.model_fields_set:
            entry.strava_activity_id = update.strava_activity_id
        db.commit()
        return _entry_to_dict(entry)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/api/training-plan/entry/{entry_id}")
def delete_plan_entry(entry_id: int, current_user: User = Depends(get_current_user)):
    """Delete a specific entry."""
    db = SessionLocal()
    try:
        entry = (
            db.query(TrainingPlanEntry)
            .join(TrainingPlan, TrainingPlanEntry.plan_id == TrainingPlan.id)
            .filter(TrainingPlanEntry.id == entry_id, TrainingPlan.user_id == current_user.id)
            .first()
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        db.delete(entry)
        db.commit()
        return {"message": "Entry deleted"}
    finally:
        db.close()
