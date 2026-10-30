import numpy as np
import pytest

from src.intervals import detect_structure
from src.pipeline import StravaDB
from src.structure_pass import run_detect_structure, stored_structure


def run_from_blocks(blocks, noise=0.08, seed=7):
    """Per-second streams from (seconds, speed m/s, heart rate) blocks, with
    a little speed noise so the detector never sees perfectly flat data."""
    rng = np.random.default_rng(seed)
    speeds, beats = [], []
    for seconds, speed, heartrate in blocks:
        speeds += [speed] * seconds
        beats += [heartrate] * seconds
    speeds = np.maximum(np.array(speeds) + rng.normal(0, noise, len(speeds)), 0.0)
    return list(range(len(speeds))), [float(value) for value in speeds], beats


def track_session(reps=6):
    """Warm-up, reps x 800 m at 4.5 m/s with 400 m jogs at 2.5 m/s, cool-down."""
    blocks = [(600, 3.0, 140)]
    for rep in range(reps):
        blocks.append((178, 4.5, 172))
        if rep < reps - 1:
            blocks.append((160, 2.5, 150))
    blocks.append((600, 3.0, 145))
    return run_from_blocks(blocks)


def test_track_reps_are_found_with_their_distances_and_recoveries():
    times, speeds, beats = track_session()
    structure = detect_structure(times, speeds, heartrate=beats)

    assert structure["kind"] == "intervals"
    assert len(structure["reps"]) == 6
    for rep in structure["reps"]:
        assert rep["distance"] == pytest.approx(800, abs=40)
        assert rep["average_speed"] == pytest.approx(4.5, abs=0.15)
        assert rep["average_heartrate"] == pytest.approx(172, abs=8)
    assert [rep["recovery_time"] for rep in structure["reps"][:-1]] == pytest.approx([160] * 5, abs=15)
    assert structure["reps"][-1]["recovery_time"] is None
    assert len(structure["recoveries"]) == 5
    assert structure["reps"][0]["start_offset"] == pytest.approx(600, abs=15)


def test_a_steady_run_has_no_structure():
    times, speeds, beats = run_from_blocks([(2400, 3.3, 150)])
    assert detect_structure(times, speeds, heartrate=beats) == {"kind": None, "reps": [], "recoveries": []}


def test_a_long_fast_block_is_a_tempo():
    times, speeds, beats = run_from_blocks([(600, 3.0, 140), (1200, 3.8, 168), (600, 3.0, 145)])
    structure = detect_structure(times, speeds, heartrate=beats)
    assert structure["kind"] == "tempo"
    assert len(structure["reps"]) == 1
    assert structure["reps"][0]["moving_time"] == pytest.approx(1200, abs=20)
    assert structure["reps"][0]["recovery_time"] is None


def test_a_short_fast_block_is_neither():
    # Ten minutes faster in a 40-minute run: too short for a tempo, too few for intervals.
    times, speeds, beats = run_from_blocks([(900, 3.3, 150), (600, 3.8, 162), (900, 3.3, 150)])
    assert detect_structure(times, speeds, heartrate=beats)["kind"] is None


def rolling_hills(cycles=5, grade=0.04, flat_speed=3.3):
    """Even effort over 4-minute climbs and descents: slow up, fast down."""
    up_speed = flat_speed / (1 + 4.5 * grade)
    down_speed = flat_speed / (1 - 2.25 * grade)
    speeds, altitude = [], [200.0]
    for _ in range(cycles):
        for speed, slope in ((up_speed, grade), (down_speed, -grade)):
            for _ in range(240):
                speeds.append(speed)
                altitude.append(altitude[-1] + slope * speed)
    return list(range(len(speeds))), speeds, altitude[:-1]


def test_climbs_at_even_effort_do_not_read_as_recoveries():
    times, speeds, altitude = rolling_hills()
    # On raw pace the descents look like reps; the grade cost evens them out.
    assert detect_structure(times, speeds)["kind"] == "intervals"
    assert detect_structure(times, speeds, altitude_m=altitude)["kind"] is None


def test_recording_pauses_are_stops_not_structure():
    times, speeds, beats = run_from_blocks([(2400, 3.3, 150)])
    # Two 90-second auto-pauses: the time stream jumps and nothing is recorded.
    keep = [index for index, second in enumerate(times) if not (800 <= second < 890 or 1600 <= second < 1690)]
    structure = detect_structure([times[index] for index in keep], [speeds[index] for index in keep], heartrate=[beats[index] for index in keep])
    assert structure["kind"] is None


def test_reps_next_to_standing_rests_keep_their_full_length():
    # 8 x 400 m with 60 s standing rests the watch auto-paused through.
    rng = np.random.default_rng(3)
    times, speeds, clock = [], [], 0
    for _ in range(600):
        times.append(clock)
        speeds.append(3.0)
        clock += 1
    for _ in range(8):
        for _ in range(80):
            times.append(clock)
            speeds.append(5.0 + rng.normal(0, 0.1))
            clock += 1
        clock += 60
    for _ in range(600):
        times.append(clock)
        speeds.append(3.0)
        clock += 1
    structure = detect_structure(times, speeds)
    assert structure["kind"] == "intervals"
    assert [rep["distance"] for rep in structure["reps"]] == pytest.approx([400] * 8, abs=15)
    assert [rep["recovery_time"] for rep in structure["reps"][:-1]] == pytest.approx([60] * 7, abs=4)


def test_missing_readings_and_short_runs_are_handled():
    assert detect_structure([], [])["kind"] is None
    assert detect_structure(list(range(120)), [3.0] * 120)["kind"] is None
    times, speeds, beats = track_session()
    speeds[700] = None
    beats = [None] * len(beats)
    structure = detect_structure(times, speeds, heartrate=beats)
    assert structure["kind"] == "intervals"
    assert structure["reps"][0]["average_heartrate"] is None


def add_run(db, activity_id, times, speeds, beats=None):
    db.con.execute(
        "INSERT INTO dim_activity (activity_id, athlete_id, type, start_date_local, moving_time) VALUES (?, 1, 'Run', '2026-11-02 07:00:00', ?)",
        [activity_id, len(times)],
    )
    streams = {"time": {"data": times}, "velocity_smooth": {"data": speeds}}
    if beats:
        streams["heartrate"] = {"data": beats}
    db.insert_streams(activity_id, streams)


def test_the_warehouse_pass_stores_structure_once():
    db = StravaDB(":memory:")
    db.con.execute("INSERT INTO dim_athlete (athlete_id, sex) VALUES (1, 'M')")
    add_run(db, 100, *track_session())
    add_run(db, 101, *run_from_blocks([(2400, 3.3, 150)]))

    assert run_detect_structure(db) == 2
    intervals = stored_structure(db.con, 100)
    assert intervals["kind"] == "intervals" and intervals["detected"]
    assert [rep["index"] for rep in intervals["reps"]] == [1, 2, 3, 4, 5, 6]
    assert len(intervals["recoveries"]) == 5
    assert intervals["recoveries"][0]["start_offset"] == intervals["reps"][0]["end_offset"]
    assert stored_structure(db.con, 101) == {"kind": None, "detected": True, "reps": [], "recoveries": []}
    assert stored_structure(db.con, 999)["detected"] is False

    assert run_detect_structure(db) == 0                                  # nothing new
    db.con.execute("UPDATE dim_activity SET updated_at = now() + INTERVAL 1 MINUTE WHERE activity_id = 100")
    assert run_detect_structure(db) == 1                                  # re-synced run is redone
    assert db.con.execute("SELECT count(*) FROM activity_intervals WHERE activity_id = 100").fetchone()[0] == 6
