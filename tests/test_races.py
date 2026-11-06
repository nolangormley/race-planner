"""The race catalog, races created from it, and the first race at sign-up, with SQLite in place of Postgres
and the race-courses library answered from dicts."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import auth_routes
from src.api.database import Base, Race, User
from src.api.routers import races as races_router
from src.api.schemas import LibraryCourse, RaceCreate
from src.api.services import course_library as library

COURSE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
  <trkpt lat="40.4400" lon="-79.9500"><ele>100</ele></trkpt>
  <trkpt lat="40.4490" lon="-79.9500"><ele>120</ele></trkpt>
  <trkpt lat="40.4580" lon="-79.9500"><ele>140</ele></trkpt>
  <trkpt lat="40.4670" lon="-79.9500"><ele>130</ele></trkpt>
</trkseg></trk></gpx>"""


def event(race, name, event_name, distance_m, finishers, status="ok"):
    slug = event_name.lower().replace(" ", "-")
    return {"race": race, "event": slug, "name": name, "event_name": event_name, "city": "Pittsburgh", "state": "PA",
            "month": 11, "distance_m": distance_m, "finishers": finishers, "status": status,
            "file": f"courses/pa/{race}/{slug}.gpx" if status == "ok" else None}


CATALOG = [
    event("pittsburgh-10-miler", "EQT Pittsburgh 10 Miler & 10K", "10 Miler", 16093, 3325),
    event("bloomsday", "Lilac Bloomsday Run", "12K", 12000, 30000),                # no race length here
    event("pittsburgh-marathon", "Pittsburgh Marathon", "Marathon", 42195, 3786),
    event("pittsburgh-marathon", "Pittsburgh Marathon", "Half Marathon", 21097.5, 12310, status="unsupported"),
]
PICKED = LibraryCourse(race="pittsburgh-10-miler", event="10-miler")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, Race.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(races_router, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def runner(db):
    user = User(email="racer@example.com", password="unused", name="Racer")
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def files(monkeypatch):
    """The course files of the stub library by (race, event)."""
    found = {("pittsburgh-10-miler", "10-miler"): COURSE}
    monkeypatch.setattr(library, "entries", lambda: CATALOG)
    monkeypatch.setattr(library, "course_file", lambda race, event: found.get((race, event)))
    return found


def test_catalog_lists_races_of_a_planned_distance_biggest_first(runner, files):
    races = races_router.race_catalog(current_user=runner)["races"]
    assert [(race["name"], race["event_name"], race["race_length"], race["has_course"]) for race in races] == [
        ("Pittsburgh Marathon", "Half Marathon", "half marathon", False),
        ("Pittsburgh Marathon", "Marathon", "marathon", True),
        ("EQT Pittsburgh 10 Miler & 10K", "10 Miler", "10 mile", True),
    ]
    assert races[2] == {"race": "pittsburgh-10-miler", "event": "10-miler", "name": "EQT Pittsburgh 10 Miler & 10K",
                        "event_name": "10 Miler", "city": "Pittsburgh", "state": "PA", "month": 11,
                        "race_length": "10 mile", "has_course": True}


def test_catalog_that_cannot_be_read_answers_503(runner, monkeypatch):
    def offline():
        raise library.LibraryUnavailable("The course library could not be read (offline).")
    monkeypatch.setattr(library, "entries", offline)
    with pytest.raises(HTTPException) as refused:
        races_router.race_catalog(current_user=runner)
    assert refused.value.status_code == 503


def test_a_race_picked_from_the_catalog_is_saved_with_its_course(db, runner, files, monkeypatch):
    picked = races_router.create_user_race(runner.id, RaceCreate(
        name="EQT Pittsburgh 10 Miler & 10K", race_length="10 mile", race_date="2027-11-07", course=PICKED,
    ), current_user=runner)
    assert 2900 < picked["course"]["length_m"] < 3100
    typed = races_router.create_user_race(runner.id, RaceCreate(
        name="Club 10K", race_length="10k", race_date="2027-05-01",
    ), current_user=runner)
    assert typed["course"] is None
    files.clear()                                               # the dataset dropped the course
    gone = races_router.create_user_race(runner.id, RaceCreate(
        name="EQT Pittsburgh 10 Miler & 10K", race_length="10 mile", race_date="2028-11-05", course=PICKED,
    ), current_user=runner)
    assert gone["course"] is None
    listed = {race["id"]: race["has_course"] for race in races_router.get_user_races(runner.id, current_user=runner)}
    assert listed == {picked["id"]: True, typed["id"]: False, gone["id"]: False}


def test_a_library_that_cannot_be_read_still_saves_the_race(db, runner, monkeypatch):
    def offline(race, event):
        raise library.LibraryUnavailable("The course library could not be read (offline).")
    monkeypatch.setattr(library, "course_file", offline)
    created = races_router.create_user_race(runner.id, RaceCreate(
        name="EQT Pittsburgh 10 Miler & 10K", race_length="10 mile", race_date="2027-11-07", course=PICKED,
    ), current_user=runner)
    assert created["course"] is None and db.get(Race, created["id"]).course_gpx is None


def test_the_first_race_at_sign_up_comes_from_the_catalog_with_its_course(db, runner, files, monkeypatch):
    monkeypatch.setattr(auth_routes, "get_current_user_from_session", lambda request, session: runner)
    response = auth_routes.setup_race_post(
        request=None, race_length="10 mile", race_date="2027-11-07", race_goal_time=None,
        race_name="  EQT Pittsburgh 10 Miler & 10K ", course_race="pittsburgh-10-miler", course_event="10-miler", db=db,
    )
    assert response.status_code == 302
    race = db.query(Race).filter(Race.user_id == runner.id).one()
    assert (race.name, race.race_length, race.course_gpx) == ("EQT Pittsburgh 10 Miler & 10K", "10 mile", COURSE)
    auth_routes.setup_race_post(request=None, race_length="10k", race_date="2027-05-01", race_goal_time=None,
                                race_name="", course_race="", course_event="", db=db)
    typed = db.query(Race).filter(Race.race_length == "10k").one()
    assert typed.name is None and typed.course_gpx is None
