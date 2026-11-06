"""Account deletion with SQLite in place of Postgres and an in-memory warehouse."""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.auth_routes import hash_password
from src.api.database import Base, Feedback, Race, TrainingPlan, TrainingPlanEntry, User
from src.api.routers import profile as profile_router
from src.api.schemas import AccountDelete
from src.api.services import account
from src.api.services.sync import SyncJob, registry
from src.pipeline import ACTIVITY_CHILD_TABLES, StravaDB

PASSWORD = "leaving-123456"
STRAVA_ATHLETE = 424242
STRAVA_ACTIVITY = 20_000_000_001
IMPORTED_ACTIVITY = 2**40 + 1


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, Race.__table__, TrainingPlan.__table__, TrainingPlanEntry.__table__, Feedback.__table__])
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def add_user(db, user_id, **fields):
    """A user with a race, a plan for it, one planned session and a feedback note."""
    user = User(id=user_id, email=f"runner{user_id}@example.com", password=hash_password(PASSWORD), name="Runner", **fields)
    race = Race(user_id=user_id, race_length="half marathon", race_date=date(2027, 4, 10))
    db.add_all([user, race, Feedback(user_id=user_id, page="/dashboard", message="A note")])
    db.flush()
    plan = TrainingPlan(user_id=user_id, name="Plan", race_id=race.id)
    db.add(plan)
    db.flush()
    db.add(TrainingPlanEntry(plan_id=plan.id, date=date(2026, 11, 7), workout_type="Easy Run"))
    db.commit()
    return user


def fill_warehouse(con, athlete_id, activity_id):
    """One row in every warehouse table for the athlete."""
    con.execute("INSERT INTO dim_athlete (athlete_id, firstname) VALUES (?, 'Runner')", [athlete_id])
    con.execute(
        "INSERT INTO dim_activity (activity_id, athlete_id, type, start_date_local, distance) VALUES (?, ?, 'Run', '2026-11-01 07:00:00', 8000)",
        [activity_id, athlete_id],
    )
    for table in ACTIVITY_CHILD_TABLES:
        if table.startswith("stream_"):
            value = "true" if table == "stream_moving" else "1"
            con.execute(f"INSERT INTO {table} (activity_id, time_offset, value) VALUES (?, 0, {value})", [activity_id])
    con.execute("INSERT INTO activity_effectiveness (activity_id, trimp_banister) VALUES (?, 40)", [activity_id])
    con.execute("INSERT INTO best_efforts (activity_id, distance_m, seconds) VALUES (?, 1609.344, 420)", [activity_id])
    con.execute("INSERT INTO activity_laps (activity_id, lap_index, distance) VALUES (?, 1, 1609.344)", [activity_id])
    con.execute("INSERT INTO activity_structure (activity_id, kind, rep_count) VALUES (?, 'intervals', 1)", [activity_id])
    con.execute("INSERT INTO activity_intervals (activity_id, rep_index, distance) VALUES (?, 1, 800)", [activity_id])
    con.execute("INSERT INTO coros_daily (athlete_id, day, rhr) VALUES (?, DATE '2026-11-01', 48)", [athlete_id])


def warehouse_rows(con, athlete_id, activity_id):
    """Row count per warehouse table for the athlete and their activity."""
    counts = {}
    for table in ACTIVITY_CHILD_TABLES:
        counts[table] = con.execute(f"SELECT COUNT(*) FROM {table} WHERE activity_id = ?", [activity_id]).fetchone()[0]
    for table in account.ATHLETE_TABLES:
        counts[table] = con.execute(f"SELECT COUNT(*) FROM {table} WHERE athlete_id = ?", [athlete_id]).fetchone()[0]
    return counts


def postgres_rows(db, user_id):
    plans = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id)
    return {
        "users": db.query(User).filter(User.id == user_id).count(),
        "races": db.query(Race).filter(Race.user_id == user_id).count(),
        "training_plans": plans.count(),
        "training_plan_entries": db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id.in_([plan.id for plan in plans])).count(),
        "feedback": db.query(Feedback).filter(Feedback.user_id == user_id).count(),
    }


def test_every_athlete_keyed_table_is_one_the_deletion_clears():
    warehouse = StravaDB(":memory:")
    keyed = {row[0] for row in warehouse.con.execute(
        "SELECT DISTINCT table_name FROM duckdb_columns() WHERE column_name = 'athlete_id' AND database_name = current_database()"
    ).fetchall()}
    assert keyed == set(account.ATHLETE_TABLES)


def test_deleting_an_account_removes_only_that_users_rows(db):
    warehouse = StravaDB(":memory:")
    leaving = add_user(db, 1, strava_athlete_id=STRAVA_ATHLETE, strava_refresh_token="refresh-1", coros_access_token="coros-1")
    staying = add_user(db, 2)
    fill_warehouse(warehouse.con, STRAVA_ATHLETE, STRAVA_ACTIVITY)
    fill_warehouse(warehouse.con, staying.athlete_id, IMPORTED_ACTIVITY)
    before = warehouse_rows(warehouse.con, staying.athlete_id, IMPORTED_ACTIVITY)
    assert set(before.values()) == {1}
    revoked = []

    account.delete_account(db, warehouse.con, leaving, PASSWORD, revoke=revoked.append)

    assert set(warehouse_rows(warehouse.con, STRAVA_ATHLETE, STRAVA_ACTIVITY).values()) == {0}
    assert warehouse_rows(warehouse.con, staying.athlete_id, IMPORTED_ACTIVITY) == before
    assert postgres_rows(db, 1) == {"users": 0, "races": 0, "training_plans": 0, "training_plan_entries": 0, "feedback": 0}
    assert postgres_rows(db, 2) == {"users": 1, "races": 1, "training_plans": 1, "training_plan_entries": 1, "feedback": 1}
    assert revoked == ["refresh-1"]


def test_a_wrong_password_deletes_nothing(db):
    warehouse = StravaDB(":memory:")
    leaving = add_user(db, 1, strava_athlete_id=STRAVA_ATHLETE, strava_refresh_token="refresh-1")
    fill_warehouse(warehouse.con, STRAVA_ATHLETE, STRAVA_ACTIVITY)
    revoked = []
    with pytest.raises(account.WrongPassword):
        account.delete_account(db, warehouse.con, leaving, "not it", revoke=revoked.append)
    assert set(warehouse_rows(warehouse.con, STRAVA_ATHLETE, STRAVA_ACTIVITY).values()) == {1}
    assert postgres_rows(db, 1) == {"users": 1, "races": 1, "training_plans": 1, "training_plan_entries": 1, "feedback": 1}
    assert revoked == []


def test_a_failed_strava_revoke_does_not_stop_the_deletion(db):
    warehouse = StravaDB(":memory:")
    leaving = add_user(db, 1, strava_athlete_id=STRAVA_ATHLETE, strava_refresh_token="refresh-1")
    attempts = []

    def refused(token):
        attempts.append(token)
        raise RuntimeError("Strava is down")

    account.delete_account(db, warehouse.con, leaving, PASSWORD, revoke=refused)
    assert attempts == ["refresh-1"]
    assert postgres_rows(db, 1)["users"] == 0


def test_a_user_without_strava_is_deleted_without_a_revoke(db):
    warehouse = StravaDB(":memory:")
    leaving = add_user(db, 1)
    revoked = []
    account.delete_account(db, warehouse.con, leaving, PASSWORD, revoke=revoked.append)
    assert revoked == [] and postgres_rows(db, 1)["users"] == 0


def test_the_route_waits_for_a_running_sync_and_ends_the_session(db, monkeypatch):
    warehouse = StravaDB(":memory:")
    leaving = add_user(db, 1, strava_athlete_id=STRAVA_ATHLETE, strava_refresh_token="refresh-1")
    revoked = []
    monkeypatch.setattr(profile_router, "SessionLocal", lambda: db)
    monkeypatch.setattr(profile_router, "get_db_connection", warehouse.con.cursor)
    monkeypatch.setattr(profile_router, "delete_account",
                        lambda db, con, user, password: account.delete_account(db, con, user, password, revoke=revoked.append))
    request = SimpleNamespace(session={"user_id": 1})

    registry.jobs[1] = SyncJob(1)                                       # a sync in progress
    try:
        with pytest.raises(HTTPException) as refused:
            profile_router.delete_own_account(AccountDelete(password=PASSWORD), request, current_user=leaving)
        assert refused.value.status_code == 409
    finally:
        registry.jobs.pop(1)
    with pytest.raises(HTTPException) as refused:
        profile_router.delete_own_account(AccountDelete(password="not it"), request, current_user=leaving)
    assert refused.value.status_code == 400
    assert request.session == {"user_id": 1} and postgres_rows(db, 1)["users"] == 1

    assert profile_router.delete_own_account(AccountDelete(password=PASSWORD), request, current_user=leaving) == {"deleted": True}
    assert request.session == {} and revoked == ["refresh-1"]
    assert postgres_rows(db, 1)["users"] == 0
