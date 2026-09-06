import pytest

from src.pipeline import (
    calculate_trimp_banister,
    calculate_aerobic_decoupling,
    StravaDB,
    run_analyze_effectiveness,
)


# ---- calculate_trimp_banister ----

def test_trimp_banister_zero_when_max_hr_at_or_below_rest():
    # max_hr <= rest_hr guards against a divide-by-zero/negative hr_ratio.
    assert calculate_trimp_banister(1800, 140, 60, 60, True) == 0
    assert calculate_trimp_banister(1800, 140, 50, 60, True) == 0


def test_trimp_banister_clamps_negative_hr_ratio_to_zero():
    # avg_hr below rest_hr would make hr_ratio negative without the
    # max(0, ...) clamp.
    assert calculate_trimp_banister(1800, 40, 180, 60, True) == 0


def test_trimp_banister_positive_for_normal_effort():
    assert calculate_trimp_banister(1800, 150, 190, 60, True) > 0


# ---- calculate_aerobic_decoupling ----

def test_aerobic_decoupling_empty_inputs_return_none():
    assert calculate_aerobic_decoupling([], [], [], []) is None
    assert calculate_aerobic_decoupling([1, 2], [100, 110], [], []) is None


def test_aerobic_decoupling_below_60_samples_returns_none():
    times = list(range(30))
    hr = [140] * 30
    vel = [3.0] * 30
    assert calculate_aerobic_decoupling(times, hr, times, vel) is None


def test_aerobic_decoupling_zero_mean_hr_half_handled_without_crash():
    # First half has hr == 0 throughout (mean == 0) — ef1 would divide by
    # zero without the explicit `if fh['hr'].mean() > 0 else 0` guard.
    n = 120
    times = list(range(n))
    hr = [0] * (n // 2) + [140] * (n // 2)
    vel = [3.0] * n
    assert calculate_aerobic_decoupling(times, hr, times, vel) == 0


def test_aerobic_decoupling_normal_case_returns_percentage():
    n = 120
    times = list(range(n))
    hr = [140] * n
    # Second half less efficient (lower velocity for the same HR).
    vel = [3.5] * (n // 2) + [3.0] * (n // 2)
    result = calculate_aerobic_decoupling(times, hr, times, vel)
    assert result is not None and result > 0


# ---- insert_streams: regression coverage for the duplicate-row bug ----

def _make_db():
    db = StravaDB(db_path=":memory:")
    db.con.execute("INSERT INTO dim_athlete (athlete_id, sex) VALUES (1, 'M')")
    db.con.execute("""
        INSERT INTO dim_activity (activity_id, athlete_id, type, start_date_local, moving_time, average_speed)
        VALUES (100, 1, 'Run', '2026-01-01 08:00:00', 1800, 3.0)
    """)
    return db


def test_insert_streams_is_idempotent():
    # Stream tables had no uniqueness constraint and insert_streams did a
    # DELETE-then-INSERT rather than a real upsert, so running ingestion
    # twice for the same activity duplicated every row (46,555 confirmed
    # excess rows found in production — see
    # agent_prompting/improvement_plan.md Section 2).
    db = _make_db()
    streams = {
        'time': {'data': [0, 1, 2, 3, 4]},
        'heartrate': {'data': [120, 121, 122, 123, 124]},
    }
    db.insert_streams(100, streams)
    db.insert_streams(100, streams)  # re-run with the identical payload

    rows = db.con.execute("SELECT COUNT(*) FROM stream_heartrate WHERE activity_id = 100").fetchone()[0]
    assert rows == 5  # not 10


def test_insert_streams_skips_mismatched_length_stream_with_warning(capsys):
    db = _make_db()
    streams = {
        'time': {'data': [0, 1, 2, 3, 4]},
        'heartrate': {'data': [120, 121, 122]},  # shorter than 'time'
    }
    db.insert_streams(100, streams)
    rows = db.con.execute("SELECT COUNT(*) FROM stream_heartrate WHERE activity_id = 100").fetchone()[0]
    assert rows == 0  # skipped entirely rather than silently truncated
    assert "WARNING" in capsys.readouterr().out


# ---- run_analyze_effectiveness: null-HR crash + no-stream-fallback regressions ----

def test_run_analyze_effectiveness_skips_null_hr_samples_without_crashing():
    db = _make_db()
    # A NULL sample (sensor dropout) mixed in with real ones used to raise
    # TypeError from an unfiltered sum(), aborting the whole batch.
    db.con.execute("INSERT INTO stream_heartrate VALUES (100, 0, 120), (100, 1, NULL), (100, 2, 130)")
    processed = run_analyze_effectiveness(db)
    assert processed == 1
    row = db.con.execute("SELECT trimp_banister FROM activity_effectiveness WHERE activity_id = 100").fetchone()
    assert row is not None and row[0] >= 0


def test_run_analyze_effectiveness_falls_back_to_average_hr_when_no_stream():
    # No stream_heartrate rows at all, but average_heartrate is on the
    # summary row — this activity used to be silently excluded from
    # training load entirely (indistinguishable from a rest day) because
    # the candidate-activity query required a stream via INNER JOIN.
    db = _make_db()
    db.con.execute("UPDATE dim_activity SET average_heartrate = 135 WHERE activity_id = 100")
    processed = run_analyze_effectiveness(db)
    assert processed == 1
    row = db.con.execute("SELECT trimp_banister FROM activity_effectiveness WHERE activity_id = 100").fetchone()
    assert row is not None and row[0] > 0


def test_run_analyze_effectiveness_excludes_activity_with_no_hr_data_at_all():
    db = _make_db()
    # No stream, no average_heartrate — genuinely nothing to compute from.
    processed = run_analyze_effectiveness(db)
    assert processed == 0
