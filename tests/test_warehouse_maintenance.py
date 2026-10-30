import importlib.util
import pathlib
from datetime import date

import duckdb

from src.pipeline import StravaDB
from src.api.routers.activities import ACTIVITY_TABLES_TO_DELETE, delete_activity_rows

REPO = pathlib.Path(__file__).resolve().parents[1]
ATHLETE_ID = 424242
ACTIVITY_ID = 9_000_001


def load_rebuild_script():
    spec = importlib.util.spec_from_file_location("rebuild_warehouse", REPO / "scripts" / "rebuild_warehouse.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_activity_with_children(con):
    con.execute("INSERT INTO dim_athlete (athlete_id, firstname) VALUES (?, 'Test')", [ATHLETE_ID])
    con.execute(
        "INSERT INTO dim_activity (activity_id, athlete_id, name, type, start_date_local, distance, moving_time) VALUES (?, ?, 'Run', 'Run', '2026-10-20 07:30:00', 8000, 2400)",
        [ACTIVITY_ID, ATHLETE_ID],
    )
    con.execute("INSERT INTO stream_heartrate (activity_id, time_offset, value) VALUES (?, 0, 140), (?, 1, 141)", [ACTIVITY_ID, ACTIVITY_ID])
    con.execute("INSERT INTO best_efforts (activity_id, distance_m, seconds) VALUES (?, 5000, 1450)", [ACTIVITY_ID])
    con.execute("INSERT INTO activity_laps (activity_id, lap_index, distance, moving_time) VALUES (?, 1, 1609.344, 420)", [ACTIVITY_ID])


def test_every_activity_keyed_table_is_cleaned_on_delete():
    warehouse = StravaDB(":memory:")
    keyed = {row[0] for row in warehouse.con.execute(
        "SELECT DISTINCT table_name FROM duckdb_columns() WHERE column_name = 'activity_id' AND database_name = current_database()"
    ).fetchall()}
    assert keyed - set(ACTIVITY_TABLES_TO_DELETE) == set()
    assert ACTIVITY_TABLES_TO_DELETE[-1] == "dim_activity"


def test_an_activity_with_laps_and_best_efforts_can_be_deleted():
    warehouse = StravaDB(":memory:")
    add_activity_with_children(warehouse.con)

    assert delete_activity_rows(warehouse.con, ACTIVITY_ID) == []
    for table in ACTIVITY_TABLES_TO_DELETE:
        assert warehouse.con.execute(f"SELECT COUNT(*) FROM {table} WHERE activity_id = ?", [ACTIVITY_ID]).fetchone()[0] == 0


def test_rebuild_copies_every_table(tmp_path):
    source = tmp_path / "warehouse.duckdb"
    warehouse = StravaDB(str(source))
    add_activity_with_children(warehouse.con)
    warehouse.con.execute("INSERT INTO coros_daily (athlete_id, day, rhr, hrv) VALUES (?, ?, 48, 61)", [ATHLETE_ID, date(2026, 10, 29)])
    warehouse.con.execute("INSERT INTO dim_workouts (workout_id, name) VALUES ('https://example.test/w/1', '6 x 800 m')")
    expected = {
        table: warehouse.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("dim_activity", "stream_heartrate", "best_efforts", "activity_laps", "coros_daily", "dim_workouts", "schema_migrations")
    }
    warehouse.con.close()

    load_rebuild_script().main(str(source))

    rebuilt = duckdb.connect(str(tmp_path / "warehouse.rebuilt.duckdb"), read_only=True)
    try:
        for table, count in expected.items():
            assert rebuilt.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count, table
    finally:
        rebuilt.close()
