"""Race predictions: the model in src/predictions.py, and what services/predictions.py reads from a warehouse."""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from src import predictions as model
from src.api.services import predictions
from src.metrics import vdot_from_race
from src.pipeline import StravaDB

TODAY = date(2026, 11, 6)
MILE_M, HALF_M, MARATHON_M = 1609.344, 21097.5, 42195.0
TRAINING = {"weekly_km": 35.0, "pace_s_per_km": 396.0}


def effort(label, distance_m, seconds, activity_id=1, days_ago=10):
    return {"label": label, "distance_m": distance_m, "seconds": seconds, "activity_id": activity_id,
            "date": TODAY - timedelta(days=days_ago), "name": "Morning Run"}


def test_a_half_marathon_sets_shorter_races_over_its_own_splits():
    # The 5K and 10K inside a half marathon are slower than an all-out 5K and 10K would be.
    run = [effort("1 mile", MILE_M, 491), effort("5K", 5000.0, 1595), effort("10K", 10000.0, 3255), effort("Half marathon", HALF_M, 6946)]
    five_k = model.predict(run, 5000.0)
    assert five_k["from"]["label"] == "Half marathon"
    assert five_k["seconds"] < 1595
    assert five_k["low_seconds"] < five_k["seconds"] < five_k["high_seconds"]
    assert model.predict(run, HALF_M)["seconds"] == pytest.approx(6946, abs=1)


def test_a_race_near_the_distance_beats_a_slightly_faster_one_far_from_it():
    mile, ten_k = effort("1 mile", MILE_M, 380, activity_id=1), effort("10K", 10000.0, 2700, activity_id=2)
    assert vdot_from_race(MILE_M, 380) > vdot_from_race(10000.0, 2700)
    assert model.predict([mile, ten_k], HALF_M)["from"] is ten_k
    assert model.predict([mile, ten_k], MILE_M)["from"] is mile


def test_tanda_is_the_published_formula():
    # (17.1 + 140 e^(-0.0053 * 60) + 0.55 * 300) s/km over 42.195 km, worked by hand.
    assert model.tanda_seconds(60, 300) == pytest.approx(11982, abs=1)


def test_the_marathon_meets_the_training_prediction_halfway():
    half = [effort("Half marathon", HALF_M, 6946)]
    alone = model.predict(half, MARATHON_M)
    trained = model.predict(half, MARATHON_M, training=TRAINING)
    tanda = model.tanda_seconds(TRAINING["weekly_km"], TRAINING["pace_s_per_km"])
    assert trained["training_seconds"] == pytest.approx(tanda)
    assert trained["seconds"] == pytest.approx((alone["seconds"] + tanda) / 2)
    assert trained["low_seconds"] <= min(alone["seconds"], tanda) and trained["high_seconds"] >= max(alone["seconds"], tanda)
    # Far below Tanda's sample the training model stays out, and shorter races never use it.
    assert model.predict(half, MARATHON_M, training={**TRAINING, "weekly_km": 8.0})["training_seconds"] is None
    assert model.predict(half, HALF_M, training=TRAINING)["training_seconds"] is None


def test_a_hilly_course_takes_longer_than_the_flat():
    efforts = [effort("10K", 10000.0, 2700)]
    flat = [{"distance_m": 100.0 * ii, "elevation_m": 200.0, "grade": 0.0} for ii in range(50)]
    rolling = [{**point, "grade": 0.04 if ii % 2 else -0.04} for ii, point in enumerate(flat)]
    on_flat, on_hills = model.predict(efforts, 5000.0, profile=flat), model.predict(efforts, 5000.0, profile=rolling)
    assert on_flat["course_factor"] == 1.0 and on_flat["seconds"] == model.predict(efforts, 5000.0)["seconds"]
    # Downhill gives back only half of what the climbs cost, so even an out-and-back costs time.
    assert on_hills["course_factor"] > 1.0
    assert on_hills["seconds"] == pytest.approx(on_flat["seconds"] * on_hills["course_factor"])


def test_older_efforts_widen_the_range():
    efforts = [effort("10K", 10000.0, 2700)]
    recent, old = model.predict(efforts, 10000.0), model.predict(efforts, 10000.0, old=True)
    assert recent["high_seconds"] - recent["low_seconds"] < old["high_seconds"] - old["low_seconds"]


def warehouse_with(runs):
    """An in-memory warehouse with athlete 7's runs: (activity_id, days_ago,
    distance_m, moving_s, climb_m, [(effort distance_m, seconds)])."""
    con = StravaDB(":memory:").con
    con.execute("INSERT INTO dim_athlete (athlete_id, firstname) VALUES (7, 'Test')")
    for activity_id, days_ago, distance, moving, climb, efforts in runs:
        con.execute(
            "INSERT INTO dim_activity (activity_id, athlete_id, name, type, start_date_local, distance, moving_time, total_elevation_gain)"
            " VALUES (?, 7, 'Run', 'Run', ?, ?, ?, ?)",
            [activity_id, datetime.combine(TODAY - timedelta(days=days_ago), time(7, 0)), distance, moving, climb],
        )
        for effort_m, seconds in efforts:
            con.execute("INSERT INTO best_efforts (activity_id, distance_m, seconds) VALUES (?, ?, ?)", [activity_id, effort_m, seconds])
    return con


def test_only_the_last_twelve_weeks_count_unless_nothing_is_that_recent():
    con = warehouse_with([
        (1, 150, 5000.0, 1320, 10, [(5000.0, 1320)]),                    # a fast 5K five months ago
        (2, 20, 10000.0, 2900, 30, [(5000.0, 1440), (10000.0, 2900)]),   # a 10K three weeks ago
    ])
    runner = SimpleNamespace(athlete_id=7)
    inputs = predictions.gather(con, runner, TODAY)
    assert inputs["old"] is False and {item["activity_id"] for item in inputs["efforts"]} == {2}
    assert predictions.predict(inputs, 5000.0)["from"]["label"] == "10K"
    con.execute("DELETE FROM best_efforts WHERE activity_id = 2")
    inputs = predictions.gather(con, runner, TODAY)
    assert inputs["old"] is True and [item["activity_id"] for item in inputs["efforts"]] == [1]


def test_training_is_weekly_distance_and_the_pace_of_runs_that_are_not_hilly():
    con = warehouse_with([
        (1, 3, 10000.0, 3000, 50, []),     # 5 m of climb a km: 5:00/km counts
        (2, 10, 10000.0, 4200, 300, []),   # 30 m a km: in the distance, not in the pace
        (3, 70, 20000.0, 6000, 0, []),     # older than 8 weeks
    ])
    result = predictions.training(con, 7, TODAY)
    assert result["weekly_km"] == pytest.approx(20.0 / 8)
    assert result["pace_s_per_km"] == pytest.approx(300.0)
    assert predictions.training(con, 8, TODAY) is None
