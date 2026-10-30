"""The demo user's rows: the account, a half marathon about ten weeks out
and a plan built by src.plan_builder from the fake history's recent volume.

scripts/demo.py imports this only after it has pointed the environment at
the fake services, because the src modules read their settings when they
are imported. tests/test_demo.py runs it against SQLite.
"""
from datetime import date, timedelta

from src.api.auth_routes import hash_password
from src.api.database import Race, TrainingPlan, TrainingPlanEntry, User
from src.plan_builder import build_plan

EMAIL = "demo@example.com"
PASSWORD = "demo1234"
RACE_NAME = "Demo Half"
RACE_LENGTH = "half marathon"
RACE_GOAL = "1:45:00"
RACE_WEEKS = 10


def remove_demo_user(db):
    """Deletes the demo user with their plans and races, and nothing else."""
    user = db.query(User).filter(User.email == EMAIL).first()
    if not user:
        return
    plan_ids = [plan.id for plan in db.query(TrainingPlan).filter(TrainingPlan.user_id == user.id)]
    if plan_ids:
        db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id.in_(plan_ids)).delete(synchronize_session=False)
        db.query(TrainingPlan).filter(TrainingPlan.id.in_(plan_ids)).delete(synchronize_session=False)
    db.query(Race).filter(Race.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()


def weekly_meters(runs, today):
    """Average weekly run distance over the four full weeks before this one,
    the volume the plan starts from (as the Build plan button does)."""
    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(weeks=4)
    total = sum(run["distance"] for run in runs if first_monday <= date.fromisoformat(run["start_date_local"][:10]) < this_monday)
    return total / 4


def seed_demo(db, runs, athlete_id, today=None):
    """Replaces any earlier demo rows with a user linked to the fake Strava
    athlete, a race on the Sunday about ten weeks out and a plan from this
    Monday to race day. Returns the new user."""
    today = today or date.today()
    remove_demo_user(db)
    monday = today - timedelta(days=today.weekday())
    race_day = monday + timedelta(weeks=RACE_WEEKS, days=6)

    user = User(
        email=EMAIL, password=hash_password(PASSWORD), name="Demo Runner",
        date_of_birth=date(1992, 4, 12), gender="Female", height=1.68, weight=60.0,
        race_length=RACE_LENGTH, race_date=race_day, race_goal_time=RACE_GOAL,
        strava_athlete_id=athlete_id, strava_refresh_token="fake-refresh-token",
    )
    db.add(user)
    db.flush()
    race = Race(user_id=user.id, name=RACE_NAME, race_length=RACE_LENGTH, race_date=race_day, goal_time=RACE_GOAL)
    db.add(race)
    db.flush()
    plan = TrainingPlan(user_id=user.id, name=f"{RACE_NAME} plan", race_id=race.id)
    db.add(plan)
    db.flush()
    for item in build_plan(monday, race_day, RACE_LENGTH, weekly_meters(runs, today), race_label=RACE_NAME):
        db.add(TrainingPlanEntry(
            plan_id=plan.id, date=date.fromisoformat(item["date"]), workout_type=item["workout_type"],
            training_focus=item["training_focus"], approximate_distance=item["approximate_distance"], description=item["description"],
        ))
    db.commit()
    return user
