"""Imports FIT and GPX files for a user: parse, skip duplicates, write the
activity with its streams and laps, then run the analysis passes a Strava
sync runs so the new activities carry structure, load, zones and best
efforts. Also moves a file-only user's rows to their Strava id when they
connect later.
"""
import logging

from src.importers import parse_file, UnreadableFile
from src.pipeline import (
    ACTIVITY_CHILD_TABLES, StravaDB, run_analyze_effectiveness, run_analyze_training_load, run_best_efforts,
)
from src.structure_pass import run_detect_structure
from src.api.database import synthetic_athlete_id
from src.api.services.metrics import resolve_resting_hr

logger = logging.getLogger("race_planner.imports")

DUPLICATE_WINDOW_S = 60
SEX_CODES = {"male": "M", "female": "F"}


def ensure_athlete(db, user):
    """dim_activity references dim_athlete, so a file-only user needs a row.
    A Strava athlete's row is left to the sync."""
    if db.con.execute("SELECT 1 FROM dim_athlete WHERE athlete_id = ?", [user.athlete_id]).fetchone():
        return
    db.upsert_athlete({"id": user.athlete_id, "firstname": user.name, "sex": SEX_CODES.get((user.gender or "").lower()), "weight": user.weight})


def activity_near(con, athlete_id, start_date, window_s=DUPLICATE_WINDOW_S):
    """An existing activity of the athlete that starts within window_s of
    start_date (a Strava-style UTC string), or None."""
    return con.execute("""
        SELECT activity_id, name, device_name FROM dim_activity
        WHERE athlete_id = ? AND abs(epoch(start_date) - epoch(CAST(? AS TIMESTAMP))) <= ?
        LIMIT 1
    """, [athlete_id, start_date.replace("Z", ""), window_s]).fetchone()


def write_activity(db, user, parsed):
    summary = {**parsed["summary"], "athlete": {"id": user.athlete_id}}
    db.upsert_activity(summary)
    db.insert_streams(summary["id"], parsed["streams"])
    db.insert_laps(summary["id"], parsed["laps"], parsed["streams"]["time"]["data"])


def import_files(db, user, files):
    """files are (filename, bytes) pairs. Returns one result per file:
    imported (with the activity id), skipped (with why) or error."""
    ensure_athlete(db, user)
    results, imported = [], 0
    for filename, data in files:
        try:
            parsed = parse_file(filename, data)
        except UnreadableFile as error:
            results.append({"file": filename, "status": "error", "detail": str(error)})
            continue
        except Exception as error:
            logger.exception("Import of %s failed", filename)
            results.append({"file": filename, "status": "error", "detail": f"Could not read the file: {error}"})
            continue
        summary = parsed["summary"]
        if db.activity_exists(summary["id"]):
            results.append({"file": filename, "status": "skipped", "detail": "Already imported"})
            continue
        near = activity_near(db.con, user.athlete_id, summary["start_date"])
        if near:
            source = "imported earlier" if (near[2] or "").startswith("Imported") else "synced from Strava"
            results.append({"file": filename, "status": "skipped", "detail": f"Starts within a minute of '{near[1]}' ({source})"})
            continue
        write_activity(db, user, parsed)
        imported += 1
        results.append({"file": filename, "status": "imported", "activity_id": summary["id"], "name": summary["name"], "type": summary["type"]})
    if imported:
        run_detect_structure(db, user.athlete_id)
        run_analyze_effectiveness(db, user.athlete_id, resting_hr=resolve_resting_hr(user), max_hr=user.max_hr)
        run_best_efforts(db, user.athlete_id)
        run_analyze_training_load(db)
    return results


def move_athlete(db, old_id, athlete):
    """Moves every activity (with its child rows) and COROS night from one
    athlete key to the Strava athlete in the payload.

    DuckDB checks foreign keys against committed rows only, so a parent row
    cannot be removed in the transaction that removes its children. The rows
    are copied to move_* staging tables, removed child tables first with each
    step committed, and written back under the new key. If a step fails, the
    staging tables still hold the rows."""
    con, new_id = db.con, int(athlete["id"])
    db.upsert_athlete(athlete)
    con.execute("CREATE OR REPLACE TABLE move_dim_activity AS SELECT * FROM dim_activity WHERE athlete_id = ?", [old_id])
    for table in ACTIVITY_CHILD_TABLES:
        con.execute(f"CREATE OR REPLACE TABLE move_{table} AS SELECT * FROM {table} WHERE activity_id IN (SELECT activity_id FROM move_dim_activity)")
    for table in ACTIVITY_CHILD_TABLES:
        con.execute(f"DELETE FROM {table} WHERE activity_id IN (SELECT activity_id FROM move_dim_activity)")
    con.execute("DELETE FROM dim_activity WHERE athlete_id = ?", [old_id])

    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("INSERT INTO dim_activity SELECT * REPLACE (CAST(? AS UBIGINT) AS athlete_id) FROM move_dim_activity", [new_id])
        for table in ACTIVITY_CHILD_TABLES:
            con.execute(f"INSERT INTO {table} SELECT * FROM move_{table}")
        con.execute("INSERT OR REPLACE INTO coros_daily SELECT * REPLACE (CAST(? AS UBIGINT) AS athlete_id) FROM coros_daily WHERE athlete_id = ?", [new_id, old_id])
        con.execute("DELETE FROM coros_daily WHERE athlete_id = ?", [old_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    con.execute("DELETE FROM dim_athlete WHERE athlete_id = ?", [old_id])
    for table in ("dim_activity",) + ACTIVITY_CHILD_TABLES:
        con.execute(f"DROP TABLE move_{table}")


def move_to_strava_athlete(user_id, athlete, db_path):
    """Called from the Strava callback: rows a file-only user collected under
    the synthetic key move to their real Strava id."""
    old_id = synthetic_athlete_id(user_id)
    db = StravaDB(db_path=db_path)
    try:
        has_rows = db.con.execute(
            "SELECT 1 FROM dim_activity WHERE athlete_id = ? UNION ALL SELECT 1 FROM coros_daily WHERE athlete_id = ? LIMIT 1",
            [old_id, old_id],
        ).fetchone()
        if has_rows:
            move_athlete(db, old_id, athlete)
    finally:
        db.con.close()
