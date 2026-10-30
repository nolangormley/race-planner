"""Synced Strava activities: list, detail, streams, effectiveness, AI note, delete."""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
import numpy as np
import logging

from src.api.auth_routes import get_current_user
from src.api.database import User
from src.api.services.coach import get_ai_insight
from src.api.deps import get_db_connection
from src.api.services.metrics import (
    calculate_training_status_logic,
)

logger = logging.getLogger("race_planner.api")
router = APIRouter()


# Fixed allowlist for delete_activity's per-table DELETE loop below. `table`
# is never user input today, but interpolating it into an f-string SQL
# statement is exactly the shape a real injection would take if that ever
# changed — asserting against this constant enforces the safety in code
# rather than by convention alone.
# Every table keyed by activity_id, with dim_activity last: the others
# reference it, so it can only go once they are empty for the activity.
ACTIVITY_TABLES_TO_DELETE = (
    "stream_altitude", "stream_cadence", "stream_heartrate", "stream_moving",
    "stream_temperature", "stream_velocity", "stream_watts", "activity_effectiveness",
    "best_efforts", "activity_laps",
    "dim_activity"
)

def delete_activity_rows(con, activity_id):
    """Removes an activity from every warehouse table. Returns the tables
    that failed, so the caller can decide how bad that is."""
    failed_tables = []
    for table in ACTIVITY_TABLES_TO_DELETE:
        # table is never user input, but an f-string table name is exactly
        # the shape a SQL injection would take, so the allowlist is enforced
        # in code rather than by convention.
        assert table in ACTIVITY_TABLES_TO_DELETE
        try:
            con.execute(f"DELETE FROM {table} WHERE activity_id = ?", [activity_id])
        except Exception as e:
            print(f"Failed deleting from {table}: {e}")
            failed_tables.append(table)
    return failed_tables


@router.get("/api/activities")
def get_activities(limit: Optional[int] = 50, current_user: User = Depends(get_current_user)):
    # Scoped to the logged-in user's own athlete_id — this used to return
    # every activity from every athlete in the warehouse to any caller.
    con = get_db_connection()
    try:
        query = """
        SELECT
            da.*,
            ae.trimp_banister as training_load
        FROM dim_activity da
        LEFT JOIN activity_effectiveness ae ON da.activity_id = ae.activity_id
        WHERE da.athlete_id = ?
        ORDER BY da.start_date DESC
        LIMIT ?
        """
        activities = con.execute(query, [current_user.athlete_id, limit]).fetchall()
        columns = [desc[0] for desc in con.description]
        return [dict(zip(columns, row)) for row in activities]
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

# New endpoint to fetch activities for a specific user (athlete)
@router.get("/api/user-activities/{user_id}")
def get_user_activities(user_id: int, limit: Optional[int] = 100, current_user: User = Depends(get_current_user)):
    """Return recent activities for the given user (athlete).
    user_id here is the local User.id - we look up the warehouse athlete id.
    """
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this user's activities")

    athlete_id = current_user.athlete_id

    con = get_db_connection()
    try:
        query = """
        SELECT da.*, ae.trimp_banister as training_load
        FROM dim_activity da
        LEFT JOIN activity_effectiveness ae ON da.activity_id = ae.activity_id
        WHERE da.athlete_id = ?
        ORDER BY da.start_date DESC
        LIMIT ?
        """
        rows = con.execute(query, [athlete_id, limit]).fetchall()
        cols = [desc[0] for desc in con.description]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@router.get("/api/activities/{activity_id}")
def get_activity(activity_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        # athlete_id filter scopes this to activities the current user owns —
        # a mismatched activity_id looks identical to a nonexistent one (404),
        # so this doesn't reveal whether the id belongs to someone else.
        activity = con.execute(
            "SELECT * FROM dim_activity WHERE activity_id = ? AND athlete_id = ?",
            [activity_id, current_user.athlete_id]
        ).fetchone()
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")
        columns = [desc[0] for desc in con.description]
        return dict(zip(columns, activity))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@router.get("/api/activities/{activity_id}/streams")
def get_activity_streams(activity_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        # The stream_* tables have no athlete_id column of their own, so
        # ownership has to be checked against dim_activity separately.
        owner_row = con.execute(
            "SELECT 1 FROM dim_activity WHERE activity_id = ? AND athlete_id = ?",
            [activity_id, current_user.athlete_id]
        ).fetchone()
        if not owner_row:
            raise HTTPException(status_code=404, detail="Activity not found")

        # Join streams
        query = """
        SELECT 
            t.time_offset,
            v.value as velocity,
            h.value as heartrate,
            a.value as altitude,
            c.value as cadence,
            w.value as watts
        FROM (
            SELECT DISTINCT time_offset FROM stream_velocity WHERE activity_id = ?
            UNION SELECT DISTINCT time_offset FROM stream_heartrate WHERE activity_id = ?
        ) t
        LEFT JOIN stream_velocity v ON v.activity_id = ? AND v.time_offset = t.time_offset
        LEFT JOIN stream_heartrate h ON h.activity_id = ? AND h.time_offset = t.time_offset
        LEFT JOIN stream_altitude a ON a.activity_id = ? AND a.time_offset = t.time_offset
        LEFT JOIN stream_cadence c ON c.activity_id = ? AND c.time_offset = t.time_offset
        LEFT JOIN stream_watts w ON w.activity_id = ? AND w.time_offset = t.time_offset
        ORDER BY t.time_offset ASC
        """
        params = [activity_id] * 7
        df = con.execute(query, params).fetchdf()
        
        # Replace NaNs with None so JSON serialization works
        df = df.replace({np.nan: None})
        
        return df.to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@router.get("/api/activities/{activity_id}/effectiveness")
def get_activity_effectiveness(activity_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        # Check if table exists
        table_exists = con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'activity_effectiveness'").fetchone()
        if not table_exists:
            return {"error": "Activity effectiveness data not yet compiled."}

        query = """
        SELECT 
            da.*,
            ae.trimp_banister,
            ae.trimp_edwards,
            ae.efficiency_factor,
            ae.intensity_factor,
            ae.aerobic_decoupling,
            ae.zone_1_sec,
            ae.zone_2_sec,
            ae.zone_3_sec,
            ae.zone_4_sec,
            ae.zone_5_sec,
            ae.effectiveness_score
        FROM dim_activity da
        LEFT JOIN activity_effectiveness ae ON da.activity_id = ae.activity_id
        WHERE da.activity_id = ? AND da.athlete_id = ?
        """
        row = con.execute(query, [activity_id, current_user.athlete_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Activity not found")

        columns = [desc[0] for desc in con.description]
        data = dict(zip(columns, row))

        # Cleanup: convert NaNs or similar if needed (tuple fetch doesn't usually have them but good to be safe)
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Effectiveness error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@router.get("/api/activities/{activity_id}/ai_summary")
def get_activity_ai_summary(activity_id: int, current_user: User = Depends(get_current_user)):
    # This endpoint provides a coaching summary for a SPECIFIC completed activity
    con = get_db_connection()
    try:
        # Get activity and athlete details
        query = """
        SELECT
            da.*,
            ath.sex,
            ath.weight as weight_lbs,
            ae.trimp_banister as trimp,
            ae.efficiency_factor,
            ae.aerobic_decoupling
        FROM dim_activity da
        JOIN dim_athlete ath ON da.athlete_id = ath.athlete_id
        LEFT JOIN activity_effectiveness ae ON da.activity_id = ae.activity_id
        WHERE da.activity_id = ? AND da.athlete_id = ?
        """
        row = con.execute(query, [activity_id, current_user.athlete_id]).fetchone()
        if not row: raise HTTPException(status_code=404, detail="Activity not found")
        cols = [desc[0] for desc in con.description]
        stats = dict(zip(cols, row))

        # Get current training status (CTL/TSB) to give context
        # Reuse logic from calculate_training_status_logic but for specific athlete
        status = calculate_training_status_logic(stats['athlete_id'])
        if status:
            stats.update(status)

        mock_workout = {
            "name": stats.get('name'),
            "category": stats.get('type'),
            "description": stats.get('description', 'A completed effort.')
        }

        summary = get_ai_insight(stats, context="workout", workout=mock_workout)
        return {"summary": summary}
    except HTTPException:
        raise
    except Exception as e:
        print(f"AI summary error for activity {activity_id}: {e}")
        return {"error": str(e)}
    finally:
        con.close()

@router.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        owner_row = con.execute(
            "SELECT 1 FROM dim_activity WHERE activity_id = ? AND athlete_id = ?",
            [activity_id, current_user.athlete_id]
        ).fetchone()
        if not owner_row:
            raise HTTPException(status_code=404, detail="Activity not found")

        failed_tables = delete_activity_rows(con, activity_id)

        if "dim_activity" in failed_tables:
            # The activity record itself wasn't removed — this is the one
            # failure serious enough to fail the whole request, since the
            # caller would otherwise believe the activity is gone.
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete the activity record; some associated data may already be removed. Failed tables: {failed_tables}"
            )

        if failed_tables:
            return {
                "message": "Activity deleted, but some associated data failed to clean up.",
                "failed_tables": failed_tables,
            }

        return {"message": "Activity deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()


@router.get("/api/activities/{activity_id}/laps")
def get_activity_laps(activity_id: int, current_user: User = Depends(get_current_user)):
    """The watch's laps for an activity, in order, with start_offset in
    seconds from the start of the streams."""
    con = get_db_connection()
    try:
        owner_row = con.execute(
            "SELECT 1 FROM dim_activity WHERE activity_id = ? AND athlete_id = ?",
            [activity_id, current_user.athlete_id]
        ).fetchone()
        if not owner_row:
            raise HTTPException(status_code=404, detail="Activity not found")
        rows = con.execute("""
            SELECT lap_index, name, start_offset, elapsed_time, moving_time, distance, average_speed,
                   average_heartrate, max_heartrate, average_cadence, total_elevation_gain
            FROM activity_laps WHERE activity_id = ? ORDER BY lap_index
        """, [activity_id]).fetchall()
        columns = [desc[0] for desc in con.description]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        con.close()
