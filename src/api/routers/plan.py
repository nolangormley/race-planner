"""Training plan entries, projection and the plan builder."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, date, timedelta
import logging

from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User, Race, TrainingPlan, TrainingPlanEntry
from src.api.deps import get_db_connection
from src.api.schemas import PlanEntryCreate, PlanEntryUpdate, BulkEntriesCreate, PlanBuildRequest
from src.api.services.metrics import calculate_training_status_logic
from src.metrics import planned_distance_km, estimate_entry_load, project_training_load
from src.plan_builder import build_plan, summarize, RACE_PROFILES

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


@router.get("/api/projection/{user_id}")
def get_projection(user_id: int, current_user: User = Depends(get_current_user)):
    """Fitness, fatigue and form projected day by day along the saved plan.

    Each planned session's load is the athlete's median TRIMP per kilometre
    of running over the last 90 days, scaled by workout type (see
    src.metrics.estimate_entry_load). Days without an entry count as rest.
    The projection runs to the last planned day or the next race, whichever
    is later, at least two weeks and at most 120 days ahead.
    """
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this projection")
    athlete_id = current_user.athlete_id

    db = SessionLocal()
    try:
        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()
        entries = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).all() if plan else []
        races = db.query(Race).filter(Race.user_id == user_id, Race.race_date >= date.today()).order_by(Race.race_date).all()
        race_rows = [{"date": str(race.race_date), "name": race.name or race.race_length, "race_length": race.race_length} for race in races]
    finally:
        db.close()

    today = date.today()
    future = [entry for entry in entries if entry.date > today]
    if not future:
        return {"has_plan": False, "days": [], "races": race_rows}

    status = calculate_training_status_logic(athlete_id, hr_max=current_user.max_hr, hr_rest=current_user.resting_hr)
    if not status:
        return {"has_plan": False, "days": [], "races": race_rows}

    con = get_db_connection()
    try:
        row = con.execute("""
            SELECT median(ae.trimp_banister / (da.distance / 1000.0))
            FROM dim_activity da JOIN activity_effectiveness ae ON ae.activity_id = da.activity_id
            WHERE da.athlete_id = ? AND da.type = 'Run' AND da.distance >= 1000
              AND da.start_date_local >= current_date - interval '90 days'
        """, [athlete_id]).fetchone()
    finally:
        con.close()
    trimp_per_km = float(row[0]) if row and row[0] else 12.0

    last_day = max(entry.date for entry in future)
    if races:
        last_day = max(last_day, races[0].race_date)
    horizon = min(max((last_day - today).days, 14), 120)

    by_day = {}
    for entry in future:
        by_day.setdefault(entry.date, []).append(entry)
    days = [today + timedelta(days=offset) for offset in range(1, horizon + 1)]
    loads = []
    types = []
    for day in days:
        sessions = by_day.get(day, [])
        loads.append(sum(estimate_entry_load(entry.workout_type, planned_distance_km(entry.approximate_distance), trimp_per_km) for entry in sessions))
        types.append(", ".join(entry.workout_type or "Workout" for entry in sessions) or None)

    projected = project_training_load(status["fitness_ctl"], status["fatigue_atl"], loads)
    return {
        "has_plan": True,
        "trimp_per_km": round(trimp_per_km, 2),
        "days": [
            {"date": str(day), "workout_type": kind, "load": round(load, 1), "ctl": round(ctl, 2), "atl": round(atl, 2), "tsb": round(tsb, 2)}
            for day, kind, load, (ctl, atl, tsb) in zip(days, types, loads, projected)
        ],
        "races": race_rows,
    }


def current_weekly_run_meters(athlete_id):
    """Average weekly run distance over the last four complete weeks, or None."""
    con = get_db_connection()
    try:
        rows = con.execute("""
            SELECT date_trunc('week', start_date_local)::DATE AS week, SUM(distance)
            FROM dim_activity
            WHERE athlete_id = ? AND type = 'Run'
              AND start_date_local >= date_trunc('week', current_date) - interval '4 weeks'
              AND start_date_local < date_trunc('week', current_date)
            GROUP BY 1
        """, [athlete_id]).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    return sum(row[1] for row in rows) / 4


@router.post("/api/training-plan/{user_id}/build")
def build_training_plan(user_id: int, body: PlanBuildRequest, current_user: User = Depends(get_current_user)):
    """Fills the calendar from tomorrow to a race with a generated plan (see
    src.plan_builder). Weekly volume starts from the last four weeks of
    running unless given. With replace_future, planned days after today are
    replaced; otherwise only days without an entry are filled."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this training plan")
    today = date.today()
    db = SessionLocal()
    try:
        race = db.query(Race).filter(Race.id == body.race_id, Race.user_id == user_id).first()
        if not race:
            raise HTTPException(status_code=404, detail="Race not found")
        if race.race_date <= today:
            raise HTTPException(status_code=400, detail="That race is in the past.")
        if (race.race_length or "").lower() not in RACE_PROFILES:
            raise HTTPException(status_code=400, detail="Plans cover 5K, 5 mile, 10K, 10 mile, half marathon and marathon.")

        weekly = body.weekly_distance_m
        if not weekly:
            weekly = current_weekly_run_meters(current_user.athlete_id)
        weekly = weekly or 20000.0

        entries = build_plan(
            today + timedelta(days=1), race.race_date, race.race_length, weekly,
            metric=body.metric, days_per_week=body.days_per_week, long_run_day=body.long_run_day,
            quality_sessions=body.quality_sessions, race_label=race.name or race.race_length,
        )

        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()
        if not plan:
            plan = TrainingPlan(user_id=user_id, name=f"{race.name or race.race_length} plan", race_id=race.id)
            db.add(plan)
            db.commit()
            db.refresh(plan)
        else:
            plan.race_id = race.id
            plan.name = f"{race.name or race.race_length} plan"

        future = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id, TrainingPlanEntry.date > today)
        if body.replace_future:
            future.delete(synchronize_session=False)
            taken = set()
        else:
            taken = {entry.date for entry in future.all()}

        created = 0
        for item in entries:
            day = date.fromisoformat(item["date"])
            if day in taken:
                continue
            db.add(TrainingPlanEntry(
                plan_id=plan.id, date=day, workout_type=item["workout_type"], training_focus=item["training_focus"],
                approximate_distance=item["approximate_distance"], description=item["description"],
            ))
            created += 1
        db.commit()
        all_entries = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).order_by(TrainingPlanEntry.date).all()
        return {
            "created": created,
            "weekly_distance_m": weekly,
            "summary": summarize(entries, body.metric),
            "entries": [_entry_to_dict(entry) for entry in all_entries],
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Plan build failed")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
