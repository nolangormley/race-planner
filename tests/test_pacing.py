import itertools
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.database import Base, Race, User
from src.api.routers import analytics, pacing as pacing_router
from src.api.schemas import LibraryCourse
from src.pacing import (
    METERS_PER_MILE, course_profile, format_clock, goal_range, grade_factor, parse_clock, parse_goal,
    parse_gpx_course, plan_splits, whole_second_splits,
)

HALF_MARATHON_M = 21097.5
MARATHON_M = 42195.0

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Rolling 4K</name><trkseg>
    <trkpt lat="40.4400" lon="-79.9500"><ele>100</ele></trkpt>
    <trkpt lat="40.4490" lon="-79.9500"><ele>120</ele></trkpt>
    <trkpt lat="40.4580" lon="-79.9500"><ele>140</ele></trkpt>
    <trkpt lat="40.4670" lon="-79.9500"><ele>130</ele></trkpt>
    <trkpt lat="40.4760" lon="-79.9500"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""


def test_clock_parsing_and_formatting():
    assert parse_clock("1:45:00") == [1, 45, 0] and parse_clock("45:00") == [45, 0] and parse_clock(" 1: 45 ") == [1, 45]
    assert parse_clock("") is None and parse_clock("fast") is None and parse_clock("1:2:3:4") is None and parse_clock("45") is None
    assert parse_clock("1:75") is None and parse_clock("1:45:60") is None and parse_clock("-1:45") is None
    assert format_clock(6300) == "1:45:00" and format_clock(2700) == "45:00" and format_clock(59.6) == "1:00"


def test_two_part_goal_reads_as_minutes_or_hours_by_the_distance():
    assert parse_goal("45:00", 10000) == 2700                   # m:ss
    assert parse_goal("1:45", HALF_MARATHON_M) == 6300          # 1 min 45 s is no half marathon: h:mm
    assert parse_goal("3:30", MARATHON_M) == 12600
    assert parse_goal("0:45", 10000) == 2700
    assert parse_goal("90:00", HALF_MARATHON_M) == 5400         # minutes past the hour
    assert parse_goal("1:45:00", HALF_MARATHON_M) == 6300


def test_goal_outside_a_plausible_pace_is_refused():
    fastest, slowest = goal_range(HALF_MARATHON_M)
    assert (format_clock(fastest), format_clock(slowest)) == ("45:53", "4:22:11")   # 3:30/mi and 20:00/mi
    assert parse_goal("45:53", HALF_MARATHON_M) == fastest and parse_goal("4:22:11", HALF_MARATHON_M) == slowest
    assert parse_goal("45:52", HALF_MARATHON_M) is None and parse_goal("4:22:12", HALF_MARATHON_M) is None
    assert parse_goal("0:30:00", HALF_MARATHON_M) is None       # 2:17/mi
    assert parse_goal("45:00", HALF_MARATHON_M) is None         # 3:26/mi as minutes, 45 hours as h:mm
    assert parse_goal("30:00", MARATHON_M) is None
    assert parse_goal("0:00", 5000) is None and parse_goal("fast", 5000) is None


def test_even_splits_share_the_goal_to_the_second():
    splits = plan_splits(10000, 45 * 60, 1000, "even")
    assert len(splits) == 10
    assert all(split["split_seconds"] == 270 for split in splits)
    assert splits[-1]["cumulative_seconds"] == 2700
    assert abs(splits[0]["seconds_per_km"] - 270) < 1e-6

    half = plan_splits(HALF_MARATHON_M, 6300, METERS_PER_MILE, "even")
    assert len(half) == 14 and half[-1]["length_m"] == pytest.approx(HALF_MARATHON_M - 13 * METERS_PER_MILE)
    assert sum(split["split_seconds"] for split in half) == 6300 and half[-1]["cumulative_seconds"] == 6300
    assert {split["split_seconds"] for split in half[:-1]} == {480, 481}        # 8:00.57 a mile in whole seconds
    assert all(abs(split["cumulative_seconds"] - 6300 * split["end_m"] / HALF_MARATHON_M) <= 0.5 for split in half)


@pytest.mark.parametrize("split_m, pace_key", [(METERS_PER_MILE, "seconds_per_mile"), (1000.0, "seconds_per_km")])
@pytest.mark.parametrize("goal, strategy", [(6300, "even"), (6600, "negative")])
def test_full_splits_show_the_pace_of_their_split_time(goal, strategy, split_m, pace_key):
    splits = plan_splits(HALF_MARATHON_M, goal, split_m, strategy)
    for split in splits[:-1]:
        assert split[pace_key] == pytest.approx(split["split_seconds"])
    assert list(itertools.accumulate(split["split_seconds"] for split in splits)) == [split["cumulative_seconds"] for split in splits]
    assert splits[-1]["cumulative_seconds"] == goal


def test_a_short_last_split_keeps_the_planned_pace():
    finish = plan_splits(HALF_MARATHON_M, 6300, METERS_PER_MILE, "even")[-1]
    planned = 6300 / HALF_MARATHON_M
    # Its 53 s over 0.11 mi would read 8:05/mi; the plan is 8:00.57/mi.
    assert finish["seconds_per_mile"] == pytest.approx(planned * METERS_PER_MILE)
    assert abs(finish["split_seconds"] - planned * finish["length_m"]) < 1


def test_whole_second_splits_keep_the_clock_on_the_plan_and_the_paces_in_order():
    # A steady 300.4 s a split: the clock rounds to the second, so the splits alternate.
    assert whole_second_splits([300.4] * 5, [True] * 5) == [300, 301, 300, 301, 300]
    # Rounding the clock alone gives 300, 301, 300 to a plan that gets faster.
    assert whole_second_splits([300.4, 300.3, 300.2], [True] * 3) == [301, 300, 300]
    # A short last split keeps its own time.
    assert whole_second_splits([300.4, 300.3, 52.6], [True, True, False]) == [301, 300, 52]


def test_rounding_never_slows_a_negative_split_down():
    # By the km over a marathon the plan gains less than a second a split.
    splits = plan_splits(MARATHON_M, 12600, 1000, "negative")
    times = [split["split_seconds"] for split in splits[:-1]]
    assert times == sorted(times, reverse=True)
    assert splits[-1]["cumulative_seconds"] == 12600


def test_negative_split_runs_the_second_half_about_two_percent_faster():
    splits = plan_splits(10000, 2700, 1000, "negative")
    first_half = sum(split["split_seconds"] for split in splits[:5])
    second_half = sum(split["split_seconds"] for split in splits[5:])
    assert first_half + second_half == 2700
    assert 1.015 < first_half / second_half < 1.025
    paces = [split["seconds_per_km"] for split in splits]
    assert paces == sorted(paces, reverse=True)                 # a steady ramp, not a step


def test_grade_cost_rises_uphill_and_gives_back_less_downhill():
    assert grade_factor(0.0) == pytest.approx(1.0)
    assert 1.25 < grade_factor(0.05) < 1.35
    assert 0.85 < grade_factor(-0.05) < 0.95
    assert grade_factor(0.5) == grade_factor(0.2)               # clipped
    assert grade_factor(0.05) - 1 > 1 - grade_factor(-0.05)     # asymmetric


def test_hilly_course_slows_the_climbs_and_still_meets_the_goal():
    # Up 4% for the first half, down 4% for the second.
    profile = [{"distance_m": step * 100.0, "elevation_m": 0.0, "grade": 0.04 if step <= 50 else -0.04} for step in range(1, 101)]
    splits = plan_splits(10000, 2700, 1000, "even", profile)
    assert splits[-1]["cumulative_seconds"] == 2700
    uphill = splits[0]["seconds_per_km"]
    downhill = splits[-1]["seconds_per_km"]
    assert uphill > 270 > downhill
    assert splits[0]["grade"] == pytest.approx(0.04) and splits[-1]["grade"] == pytest.approx(-0.04)
    assert splits[0]["climb_m"] == pytest.approx(40.0)


def test_gpx_course_becomes_a_per_100m_profile():
    course = parse_gpx_course(GPX)
    assert 3950 < course["length_m"] < 4060
    assert 39 <= len(course["points"]) <= 42
    assert course["points"][0]["distance_m"] == 100.0
    assert course["points"][-1]["distance_m"] == pytest.approx(course["length_m"])
    assert course["ascent_m"] > 25 and course["descent_m"] > 15
    grades = [point["grade"] for point in course["points"]]
    assert max(grades) > 0.01 and min(grades) < -0.01
    assert all(abs(grade) <= 0.2 for grade in grades)


def test_short_course_is_stretched_with_a_warning():
    course = parse_gpx_course(GPX)
    points, warnings = course_profile(course, 5000)
    assert points[-1]["distance_m"] == pytest.approx(5000)
    assert warnings and "stretched" in warnings[0]
    points, warnings = course_profile(course, course["length_m"] * 1.01)
    assert not warnings


def test_bad_files_are_rejected():
    with pytest.raises(ValueError):
        parse_gpx_course("<gpx><trk><trkseg><trkpt lat='1' lon='1'/></trkseg></trk></gpx>")
    with pytest.raises(ValueError):
        parse_gpx_course("not xml")
    with pytest.raises(ValueError):
        plan_splits(10000, 2700, 1000, "chaotic")


@pytest.fixture
def db(monkeypatch):
    """SQLite in place of Postgres for the pacing and predictions endpoints."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, Race.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(pacing_router, "SessionLocal", factory)
    monkeypatch.setattr(analytics, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def runner(db):
    user = User(email="pacer@example.com", password="unused", name="Pacer")
    db.add(user)
    db.commit()
    return user


def add_half(db, runner, goal_time):
    race = Race(user_id=runner.id, name="Winter Half", race_length="half marathon",
                race_date=date.today() + timedelta(weeks=8), goal_time=goal_time)
    db.add(race)
    db.commit()
    return race.id


def test_pacing_reads_typed_and_race_goals_for_the_distance(db, runner, monkeypatch):
    monkeypatch.setattr(pacing_router, "predicted_goal", lambda user, distance_m, course: 6600)
    plan = pacing_router.pacing_plan(add_half(db, runner, "1:45"), current_user=runner)
    assert (plan["goal_seconds"], plan["goal"], plan["goal_source"]) == (6300, "1:45:00", "race")
    assert plan["splits"][-1]["cumulative_seconds"] == 6300
    plan = pacing_router.pacing_plan(add_half(db, runner, "1:45:00"), goal="1:55", current_user=runner)
    assert (plan["goal_seconds"], plan["goal_source"]) == (6900, "query")
    plan = pacing_router.pacing_plan(add_half(db, runner, "finish"), current_user=runner)   # not a clock time
    assert (plan["goal_seconds"], plan["goal_source"]) == (6600, "prediction")


def test_pacing_refuses_a_goal_no_runner_can_hold(db, runner):
    race_id = add_half(db, runner, "1:45:00")
    with pytest.raises(HTTPException) as refused:
        pacing_router.pacing_plan(race_id, goal="0:30:00", current_user=runner)
    assert refused.value.status_code == 400
    assert refused.value.detail == (
        "Goal 0:30:00 is not a plausible half marathon time. Type a goal between 45:53 and 4:22:11 (3:30 to 20:00/mi)."
    )
    with pytest.raises(HTTPException) as refused:
        pacing_router.pacing_plan(add_half(db, runner, "0:30:00"), unit="km", current_user=runner)
    assert refused.value.detail == (
        "The race goal 0:30:00 is not a plausible half marathon time. Type a goal between 45:53 and 4:22:11 (2:10 to 12:26/km)."
    )
    with pytest.raises(HTTPException) as refused:
        pacing_router.pacing_plan(race_id, goal="1:75", current_user=runner)
    assert refused.value.detail == "Goal time should look like 1:45:00 or 45:00."


def test_predictions_card_reads_each_race_goal_for_its_distance(db, runner, monkeypatch):
    monkeypatch.setattr(analytics, "get_db_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(analytics.predictions, "gather", lambda con, user, today: {
        "efforts": [{"distance_m": 10000.0, "seconds": 2700, "label": "10K", "date": today - timedelta(days=9), "name": "Tempo"}],
        "old": False, "training": None,
    })
    monkeypatch.setattr(analytics, "calculate_vo2max", lambda athlete_id, hr_max, hr_rest: None)
    two_part, implausible = add_half(db, runner, "1:45"), add_half(db, runner, "0:30:00")
    races = {race["id"]: race for race in analytics.get_predictions(runner.athlete_id, current_user=runner)["races"]}
    assert races[two_part]["goal_seconds"] == 6300              # as the pace band reads it, not 105 s
    assert races[implausible]["goal_seconds"] is None           # no verdict against a goal no runner can hold


LIBRARY = [
    {"race": "pittsburgh-marathon", "event": "half-marathon", "name": "Pittsburgh Marathon", "event_name": "Half Marathon",
     "city": "Pittsburgh", "state": "PA", "distance_m": 21097.5, "status": "warn", "detail": "joined from 5 of 11 pieces of the map",
     "file": "courses/pa/pittsburgh-marathon/half-marathon.gpx", "length_m": 21000.0, "ascent_m": 180.0, "geometry_sha256": "ab12"},
    {"race": "boston-marathon", "event": "marathon", "name": "Boston Marathon", "event_name": "Marathon", "city": "Boston",
     "state": "MA", "distance_m": 42195.0, "status": "ok", "detail": "osm map", "file": "courses/ma/boston-marathon/marathon.gpx",
     "length_m": 42150.0, "ascent_m": 250.0, "geometry_sha256": "cd34"},
    {"race": "akron-marathon", "event": "half-marathon", "name": "Akron Marathon", "event_name": "Half Marathon", "city": "Akron",
     "state": "OH", "distance_m": 21097.5, "status": "ok", "detail": "mymaps map", "file": "courses/oh/akron-marathon/half-marathon.gpx",
     "length_m": 21120.0, "ascent_m": 140.0, "geometry_sha256": "ef56"},
]


def test_course_library_lists_the_courses_of_the_race_distance_by_name(db, runner, monkeypatch):
    monkeypatch.setattr(pacing_router.library, "courses", lambda: LIBRARY)
    listed = pacing_router.course_library(add_half(db, runner, "1:45:00"), current_user=runner)["courses"]
    assert [(course["name"], course["event"]) for course in listed] == [
        ("Akron Marathon", "half-marathon"), ("Pittsburgh Marathon", "half-marathon"),
    ]
    assert set(listed[1]) == set(pacing_router.LIBRARY_FIELDS)    # no file paths or digests
    assert listed[1]["status"] == "warn" and listed[1]["detail"] == "joined from 5 of 11 pieces of the map"


def test_a_library_course_is_stored_as_an_upload_is(db, runner, monkeypatch):
    files = {("pittsburgh-marathon", "half-marathon"): GPX, ("akron-marathon", "half-marathon"): "<gpx/>"}
    monkeypatch.setattr(pacing_router.library, "course_file", lambda race, event: files.get((race, event)))
    race_id = add_half(db, runner, "1:45:00")
    saved = pacing_router.use_library_course(
        race_id, LibraryCourse(race="pittsburgh-marathon", event="half-marathon"), current_user=runner,
    )
    assert 3950 < saved["course"]["length_m"] < 4060
    assert pacing_router.pacing_plan(race_id, current_user=runner)["course"] == saved["course"]
    for race, status in (("akron-marathon", 400), ("not-in-the-library", 404)):
        with pytest.raises(HTTPException) as refused:
            pacing_router.use_library_course(race_id, LibraryCourse(race=race, event="half-marathon"), current_user=runner)
        assert refused.value.status_code == status
    assert pacing_router.pacing_plan(race_id, current_user=runner)["course"] == saved["course"]   # kept


def test_course_library_that_cannot_be_read_answers_503(db, runner, monkeypatch):
    def offline(*args):
        raise pacing_router.library.LibraryUnavailable("The course library could not be read (offline).")
    monkeypatch.setattr(pacing_router.library, "courses", offline)
    monkeypatch.setattr(pacing_router.library, "course_file", offline)
    race_id = add_half(db, runner, "1:45:00")
    with pytest.raises(HTTPException) as refused:
        pacing_router.course_library(race_id, current_user=runner)
    assert (refused.value.status_code, refused.value.detail) == (503, "The course library could not be read (offline).")
    with pytest.raises(HTTPException) as refused:
        pacing_router.use_library_course(race_id, LibraryCourse(race="akron-marathon", event="half-marathon"), current_user=runner)
    assert refused.value.status_code == 503
