"""Rebuilds the DuckDB warehouse into a fresh file with a clean schema.

    python scripts/rebuild_warehouse.py data/strava_warehouse.duckdb

Writes <path>.rebuilt.duckdb next to the original and leaves the original
untouched; swap the files yourself once the printed row counts match. Stop
the app first: DuckDB allows one process in a file at a time.

Why: stream tables that were once migrated with CREATE ... RENAME kept a
foreign-key reference to their old *_new name inside DuckDB, and every
DELETE on them fails at commit with "Table with name stream_x_new does not
exist". Creating the schema from scratch (src.pipeline.StravaDB) and
copying the rows over clears it.
"""
import os
import sys

import duckdb

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.pipeline import StravaDB  # noqa: E402

# Copied first so foreign keys hold while the other tables fill.
PARENT_TABLES = ("dim_athlete", "dim_activity")


def tables_to_copy(con):
    """Every table the current schema creates, parents first. Read from the
    new file rather than listed by hand, so a table added later is never
    left behind. schema_migrations is merged separately."""
    names = {row[0] for row in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = current_database() AND schema_name = 'main'"
    ).fetchall()}
    names.discard("schema_migrations")
    parents = [name for name in PARENT_TABLES if name in names]
    return parents + sorted(names - set(parents))


def main(source):
    target = source.replace(".duckdb", ".rebuilt.duckdb")
    if os.path.exists(target):
        sys.exit(f"{target} already exists; remove it first.")
    db = StravaDB(db_path=target)
    con = db.con
    tables = tables_to_copy(con)
    con.execute(f"ATTACH '{source}' AS old (READ_ONLY)")
    existing = {row[0] for row in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_catalog = 'old' AND table_schema = 'main'"
    ).fetchall()}
    for table in tables:
        if table not in existing:
            print(f"{table}: not in source, skipped")
            continue
        columns = [row[0] for row in con.execute(f"DESCRIBE {table}").fetchall()]
        old_columns = {row[0] for row in con.execute(f"DESCRIBE old.{table}").fetchall()}
        shared = ", ".join(column for column in columns if column in old_columns)
        con.execute(f"INSERT INTO {table} ({shared}) SELECT {shared} FROM old.{table}")
        count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        old_count = con.execute(f"SELECT count(*) FROM old.{table}").fetchone()[0]
        print(f"{table}: {count} rows" + ("" if count == old_count else f" (source had {old_count})"))
    con.execute("INSERT OR IGNORE INTO schema_migrations (name) SELECT name FROM old.schema_migrations")
    con.execute("DETACH old")
    con.close()
    print(f"\nwrote {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
