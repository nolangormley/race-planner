"""The Feedback button's endpoint and the admin page: the activation numbers
from plain rows, those rows read from SQLite (in place of Postgres) and an
in-memory warehouse, the last-seen stamp, and the gate (a 404 for anyone not
in ADMIN_EMAILS)."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import auth_routes
from src.api.database import Base, Feedback, Race, User, synthetic_athlete_id
from src.api.routers import admin as admin_router, feedback as feedback_router, pages
from src.api.services import admin as service
from src.pipeline import StravaDB

THURSDAY = datetime(2026, 11, 5, 10, 0)
PASSWORD = "a-password-123"
HASHED = auth_routes.hash_password(PASSWORD)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    # Importing the app creates the warehouse schema at DB_PATH. An earlier
    # test module may have imported it already, so create the schema here too.
    import src.api.deps as deps
    deps.DB_PATH = str(tmp_path_factory.mktemp("warehouse") / "warehouse.duckdb")
    from src.api.main import app
    StravaDB(deps.DB_PATH).con.close()
    return app


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, Race.__table__, Feedback.__table__])
    factory = sessionmaker(bind=engine)
    for module in (auth_routes, pages, admin_router, feedback_router):
        monkeypatch.setattr(module, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()


def facts(created_at=None, last_seen_at=None, connected=False, has_race=False, has_course=False):
    return {"created_at": created_at, "last_seen_at": last_seen_at, "connected": connected, "has_race": has_race, "has_course": has_course}


def add_user(db, email, name, **fields):
    user = User(email=email, password=HASHED, name=name, **fields)
    db.add(user)
    db.commit()
    return user


def login(client, user):
    response = client.post("/login", data={"username": user.email, "password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 302


def test_activation_counts_each_week_and_the_totals():
    rows = [
        facts(datetime(2026, 11, 3, 9), datetime(2026, 11, 3, 9), connected=True, has_race=True),   # this week
        facts(datetime(2026, 10, 20), datetime(2026, 10, 28), has_race=True, has_course=True),       # back eight days later
        facts(datetime(2026, 10, 20), datetime(2026, 10, 26)),                                       # six days: not back yet
        facts(datetime(2026, 9, 10), datetime(2026, 11, 1), connected=True),                         # before the eight weeks
        facts(None, datetime(2026, 11, 4), connected=True, has_race=True),                           # before tracking
    ]
    report = service.activation(rows, THURSDAY)
    weeks = {week["start"]: week for week in report["weeks"]}
    assert list(weeks) == [date(2026, 9, 14) + timedelta(days=7 * ii) for ii in range(8)]
    assert weeks[date(2026, 11, 2)] == {"start": date(2026, 11, 2), "signed_up": 1, "connected": 1, "added_race": 1, "course_file": 0, "returned": 0}
    assert weeks[date(2026, 10, 19)] == {"start": date(2026, 10, 19), "signed_up": 2, "connected": 0, "added_race": 1, "course_file": 1, "returned": 1}
    assert sum(week["signed_up"] for week in report["weeks"]) == 3
    assert report["totals"] == {"signed_up": 5, "connected": 3, "added_race": 3, "course_file": 1, "returned": 2, "before_tracking": 1}


def test_activation_weeks_run_monday_to_sunday_and_the_seventh_day_counts_as_back():
    monday = datetime(2026, 10, 26)
    rows = [
        facts(monday, monday + timedelta(days=7)),                     # the week's first second; back on the seventh day
        facts(datetime(2026, 11, 1, 23, 59), datetime(2026, 11, 2)),   # the week's last minute
        facts(datetime(2026, 11, 2), None),                             # the next week; never seen again
    ]
    weeks = {week["start"]: week for week in service.activation(rows, THURSDAY)["weeks"]}
    assert (weeks[date(2026, 10, 26)]["signed_up"], weeks[date(2026, 10, 26)]["returned"]) == (2, 1)
    assert (weeks[date(2026, 11, 2)]["signed_up"], weeks[date(2026, 11, 2)]["returned"]) == (1, 0)


def test_user_facts_reads_links_races_courses_and_warehouse_activities(db):
    strava = add_user(db, "strava@example.com", "Strava", strava_athlete_id=7, created_at=THURSDAY)
    coros = add_user(db, "coros@example.com", "COROS", coros_linked_at=THURSDAY)
    files = add_user(db, "files@example.com", "Files")
    nobody = add_user(db, "nobody@example.com", "Nobody")
    db.add_all([
        Race(user_id=strava.id, race_length="10k", race_date=date(2027, 5, 1)),
        Race(user_id=files.id, race_length="marathon", race_date=date(2027, 5, 1), course_gpx="<gpx/>"),
    ])
    db.commit()
    warehouse = StravaDB(":memory:")
    warehouse.con.execute("INSERT INTO dim_athlete (athlete_id, sex) VALUES (?, 'F')", [synthetic_athlete_id(files.id)])
    warehouse.con.execute("INSERT INTO dim_activity (activity_id, athlete_id, type) VALUES (1, ?, 'Run')", [synthetic_athlete_id(files.id)])

    assert service.user_facts(db, warehouse.con) == [
        facts(THURSDAY, None, connected=True, has_race=True),
        facts(coros.created_at, None, connected=True),
        facts(files.created_at, None, connected=True, has_race=True, has_course=True),
        facts(nobody.created_at, None),
    ]


def test_last_seen_is_stamped_at_most_once_a_day(db):
    user = add_user(db, "runner@example.com", "Runner")
    auth_routes.touch_last_seen(user, db, THURSDAY)
    auth_routes.touch_last_seen(user, db, THURSDAY + timedelta(hours=13))
    assert db.get(User, user.id).last_seen_at == THURSDAY
    auth_routes.touch_last_seen(user, db, THURSDAY + timedelta(days=1, minutes=1))
    assert db.get(User, user.id).last_seen_at == THURSDAY + timedelta(days=1, minutes=1)


def test_a_note_is_stored_with_its_page_and_the_contact_choice(app, db):
    user_id = add_user(db, "runner@example.com", "Runner").id
    client = TestClient(app)
    assert client.post("/api/feedback", json={"message": "hello"}).status_code == 401       # logged out
    app.dependency_overrides[auth_routes.get_current_user] = lambda: SimpleNamespace(id=user_id)
    try:
        sent = client.post("/api/feedback", json={
            "message": "  The calendar could show the week's total.  ", "page": "/training-calendar", "contact_ok": True,
        })
        assert sent.status_code == 200, sent.text
        note = db.query(Feedback).one()
        assert (note.user_id, note.page, note.message, note.contact_ok) == (user_id, "/training-calendar", "The calendar could show the week's total.", True)
        assert note.created_at is not None
        [listed] = service.recent_feedback(db)
        assert (listed["user_name"], listed["user_email"], listed["page"], listed["contact_ok"]) == ("Runner", "runner@example.com", "/training-calendar", True)
        assert listed["created_at"].startswith(note.created_at.isoformat()[:19])

        assert client.post("/api/feedback", json={"message": "   "}).status_code == 400
        assert client.post("/api/feedback", json={"message": "x" * 4001}).status_code == 422
        assert client.post("/api/feedback", json={"message": "ok", "page": "/" * 201}).status_code == 422
        assert db.query(Feedback).count() == 1
    finally:
        app.dependency_overrides.clear()


def test_admin_page_and_api_exist_only_for_admins(app, db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Owner@Example.com, coach@example.com")
    owner = add_user(db, "owner@example.com", "Owner")
    runner = add_user(db, "runner@example.com", "Runner")
    client = TestClient(app)
    assert client.get("/admin").status_code == 404                     # logged out
    assert client.get("/api/admin/stats").status_code == 404

    login(client, runner)
    assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/stats").status_code == 404
    assert 'href="/admin"' not in client.get("/dashboard").text

    client.get("/logout")
    login(client, owner)
    assert 'href="/admin"' in client.get("/dashboard").text
    assert "Sign-ups by week" in client.get("/admin").text
    report = client.get("/api/admin/stats").json()
    assert len(report["weeks"]) == 8
    assert (report["weeks"][-1]["signed_up"], report["totals"]["signed_up"], report["totals"]["before_tracking"]) == (2, 2, 0)
    assert report["totals"]["returned"] == 0 and report["feedback"] == []

    monkeypatch.delenv("ADMIN_EMAILS")                                 # unset: nobody is an admin
    assert client.get("/admin").status_code == 404
