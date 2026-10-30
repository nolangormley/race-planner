from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.database import Base, User, Race, TrainingPlan, TrainingPlanEntry
from src.api.services.plan_match import complete_matching_entries
from src.pipeline import StravaDB

ATHLETE = 424242
TODAY = date(2026, 11, 6)            # a Friday; the window is Sat Oct 31 to today
MILE = 1609.344


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, Race.__table__, TrainingPlan.__table__, TrainingPlanEntry.__table__])
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, email="runner@example.com", password="x", name="Runner", strava_athlete_id=ATHLETE))
    session.add(TrainingPlan(id=1, user_id=1, name="Plan"))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def warehouse():
    store = StravaDB(":memory:")
    store.con.execute("INSERT INTO dim_athlete (athlete_id, sex) VALUES (?, 'M')", [ATHLETE])
    return store


def activity(warehouse, number, day, distance, kind="unchecked", sport="Run"):
    """Strava-sized ids (past 32 bits). kind None means checked with nothing found."""
    activity_id = 20_000_000_000 + number
    warehouse.con.execute(
        "INSERT INTO dim_activity (activity_id, athlete_id, type, start_date_local, distance) VALUES (?, ?, ?, ?, ?)",
        [activity_id, ATHLETE, sport, f"{day} 07:00:00", distance],
    )
    if kind != "unchecked":
        warehouse.con.execute("INSERT INTO activity_structure (activity_id, kind, rep_count) VALUES (?, ?, 0)", [activity_id, kind])
    return activity_id


def entry(db, day, workout, distance=None, **extra):
    planned = TrainingPlanEntry(plan_id=1, date=day, workout_type=workout, approximate_distance=distance, **extra)
    db.add(planned)
    db.commit()
    return planned


def test_runs_complete_the_sessions_they_match(db, warehouse):
    week_longest = activity(warehouse, 5, date(2026, 10, 28), 15000)
    short_long_run = activity(warehouse, 6, date(2026, 10, 31), 9000)
    long_enough = activity(warehouse, 4, date(2026, 11, 1), 13000)
    intervals = activity(warehouse, 1, date(2026, 11, 2), 8000, kind="intervals")
    steady = activity(warehouse, 2, date(2026, 11, 3), 9000, kind=None)
    easy = activity(warehouse, 3, date(2026, 11, 4), 6000)
    longest_this_week = activity(warehouse, 7, date(2026, 11, 5), 10000)
    lifting = activity(warehouse, 8, date(2026, 11, 6), 0, sport="WeightTraining")
    activity(warehouse, 9, date(2026, 11, 6), 5000)
    activity(warehouse, 10, date(2026, 10, 30), 5000)

    missed_long = entry(db, date(2026, 10, 31), "Long Run", "12 miles")     # 9 km is under 80% and not the week's longest
    long_run = entry(db, date(2026, 11, 1), "Long Run", "10 miles")         # 13 km is over 80% of 16.1 km
    session = entry(db, date(2026, 11, 2), "Intervals")
    second_on_same_day = entry(db, date(2026, 11, 2), "Easy Run")          # the only run that day is taken
    tempo = entry(db, date(2026, 11, 3), "Tempo")                           # the run had no tempo block
    easy_run = entry(db, date(2026, 11, 4), "Easy Run")
    open_long = entry(db, date(2026, 11, 5), "Long Run")                    # no distance: longest of its week
    strength = entry(db, date(2026, 11, 6), "Strength")
    rest = entry(db, date(2026, 11, 6), "Rest")
    too_old = entry(db, date(2026, 10, 30), "Easy Run")

    marked = complete_matching_entries(db, warehouse.con, 1, ATHLETE, today=TODAY)

    assert sorted(marked) == sorted([long_run.id, session.id, easy_run.id, open_long.id, strength.id])
    linked = {planned.id: planned.strava_activity_id for planned in db.query(TrainingPlanEntry).all()}
    assert linked[long_run.id] == long_enough
    assert linked[session.id] == intervals
    assert linked[easy_run.id] == easy
    assert linked[open_long.id] == longest_this_week
    assert linked[strength.id] == lifting
    for untouched in (missed_long, second_on_same_day, tempo, rest, too_old):
        assert linked[untouched.id] is None
        assert not db.get(TrainingPlanEntry, untouched.id).is_completed
    assert week_longest not in linked.values() and short_long_run not in linked.values() and steady not in linked.values()

    assert complete_matching_entries(db, warehouse.con, 1, ATHLETE, today=TODAY) == []


def test_an_activity_already_linked_by_hand_is_not_reused(db, warehouse):
    run = activity(warehouse, 1, date(2026, 11, 4), 6000)
    entry(db, date(2026, 11, 4), "Easy Run", is_completed=True, strava_activity_id=run)
    duplicate = entry(db, date(2026, 11, 4), "Easy Run")
    assert complete_matching_entries(db, warehouse.con, 1, ATHLETE, today=TODAY) == []
    assert db.get(TrainingPlanEntry, duplicate.id).strava_activity_id is None


def test_a_planned_tempo_is_done_by_a_detected_tempo(db, warehouse):
    run = activity(warehouse, 1, date(2026, 11, 5), 11000, kind="tempo")
    tempo = entry(db, date(2026, 11, 5), "tempo")                          # case does not matter
    assert complete_matching_entries(db, warehouse.con, 1, ATHLETE, today=TODAY) == [tempo.id]
    assert db.get(TrainingPlanEntry, tempo.id).strava_activity_id == run
