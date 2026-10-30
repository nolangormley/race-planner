from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.api.database import SYNTHETIC_ATHLETE_BASE, User, synthetic_athlete_id
from src.api.services.imports import import_files, move_to_strava_athlete
from src.pipeline import StravaDB

START = datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc)


def runner(**overrides):
    values = {"id": 5, "athlete_id": synthetic_athlete_id(5), "strava_athlete_id": None, "name": "File Runner",
              "gender": "Female", "weight": 58.0, "resting_hr": None, "max_hr": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def count(con, sql, params):
    return con.execute(sql, params).fetchone()[0]


def test_athlete_key_is_the_strava_id_or_a_synthetic_one():
    assert User(id=5).athlete_id == SYNTHETIC_ATHLETE_BASE + 5
    assert User(id=5, strava_athlete_id=424242).athlete_id == 424242


def test_import_writes_the_activity_and_runs_the_analysis(make_gpx):
    db = StravaDB(":memory:")
    user = runner()
    results = import_files(db, user, [("track.gpx", make_gpx(START, points=700, spacing_s=3))])

    assert [result["status"] for result in results] == ["imported"]
    activity_id = results[0]["activity_id"]
    con = db.con
    assert con.execute("SELECT athlete_id, type, device_name, laps_fetched_at IS NOT NULL FROM dim_activity WHERE activity_id = ?",
                       [activity_id]).fetchone() == (user.athlete_id, "Run", "Imported GPX", True)
    assert con.execute("SELECT firstname, sex FROM dim_athlete WHERE athlete_id = ?", [user.athlete_id]).fetchone() == ("File Runner", "F")
    assert count(con, "SELECT COUNT(*) FROM stream_heartrate WHERE activity_id = ?", [activity_id]) == 700
    assert count(con, "SELECT COUNT(*) FROM activity_effectiveness WHERE activity_id = ? AND trimp_banister > 0", [activity_id]) == 1
    assert count(con, "SELECT COUNT(*) FROM best_efforts WHERE activity_id = ?", [activity_id]) >= 2      # a mile and a 5K


def test_repeats_and_runs_already_synced_are_skipped(make_gpx):
    db = StravaDB(":memory:")
    user = runner(athlete_id=424242, strava_athlete_id=424242)
    db.upsert_athlete({"id": 424242, "firstname": "Strava"})
    db.upsert_activity({"id": 20062071596, "athlete": {"id": 424242}, "name": "Lunch Run", "type": "Run",
                        "start_date": "2026-10-26T16:00:00Z", "start_date_local": "2026-10-26T12:00:00Z", "distance": 5000.0})
    first_file = make_gpx(START, points=100)
    assert import_files(db, user, [("first.gpx", first_file)])[0]["status"] == "imported"

    results = import_files(db, user, [
        ("first.gpx", first_file),
        ("other-watch.gpx", make_gpx(START + timedelta(seconds=40), points=100, name="Same run, other watch")),
        ("strava-export.gpx", make_gpx(datetime(2026, 10, 26, 16, 0, 20, tzinfo=timezone.utc), points=100)),
        ("later.gpx", make_gpx(START + timedelta(minutes=5), points=100)),
        ("notes.txt", b"hello"),
    ])
    assert [result["status"] for result in results] == ["skipped", "skipped", "skipped", "imported", "error"]
    assert results[0]["detail"] == "Already imported"
    assert "imported earlier" in results[1]["detail"]
    assert "'Lunch Run' (synced from Strava)" in results[2]["detail"]
    assert count(db.con, "SELECT COUNT(*) FROM dim_activity WHERE athlete_id = ?", [424242]) == 3


def test_connecting_strava_moves_imported_rows_to_the_strava_id(make_gpx, tmp_path):
    path = str(tmp_path / "warehouse.duckdb")
    db = StravaDB(db_path=path)
    user = runner()
    activity_id = import_files(db, user, [("track.gpx", make_gpx(START, points=300))])[0]["activity_id"]
    db.con.execute("INSERT INTO coros_daily (athlete_id, day, rhr) VALUES (?, DATE '2026-10-25', 47)", [user.athlete_id])
    db.con.close()

    athlete = {"id": 424242, "firstname": "Strava", "sex": "F"}
    move_to_strava_athlete(user.id, athlete, path)
    move_to_strava_athlete(user.id, athlete, path)          # nothing left to move

    con = StravaDB(db_path=path).con
    assert con.execute("SELECT athlete_id FROM dim_activity WHERE activity_id = ?", [activity_id]).fetchone() == (424242,)
    assert count(con, "SELECT COUNT(*) FROM stream_heartrate WHERE activity_id = ?", [activity_id]) == 300
    assert count(con, "SELECT COUNT(*) FROM activity_effectiveness WHERE activity_id = ?", [activity_id]) == 1
    assert con.execute("SELECT athlete_id, rhr FROM coros_daily").fetchall() == [(424242, 47)]
    assert con.execute("SELECT athlete_id, firstname FROM dim_athlete").fetchall() == [(424242, "Strava")]
    assert not con.execute("SELECT table_name FROM duckdb_tables() WHERE table_name LIKE 'move_%'").fetchall()
