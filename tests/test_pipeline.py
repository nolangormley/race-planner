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


# ---- ingest_data: two passes, progress reporting ----

class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _FakeStrava:
    """Stands in for StravaManager: one page of activities, canned streams."""

    def __init__(self, activities, fail_first_list=False):
        self.activities = activities
        self.fail_first_list = fail_first_list
        self.list_calls = 0
        self.stream_calls = []

    def fetch_activities(self, access_token, count=50, page=1, after=None):
        self.list_calls += 1
        if self.fail_first_list and self.list_calls == 1:
            raise Exception("Unauthorized")
        return self.activities if page == 1 else []

    def fetch_activity_streams(self, access_token, activity_id):
        self.stream_calls.append(activity_id)
        return {'time': {'data': [0, 1, 2]}, 'heartrate': {'data': [130, 131, 132]}}

    def fetch_activity_laps(self, access_token, activity_id):
        return [
            {'lap_index': 1, 'name': 'Lap 1', 'start_index': 0, 'end_index': 1, 'elapsed_time': 2, 'moving_time': 2, 'distance': 6.0, 'average_speed': 3.0},
            {'lap_index': 2, 'name': 'Lap 2', 'start_index': 2, 'end_index': 2, 'elapsed_time': 1, 'moving_time': 1, 'distance': 3.0, 'average_speed': 3.0},
        ]


def _ingest(monkeypatch, strava, db, **kwargs):
    import src.pipeline as pipeline
    monkeypatch.setattr(pipeline.requests, "get", lambda *args, **kw: _FakeResponse({'id': 1, 'firstname': 'Test'}))
    kwargs.setdefault("reauth_fn", lambda: None)
    return pipeline.ingest_data(strava, db, "token", **kwargs)


def test_ingest_only_fetches_streams_for_new_activities_and_reports_progress(monkeypatch):
    db = _make_db()
    db.insert_streams(100, {'time': {'data': [0, 1]}, 'heartrate': {'data': [120, 121]}})
    strava = _FakeStrava([
        {'id': 100, 'name': 'Already synced', 'type': 'Run', 'start_date_local': '2026-01-01T08:00:00Z'},
        {'id': 101, 'name': 'New Run', 'type': 'Run', 'start_date_local': '2026-01-02T08:00:00Z'},
    ])
    events = []

    count = _ingest(monkeypatch, strava, db, progress=lambda phase, **info: events.append((phase, info)))

    assert count == 1
    assert strava.stream_calls == [101]
    assert events == [
        ('listing', {'page': 1, 'found': 2}),
        ('streams', {'done': 1, 'total': 1, 'name': 'New Run'}),
    ]
    assert db.activity_exists(101) and db.activity_has_streams(101)


def test_ingest_retries_the_activity_list_after_reauth(monkeypatch):
    db = _make_db()
    strava = _FakeStrava(
        [{'id': 102, 'name': 'Run', 'type': 'Run', 'start_date_local': '2026-01-03T08:00:00Z'}],
        fail_first_list=True,
    )

    count = _ingest(monkeypatch, strava, db, reauth_fn=lambda: "fresh-token")

    assert strava.list_calls == 2
    assert count == 1


# ---- heart rate zones: reserve-based, athlete-wide max ----

def test_time_in_zones_uses_heart_rate_reserve():
    from src.pipeline import calculate_time_in_zones
    # Max 196, rest 60: reserve shares 0.44, 0.66, 0.81 and 0.96.
    assert calculate_time_in_zones([120, 150, 170, 190], 196, 60) == [1, 1, 0, 1, 1]
    assert calculate_time_in_zones([], 196, 60) == [0, 0, 0, 0, 0]


def test_effectiveness_zones_use_the_athletes_max_not_the_activitys():
    db = _make_db()
    db.con.execute("UPDATE dim_activity SET max_heartrate = 173 WHERE activity_id = 100")
    db.con.execute("""
        INSERT INTO dim_activity (activity_id, athlete_id, type, start_date_local, moving_time, average_speed, max_heartrate)
        VALUES (101, 1, 'Run', '2026-01-02 08:00:00', 600, 4.0, 196)
    """)
    db.insert_streams(100, {'time': {'data': list(range(10))}, 'heartrate': {'data': [150] * 10}})

    run_analyze_effectiveness(db)

    zones = db.con.execute(
        "SELECT zone_1_sec, zone_2_sec, zone_3_sec, zone_4_sec, zone_5_sec FROM activity_effectiveness WHERE activity_id = 100"
    ).fetchone()
    # 150 bpm is 66% of reserve against the athlete's 196 max (zone 2); against
    # the activity's own 173 peak it would have landed in zone 4.
    assert zones == (0, 10, 0, 0, 0)


# ---- effectiveness is incremental and follows the heart rate profile ----

def test_effectiveness_only_recomputes_when_something_changed():
    db = _make_db()
    db.con.execute("UPDATE dim_activity SET max_heartrate = 196 WHERE activity_id = 100")
    db.insert_streams(100, {'time': {'data': list(range(10))}, 'heartrate': {'data': [150] * 10}})

    assert run_analyze_effectiveness(db) == 1
    assert run_analyze_effectiveness(db) == 0          # nothing new

    # A different heart rate profile invalidates every row.
    assert run_analyze_effectiveness(db, max_hr=190, resting_hr=50) == 1
    row = db.con.execute("SELECT hr_max_used, hr_rest_used FROM activity_effectiveness WHERE activity_id = 100").fetchone()
    assert row == (190, 50)
    assert run_analyze_effectiveness(db, max_hr=190, resting_hr=50) == 0

    # A re-synced activity (newer updated_at) is recomputed.
    db.con.execute("UPDATE dim_activity SET updated_at = now() + INTERVAL 1 MINUTE WHERE activity_id = 100")
    assert run_analyze_effectiveness(db, max_hr=190, resting_hr=50) == 1


# ---- best efforts pass ----

def test_best_efforts_pass_is_incremental():
    from src.pipeline import run_best_efforts
    db = _make_db()
    times = list(range(0, 1800))
    speeds = [3.0] * 1800
    speeds[600:1000] = [4.0] * 400
    db.insert_streams(100, {'time': {'data': times}, 'velocity_smooth': {'data': speeds}})

    assert run_best_efforts(db) == 1
    mile = db.con.execute("SELECT seconds FROM best_efforts WHERE activity_id = 100 AND distance_m = 1609.344").fetchone()[0]
    assert mile == pytest.approx(1609.344 / 4.0, abs=2)
    # 5.4 km run: no 10K or half rows.
    assert db.con.execute("SELECT count(*) FROM best_efforts WHERE activity_id = 100").fetchone()[0] == 2
    assert run_best_efforts(db) == 0


# ---- laps ----

def test_ingest_stores_laps_with_start_offsets(monkeypatch):
    db = _make_db()
    strava = _FakeStrava([{'id': 105, 'name': 'Lapped run', 'type': 'Run', 'start_date_local': '2026-01-05T08:00:00Z'}])
    _ingest(monkeypatch, strava, db)
    laps = db.con.execute("SELECT lap_index, start_offset, distance FROM activity_laps WHERE activity_id = 105 ORDER BY lap_index").fetchall()
    assert laps == [(1, 0, 6.0), (2, 2, 3.0)]
    assert db.con.execute("SELECT laps_fetched_at IS NOT NULL FROM dim_activity WHERE activity_id = 105").fetchone()[0]


def test_missing_laps_are_backfilled_once():
    from src.pipeline import run_fetch_missing_laps
    db = _make_db()
    db.insert_streams(100, {'time': {'data': [0, 1, 2]}, 'velocity_smooth': {'data': [3.0, 3.0, 3.0]}})
    strava = _FakeStrava([])
    assert run_fetch_missing_laps(strava, db, "token") == 1
    assert db.con.execute("SELECT count(*) FROM activity_laps WHERE activity_id = 100").fetchone()[0] == 2
    assert run_fetch_missing_laps(strava, db, "token") == 0   # marked fetched
