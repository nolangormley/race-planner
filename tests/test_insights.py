"""The What to work on rules (src/insights.py), the service that feeds them
(services/insights.py) on a temp warehouse, and the endpoint."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import insights as rules
from src.api.database import Base, User, Race, TrainingPlan, TrainingPlanEntry
from src.api.services import insights as service
from src.pipeline import StravaDB

TODAY = date(2026, 11, 2)
ATHLETE = 424242


# ---- Easy runs ----

def easy(avg_hr, climb=5.0, name="Morning Run"):
    return {"avg_hr": avg_hr, "climb": climb, "name": name}


HARD_MIX = [100, 400, 1500, 400, 0]      # 21% of the time in Recovery and Easy
EASY_MIX = [600, 1400, 300, 200, 0]      # 80%


def test_easy_runs_over_the_easy_zone_are_flagged_with_the_time_share():
    found = rules.easy_effort([easy(156), easy(155), easy(158, climb=25), easy(154)], HARD_MIX, 190, 50)
    assert (found["tone"], found["label"]) == ("warning", "Too hard")
    assert "average 156 bpm, over the top of your Easy zone at 148, and only 21% of your running time" in found["text"]
    assert "walk the steep climbs on trails" in found["text"]
    assert found["evidence"] == "4 easy runs in the last 4 weeks · max HR 190, resting 50"


def test_easy_runs_on_flat_ground_get_no_trail_advice():
    found = rules.easy_effort([easy(156)] * 4, HARD_MIX, 190, 50)
    assert "trails" not in found["text"]


@pytest.mark.parametrize("heart_rates, zones", [
    ([150, 151, 150, 151], HARD_MIX),    # within EASY_MARGIN_BPM of the ceiling
    ([156, 156, 156, 156], [200, 1300, 400, 100, 0]),   # 75% of the time easy: a few hard sessions, not hard easy days
])
def test_easy_runs_near_the_ceiling_or_with_mostly_easy_time_say_nothing(heart_rates, zones):
    assert rules.easy_effort([easy(hr) for hr in heart_rates], zones, 190, 50) is None


def test_easy_runs_inside_the_zone_with_mostly_easy_time_are_good_news():
    found = rules.easy_effort([easy(140), easy(143), easy(138), easy(141)], EASY_MIX, 190, 50)
    assert (found["tone"], found["label"], found["title"]) == ("good", "On track", "Easy days are easy")
    assert "80% of your running time was easy" in found["text"]


@pytest.mark.parametrize("runs, zones", [
    ([easy(160)] * 3, HARD_MIX),         # too few runs
    ([easy(160)] * 4, [0, 0, 0, 0, 0]),  # no heart rate time
])
def test_easy_runs_on_thin_data_say_nothing(runs, zones):
    assert rules.easy_effort(runs, zones, 190, 50) is None


# ---- Efficiency ----

def point(days_ago, ef, climb=5.0, name="Morning Run"):
    return {"day": TODAY - timedelta(days=days_ago), "name": name, "ef": ef, "climb": climb}


BEFORE_FLAT = [point(30 + 5 * index, 1.0) for index in range(6)]


def test_an_efficiency_dip_from_trail_runs_is_put_down_to_the_terrain():
    recent = [point(2, 0.85, climb=27), point(5, 0.84, name="Morning Trail Run"), point(9, 0.86, climb=30),
              point(12, 1.0), point(20, 1.01)]
    found = rules.efficiency_terrain(recent + BEFORE_FLAT, TODAY, 141, "mi")
    assert (found["tone"], found["label"]) == ("accent", "Terrain")
    assert "Efficiency is down 14% over the last 4 weeks, but 3 of your 5 easy runs were hilly or trail runs" in found["text"]
    # Two recent flat runs are too few to compare, so the note asks for a benchmark.
    assert "run a flat easy run every couple of weeks" in found["text"]


def test_the_terrain_note_compares_flat_runs_when_there_are_enough():
    recent = [point(day, 0.85, climb=25) for day in (1, 3, 5, 7, 9)] + [point(day, 1.03) for day in (2, 4, 6, 8)]
    found = rules.efficiency_terrain(recent + BEFORE_FLAT, TODAY, 141, "mi")
    assert found["id"] == "efficiency" and found["tone"] == "accent"
    assert "On flat runs you are 0:20/mi faster at 141 bpm" in found["text"]


def test_flat_runs_losing_efficiency_are_a_warning():
    recent = [point(day, 0.92) for day in (2, 6, 10, 14)]
    found = rules.efficiency_terrain(recent + BEFORE_FLAT, TODAY, 141, "km")
    assert (found["tone"], found["label"]) == ("warning", "Check")
    assert "you are 0:37/km slower at 141 bpm" in found["text"] and "fatigue, heat, sleep or a coming illness" in found["text"]
    assert found["evidence"] == "4 flat easy runs in the last 4 weeks, 6 in the 8 weeks before"


def test_flat_runs_gaining_efficiency_are_good_news():
    recent = [point(day, 1.05) for day in (2, 6, 10, 14)]
    found = rules.efficiency_terrain(recent + BEFORE_FLAT, TODAY, 141, "mi")
    assert (found["tone"], found["label"]) == ("good", "Improving")
    assert "0:33/mi faster at 141 bpm" in found["text"]


def test_steady_efficiency_says_nothing():
    recent = [point(day, 1.01) for day in (2, 6, 10, 14)]
    assert rules.efficiency_terrain(recent + BEFORE_FLAT, TODAY, 141, "mi") is None


def test_treadmill_runs_do_not_count_and_thin_data_says_nothing():
    recent = [point(2, 0.8), point(6, 0.8), point(10, 0.8)] + [point(day, 1.3, climb=0) for day in (3, 4, 5)]
    assert rules.efficiency_terrain(recent + BEFORE_FLAT, TODAY, 141, "mi") is None


# ---- Load, recovery, plan ----

def test_a_load_spike_is_serious_and_a_fast_ramp_a_warning():
    assert rules.load_ramp(60, 55, 1.6, 200)["tone"] == "serious"
    ramp = rules.load_ramp(60, 50, 1.3, 200)
    assert (ramp["tone"], ramp["label"]) == ("warning", "Ramping fast") and "Fitness rose 10 points in a week" in ramp["text"]
    assert rules.load_ramp(60, 55, 1.2, 200) is None


def test_load_waits_for_a_settled_fitness_average():
    # Six weeks of steady training still read as a ratio over 1.5.
    assert rules.load_ramp(30, 20, 1.6, 42) is None


def night(days_ago, hrv, rhr):
    return {"day": TODAY - timedelta(days=days_ago), "hrv": hrv, "rhr": rhr}


BASELINE_NIGHTS = [night(days_ago, 70, 48) for days_ago in range(8, 36)]


def test_lower_hrv_with_a_higher_resting_heart_rate_is_a_warning():
    found = rules.recovery_trend([night(days_ago, 60, 52) for days_ago in range(7)] + BASELINE_NIGHTS, TODAY)
    assert (found["tone"], found["label"]) == ("warning", "Recovery")
    assert "HRV is 14% under its 4-week average (60 against 70 ms) and your resting heart rate is up 4 bpm" in found["text"]


@pytest.mark.parametrize("recent", [
    [night(days_ago, 60, 48) for days_ago in range(7)],     # HRV down, resting heart rate steady
    [night(days_ago, 60, 52) for days_ago in range(4)],     # too few recent nights
])
def test_recovery_needs_both_signals_and_enough_nights(recent):
    assert rules.recovery_trend(recent + BASELINE_NIGHTS, TODAY) is None


@pytest.mark.parametrize("planned, done, flagged", [(8, 3, True), (8, 5, False), (3, 0, False)])
def test_plan_slipping(planned, done, flagged):
    found = rules.plan_slipping(planned, done)
    assert (found is not None) is flagged
    if flagged:
        assert found["text"].startswith("You did 3 of 8 planned sessions in the last two weeks.")


def test_rank_puts_serious_first_keeps_rule_order_and_four_at_most():
    def made(key, tone):
        return rules.insight(key, tone, "", "", "", "")
    ranked = rules.rank([made("a", "good"), None, made("b", "warning"), made("c", "serious"), made("d", "accent"),
                         made("e", "warning")])
    assert [item["id"] for item in ranked] == ["c", "b", "e", "d"]


# ---- Service and endpoint ----

@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, Race.__table__, TrainingPlan.__table__, TrainingPlanEntry.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_insights_for_reads_the_warehouse_and_the_plan(db_session, tmp_path, monkeypatch):
    path = str(tmp_path / "warehouse.duckdb")
    warehouse = StravaDB(path)
    warehouse.con.execute("INSERT INTO dim_athlete (athlete_id, sex) VALUES (?, 'M')", [ATHLETE])
    runs = [(day, "Morning Run", None) for day in (1, 4, 8, 11, 15, 18)]
    runs += [(20, "Turkey Trot race", None), (22, "Morning Run", "tempo")]    # a race and a tempo run stay out
    for number, (days_ago, name, kind) in enumerate(runs, start=1):
        activity_id = 30_000_000_000 + number
        start = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time()).replace(hour=7)
        warehouse.con.execute(
            "INSERT INTO dim_activity (activity_id, athlete_id, name, type, start_date_local, distance, moving_time,"
            " total_elevation_gain, average_heartrate) VALUES (?, ?, ?, 'Run', ?, 8000, 2400, 40, 155)",
            [activity_id, ATHLETE, name, start],
        )
        warehouse.con.execute(
            "INSERT INTO activity_effectiveness (activity_id, trimp_banister, efficiency_factor, intensity_factor,"
            " zone_1_sec, zone_2_sec, zone_3_sec, zone_4_sec, zone_5_sec) VALUES (?, 60, 0.95, ?, 100, 400, 1500, 400, 0)",
            [activity_id, 155 / 190],
        )
        if kind:
            warehouse.con.execute("INSERT INTO activity_structure (activity_id, kind, rep_count) VALUES (?, ?, 0)", [activity_id, kind])
    warehouse.con.close()
    monkeypatch.setattr(service, "get_db_connection", lambda: duckdb.connect(path))

    user = User(id=1, email="runner@example.com", password="x", name="Runner", strava_athlete_id=ATHLETE, max_hr=190, resting_hr=50)
    db_session.add_all([user, TrainingPlan(id=1, user_id=1, name="Plan")])
    db_session.add_all([
        TrainingPlanEntry(plan_id=1, date=TODAY - timedelta(days=days_ago), workout_type=workout, is_completed=False)
        for days_ago, workout in [(1, "Easy Run"), (2, "Tempo"), (3, "Rest"), (5, "Intervals"), (6, "Long Run"),
                                  (8, "Easy Run"), (9, "Tempo"), (0, "Tempo")]
    ])
    db_session.commit()

    found = service.insights_for(db_session, user, "mi", TODAY)
    assert [item["id"] for item in found] == ["easy-effort", "plan"]
    assert found[0]["evidence"].startswith("6 easy runs in the last 4 weeks")
    # Six sessions in the last 14 days (rest and today left out), done on the two days with a run.
    assert found[1]["text"].startswith("You did 2 of 6 planned sessions")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    # Importing the app creates the warehouse schema at DB_PATH, so point it at a temp file.
    import src.api.deps as deps
    deps.DB_PATH = str(tmp_path_factory.mktemp("warehouse") / "warehouse.duckdb")
    from src.api.main import app
    return app


def test_the_endpoint_answers_only_its_own_user_in_a_known_unit(app, monkeypatch):
    from src.api.auth_routes import get_current_user
    from src.api.routers import insights as router

    monkeypatch.setattr(router, "insights_for", lambda db, user, unit: [{"id": "x", "unit": unit}])
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=5)
    try:
        client = TestClient(app)
        assert client.get("/api/insights/5?unit=km").json() == {"insights": [{"id": "x", "unit": "km"}]}
        assert client.get("/api/insights/6").status_code == 403
        assert client.get("/api/insights/5?unit=furlongs").status_code == 400
    finally:
        app.dependency_overrides.clear()
