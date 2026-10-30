"""Warehouse pass for src.intervals: detects the structure of each run and
stores it in activity_structure (one row per run, kind NULL when nothing
was found) and activity_intervals (one row per rep)."""
from src.intervals import detect_structure


def load_streams(con, activity_id):
    """(times, speeds, altitudes, heart rates) for one activity; altitudes
    and heart rates are None when the activity has no such stream."""
    rows = con.execute("""
        SELECT sv.time_offset, sv.value, sa.value, sh.value
        FROM stream_velocity sv
        LEFT JOIN stream_altitude sa ON sa.activity_id = sv.activity_id AND sa.time_offset = sv.time_offset
        LEFT JOIN stream_heartrate sh ON sh.activity_id = sv.activity_id AND sh.time_offset = sv.time_offset
        WHERE sv.activity_id = ?
        ORDER BY sv.time_offset
    """, [activity_id]).fetchall()
    if not rows:
        return None
    times, speeds, altitudes, beats = (list(column) for column in zip(*rows))
    return (
        times,
        speeds,
        altitudes if any(value is not None for value in altitudes) else None,
        beats if any(value is not None for value in beats) else None,
    )


def detect_activity(con, activity_id):
    streams = load_streams(con, activity_id)
    if streams is None:
        return {"kind": None, "reps": [], "recoveries": []}
    return detect_structure(*streams)


def store_structure(con, activity_id, structure):
    con.execute("DELETE FROM activity_intervals WHERE activity_id = ?", [activity_id])
    con.execute("DELETE FROM activity_structure WHERE activity_id = ?", [activity_id])
    con.execute(
        "INSERT INTO activity_structure (activity_id, kind, rep_count) VALUES (?, ?, ?)",
        [activity_id, structure["kind"], len(structure["reps"])],
    )
    if structure["reps"]:
        con.executemany("""
            INSERT INTO activity_intervals (activity_id, rep_index, start_offset, end_offset, distance, moving_time,
                                            average_speed, average_heartrate, max_heartrate, recovery_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            [activity_id, rep["index"], rep["start_offset"], rep["end_offset"], rep["distance"], rep["moving_time"],
             rep["average_speed"], rep["average_heartrate"], rep["max_heartrate"], rep["recovery_time"]]
            for rep in structure["reps"]
        ])


def stored_structure(con, activity_id):
    """The stored structure of one activity as {"kind", "detected", "reps",
    "recoveries"}; detected is False before the pass has looked at it."""
    row = con.execute("SELECT kind FROM activity_structure WHERE activity_id = ?", [activity_id]).fetchone()
    if row is None:
        return {"kind": None, "detected": False, "reps": [], "recoveries": []}
    rows = con.execute("""
        SELECT rep_index, start_offset, end_offset, distance, moving_time, average_speed,
               average_heartrate, max_heartrate, recovery_time
        FROM activity_intervals WHERE activity_id = ? ORDER BY rep_index
    """, [activity_id]).fetchall()
    columns = ["index", "start_offset", "end_offset", "distance", "moving_time", "average_speed",
               "average_heartrate", "max_heartrate", "recovery_time"]
    reps = [dict(zip(columns, values)) for values in rows]
    recoveries = [
        {"after_rep": rep["index"], "start_offset": rep["end_offset"], "end_offset": rep["end_offset"] + rep["recovery_time"],
         "moving_time": rep["recovery_time"]}
        for rep in reps if rep["recovery_time"] is not None
    ]
    return {"kind": row[0], "detected": True, "reps": reps, "recoveries": recoveries}


def run_detect_structure(db, athlete_id, progress=None):
    """Detects and stores the structure of each of one athlete's runs whose
    streams the pass has not seen since the run was last synced. Returns how
    many it looked at."""
    con = db.con
    pending = [row[0] for row in con.execute("""
        SELECT da.activity_id FROM dim_activity da
        WHERE da.athlete_id = ? AND da.type = 'Run'
          AND EXISTS (SELECT 1 FROM stream_velocity sv WHERE sv.activity_id = da.activity_id)
          AND NOT EXISTS (
              SELECT 1 FROM activity_structure ast
              WHERE ast.activity_id = da.activity_id AND ast.detected_at >= da.updated_at
          )
        ORDER BY da.start_date_local
    """, [athlete_id]).fetchall()]
    for position, activity_id in enumerate(pending, start=1):
        if progress:
            progress('structure', done=position, total=len(pending))
        store_structure(con, activity_id, detect_activity(con, activity_id))
    return len(pending)
