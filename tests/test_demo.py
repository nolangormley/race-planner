from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from e2e.fake_strava import ATHLETE_ID, synthetic_runs
from scripts.demo_seed import EMAIL, PASSWORD, RACE_GOAL, RACE_LENGTH, seed_demo, weekly_meters
from src.api.auth_routes import hash_password, verify_password
from src.api.database import Base, Race, TrainingPlan, TrainingPlanEntry, User


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, Race.__table__, TrainingPlan.__table__, TrainingPlanEntry.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_seed_builds_the_demo_user_race_and_plan(db):
    today = date.today()
    runs = synthetic_runs(60, span_days=175)
    user = seed_demo(db, runs, ATHLETE_ID, today)

    assert user.email == EMAIL and verify_password(PASSWORD, user.password)
    assert user.strava_athlete_id == ATHLETE_ID and user.strava_refresh_token

    race = db.query(Race).filter(Race.user_id == user.id).one()
    monday = today - timedelta(days=today.weekday())
    assert race.race_date == monday + timedelta(weeks=10, days=6)            # a Sunday about ten weeks out
    assert (race.race_length, race.goal_time) == (RACE_LENGTH, RACE_GOAL)

    plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user.id).one()
    assert plan.race_id == race.id
    entries = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).order_by(TrainingPlanEntry.date).all()
    assert [entry.date for entry in entries] == [monday + timedelta(days=offset) for offset in range((race.race_date - monday).days + 1)]
    assert entries[-1].workout_type == "Race"
    assert sum(entry.workout_type == "Long Run" for entry in entries) >= 9


def test_plan_starts_from_the_last_four_weeks_of_running():
    today = date.today()
    runs = synthetic_runs(60, span_days=175)
    this_monday = today - timedelta(days=today.weekday())
    recent = [run for run in runs if this_monday - timedelta(weeks=4) <= date.fromisoformat(run["start_date_local"][:10]) < this_monday]
    assert recent
    assert weekly_meters(runs, today) == pytest.approx(sum(run["distance"] for run in recent) / 4)


def test_seeding_again_replaces_only_the_demo_rows(db):
    other = User(email="runner@example.com", password=hash_password("their-password"), name="Someone Else")
    db.add(other)
    db.flush()
    db.add(Race(user_id=other.id, name="Their race", race_length="10k", race_date=date.today() + timedelta(days=30)))
    db.commit()

    runs = synthetic_runs(60, span_days=175)
    seed_demo(db, runs, ATHLETE_ID)
    user = seed_demo(db, runs, ATHLETE_ID)

    assert db.query(User).filter(User.email == EMAIL).count() == 1
    assert db.query(Race).filter(Race.user_id == user.id).count() == 1
    assert db.query(TrainingPlan).count() == 1
    plan_ids = {plan.id for plan in db.query(TrainingPlan)}
    assert {entry.plan_id for entry in db.query(TrainingPlanEntry)} == plan_ids        # no entries left from the first seed
    assert db.query(Race).filter(Race.user_id == other.id).one().name == "Their race"
