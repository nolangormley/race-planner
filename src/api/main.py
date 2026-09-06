from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, date
import calendar
import json
import logging
import os
import secrets
import random
import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("race_planner.api")

# Fixed allowlist for delete_activity's per-table DELETE loop below. `table`
# is never user input today, but interpolating it into an f-string SQL
# statement is exactly the shape a real injection would take if that ever
# changed — asserting against this constant enforces the safety in code
# rather than by convention alone.
ACTIVITY_TABLES_TO_DELETE = (
    "stream_altitude", "stream_cadence", "stream_heartrate", "stream_moving",
    "stream_temperature", "stream_velocity", "stream_watts", "activity_effectiveness",
    "dim_activity"
)


import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from src.metrics import get_target_category
from src.pipeline import sync_athlete, StravaAuthError
from src.api.auth_routes import router as auth_router, get_current_user, get_current_user_from_session
from src.api.database import SessionLocal, User, Race, TrainingPlan, TrainingPlanEntry, ChatMessage
from src.api.schemas import (
    WorkoutCreate, SettingsUpdate, RaceCreate, ProfileUpdate,
    PlanEntryCreate, PlanEntryUpdate, BulkEntriesCreate, ChatMessageCreate,
)
from src.api.services.llm import call_llm, get_llm_provider, clean_json_response, LLMConfigError
from src.api.deps import get_db_connection, DEFAULT_MAX_HR
from src.api.services.metrics import (
    get_performance_standing,
    calculate_training_status_logic,
    calculate_vo2max,
    speed_to_pace_str,
    calculate_pace_zones,
)
from sqlalchemy.orm import Session

load_dotenv()
# Also try loading .env.dev, overriding variables from .env if both exist
if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env.dev')):
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env.dev'), override=True)
elif os.path.exists('.env.dev'):
    load_dotenv('.env.dev', override=True)

app = FastAPI()

app.include_router(auth_router)

# SECRET_KEY signs the session cookie (see SessionMiddleware below). Without
# it — or with a hardcoded/guessable value — anyone could forge a session.
# Falling back to a random key keeps local dev working without extra setup,
# but it means sessions won't survive a restart and *must* be overridden with
# a real, persistent value in any shared/production environment.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print(
        "WARNING: SECRET_KEY is not set in the environment. Using a random, "
        "process-local key — all sessions will be invalidated on restart. "
        "Set SECRET_KEY in your .env for any shared or production deployment."
    )

# https_only should be True in any deployment served over HTTPS (e.g. Render).
# It defaults to False so local http://localhost dev keeps working; set
# SECURE_COOKIES=true in production environments.
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() == "true"

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="session",
    same_site="lax",
    https_only=SECURE_COOKIES,
    max_age=60 * 60 * 24 * 14,  # 14 days
)

# CORS: this app's own frontend is server-rendered and only ever calls the
# API via same-origin relative fetch() calls, so there's no legitimate need
# for a wildcard. allow_origins=["*"] combined with allow_credentials=True is
# also invalid per the CORS spec, which made browsers/Starlette effectively
# reflect any Origin — i.e. any third-party site could make credentialed
# requests against this API. ALLOWED_ORIGINS lets a real deployment add its
# own domain without editing code.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup Templates and Static Files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Fallback in case they don't exist yet:
os.makedirs(os.path.join(BASE_DIR, "static"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "templates"), exist_ok=True)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ==========================================
# FRONTEND ROUTES
# ==========================================

@app.get("/")
def read_root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)

@app.get("/dashboard")
def dashboard(request: Request):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

@app.get("/workout_streams/{activity_id}")
def view_workout_streams(request: Request, activity_id: int):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("streams.html", {"request": request, "activity_id": activity_id, "user": user})

@app.get("/warehouse")
def view_warehouse(request: Request):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

# ==========================================
# CRUD FOR ACTIVITIES (Read, Delete)
# ==========================================

@app.get("/api/activities")
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
        activities = con.execute(query, [current_user.strava_athlete_id, limit]).fetchall()
        columns = [desc[0] for desc in con.description]
        return [dict(zip(columns, row)) for row in activities]
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

# New endpoint to fetch activities for a specific user (athlete)
@app.get("/api/user-activities/{user_id}")
def get_user_activities(user_id: int, limit: Optional[int] = 100, current_user: User = Depends(get_current_user)):
    """Return recent activities for the given user (athlete).
    user_id here is the local User.id - we look up the strava_athlete_id.
    """
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this user's activities")

    athlete_id = current_user.strava_athlete_id
    if not athlete_id:
        return []

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

@app.post("/api/sync-strava/{user_id}")
def sync_strava(user_id: int, current_user: User = Depends(get_current_user)):
    """Manually triggers a Strava sync for the logged-in user, using their
    own stored refresh token (captured at /strava-callback). This is the
    only thing that connects a web user's Strava login to the DuckDB
    warehouse — before this route existed, /strava-callback stored tokens
    that were never read anywhere else, and the warehouse could only be
    populated by running src/pipeline.py by hand against a single
    developer's STRAVA_REFRESH_TOKEN in .env (see
    agent_prompting/improvement_plan.md Section 2).

    This runs synchronously (it's a plain `def` route, so FastAPI runs it in
    its thread pool rather than blocking the event loop) — for an account
    with a lot of new activity this can take a while, and while it's
    running, other requests that need to write to the DuckDB warehouse may
    block or fail, since DuckDB only allows one read-write connection to a
    file at a time. Fine for a manual, deliberate "Sync Now" click on a
    small personal app; a background job queue would be the fix if this
    ever needs to run automatically/frequently or the warehouse ever
    supports many concurrent users.
    """
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to sync Strava data for this user")

    if not current_user.strava_refresh_token:
        raise HTTPException(status_code=400, detail="Strava isn't connected for this account yet — visit /strava-auth first.")

    try:
        new_refresh_token, new_activity_count = sync_athlete(current_user.strava_refresh_token)
    except StravaAuthError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Strava sync failed: {e}")

    if new_refresh_token:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == current_user.id).first()
            user.strava_refresh_token = new_refresh_token
            db.commit()
        finally:
            db.close()

    return {"message": "Sync complete", "new_activities": new_activity_count}

# Mileage summary endpoint – planned vs actual mileage for a user
@app.get("/api/mileage-summary/{user_id}")
def get_mileage_summary(user_id: int, start: str = None, end: str = None, current_user: User = Depends(get_current_user)):
    """Return planned vs actual mileage for the user.

    Args:
        user_id: User/athlete ID
        start: Start date filter (YYYY-MM-DD), defaults to start of current month
        end: End date filter (YYYY-MM-DD), defaults to end of current month
    """
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this user's mileage summary")

    if not current_user.strava_athlete_id:
        return {"planned_miles": 0, "actual_miles": 0, "planned_days": 0, "completed_days": 0, "rest_days": 0}
    athlete_id = current_user.strava_athlete_id

    # Determine date range
    if not (start and end):
        today = date.today()
        start = today.replace(day=1).isoformat()
        last_day = calendar.monthrange(today.year, today.month)[1]
        end = today.replace(day=last_day).isoformat()

    try:
        start_date = datetime.strptime(start, '%Y-%m-%d').date()
        end_date = datetime.strptime(end, '%Y-%m-%d').date()
    except ValueError:
        raise HTTPException(status_code=400, detail="start/end must be YYYY-MM-DD dates.")

    db = SessionLocal()
    con = get_db_connection()
    try:
        # TrainingPlan/TrainingPlanEntry live in Postgres (via SQLAlchemy),
        # not the DuckDB warehouse — this used to query them through the
        # DuckDB connection, which has no such tables and raised a Catalog
        # Error on every single call. See
        # agent_prompting/improvement_plan.md Section 6.
        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()

        planned = 0.0
        planned_days = 0
        completed_days = 0
        rest_days = 0
        if plan:
            # Planned mileage is the full plan's total (unfiltered by date,
            # matching the original behavior); day counts below ARE
            # filtered to the requested range.
            for entry in db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).all():
                if entry.approximate_distance:
                    try:
                        num = float(entry.approximate_distance.split()[0])
                        if 'km' in entry.approximate_distance.lower():
                            num = num * 0.621371
                        planned += num
                    except (ValueError, IndexError):
                        pass

            ranged_entries = db.query(TrainingPlanEntry).filter(
                TrainingPlanEntry.plan_id == plan.id,
                TrainingPlanEntry.date.between(start_date, end_date)
            ).all()
            for entry in ranged_entries:
                if entry.workout_type and entry.workout_type.lower() == 'rest':
                    rest_days += 1
                else:
                    planned_days += 1
                if entry.is_completed:
                    completed_days += 1

        # Actual mileage still comes from the DuckDB warehouse (distance in
        # meters -> miles).
        activities = con.execute(
            "SELECT distance FROM dim_activity WHERE athlete_id = ? AND start_date_local::DATE BETWEEN ? AND ?",
            [athlete_id, start, end]
        ).fetchall()
        actual_meters = sum(row[0] for row in activities if row[0])
        actual_miles = actual_meters * 0.000621371

        return {
            "planned_miles": round(planned, 2),
            "actual_miles": round(actual_miles, 2),
            "planned_days": planned_days,
            "completed_days": completed_days,
            "rest_days": rest_days
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Mileage summary error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()
        db.close()
@app.get("/api/activities/{activity_id}")
def get_activity(activity_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        # athlete_id filter scopes this to activities the current user owns —
        # a mismatched activity_id looks identical to a nonexistent one (404),
        # so this doesn't reveal whether the id belongs to someone else.
        activity = con.execute(
            "SELECT * FROM dim_activity WHERE activity_id = ? AND athlete_id = ?",
            [activity_id, current_user.strava_athlete_id]
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

@app.get("/api/activities/{activity_id}/streams")
def get_activity_streams(activity_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        # The stream_* tables have no athlete_id column of their own, so
        # ownership has to be checked against dim_activity separately.
        owner_row = con.execute(
            "SELECT 1 FROM dim_activity WHERE activity_id = ? AND athlete_id = ?",
            [activity_id, current_user.strava_athlete_id]
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

@app.get("/api/activities/{activity_id}/effectiveness")
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
        row = con.execute(query, [activity_id, current_user.strava_athlete_id]).fetchone()
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

@app.get("/api/activities/{activity_id}/ai_summary")
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
        row = con.execute(query, [activity_id, current_user.strava_athlete_id]).fetchone()
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

@app.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        owner_row = con.execute(
            "SELECT 1 FROM dim_activity WHERE activity_id = ? AND athlete_id = ?",
            [activity_id, current_user.strava_athlete_id]
        ).fetchone()
        if not owner_row:
            raise HTTPException(status_code=404, detail="Activity not found")

        # Delete from all tables sequentially, tracking failures instead of
        # unconditionally reporting success regardless of what happened.
        failed_tables = []
        for table in ACTIVITY_TABLES_TO_DELETE:
            # table is never user input today, but an f-string table name is
            # exactly the shape a real SQL injection would take if that ever
            # changed — assert against a fixed allowlist so the safety is
            # enforced in code, not just by "table always comes from this
            # literal list" convention.
            assert table in ACTIVITY_TABLES_TO_DELETE
            try:
                con.execute(f"DELETE FROM {table} WHERE activity_id = ?", [activity_id])
            except Exception as e:
                print(f"Failed deleting from {table}: {e}")
                failed_tables.append(table)

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

# ==========================================
# CRUD FOR WORKOUTS (Create, Read, Update, Delete)
# dim_workouts is a shared workout library with no per-user owner column in
# the current schema, so these routes only require *some* authenticated user
# (no per-resource ownership check is possible without a schema change).
# ==========================================
@app.get("/api/workouts")
def get_workouts(current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        workouts = con.execute("SELECT * FROM dim_workouts ORDER BY workout_id DESC").fetchall()
        columns = [desc[0] for desc in con.description]
        return [dict(zip(columns, row)) for row in workouts]
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@app.post("/api/workouts")
def create_workout(workout: WorkoutCreate, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        # Auto-incrementing workout_id via MAX + 1
        max_id_row = con.execute("SELECT MAX(workout_id) FROM dim_workouts").fetchone()
        next_id = (max_id_row[0] or 0) + 1 if max_id_row and max_id_row[0] is not None else 1
        
        con.execute(
            "INSERT INTO dim_workouts (workout_id, name, description, category, tags, url, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [next_id, workout.name, workout.description, workout.category, workout.tags, workout.url, workout.source]
        )
        return {"message": "Workout created successfully", "workout_id": next_id}
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@app.put("/api/workouts/{workout_id}")
def update_workout(workout_id: int, workout: WorkoutCreate, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        con.execute(
            "UPDATE dim_workouts SET name=?, description=?, category=?, tags=?, url=?, source=? WHERE workout_id=?",
            [workout.name, workout.description, workout.category, workout.tags, workout.url, workout.source, workout_id]
        )
        return {"message": "Workout updated successfully"}
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

@app.delete("/api/workouts/{workout_id}")
def delete_workout(workout_id: int, current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        con.execute("DELETE FROM dim_workouts WHERE workout_id=?", [workout_id])
        return {"message": "Workout deleted successfully"}
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()


# ==========================================
# ORIGINAL ENDPOINTS & LOGIC (Refactored DB close)
# ==========================================

@app.get("/users/")
def get_users(current_user: User = Depends(get_current_user)):
    con = get_db_connection()
    try:
        # Check if table exists
        try:
            users = con.execute("SELECT * FROM dim_athlete").fetchall()
        except duckdb.CatalogException:
            # dim_athlete genuinely doesn't exist yet (fresh warehouse) — a
            # real "no data" case.
            return []
        except Exception:
            logger.exception("Unexpected error querying dim_athlete")
            raise HTTPException(status_code=500, detail="Failed to query athletes.")


        columns = [desc[0] for desc in con.description]
        result = []
        for user in users:
            result.append(dict(zip(columns, user)))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        con.close()

def get_ai_insight(stats, context="status", workout=None):
    try:
        history_str = "\n".join([f"  - {h['date']}: Form (TSB): {h['form']}, Load (ATL): {h['fatigue']}" for h in stats.get('history', [])])

        standings = get_performance_standing(stats)

        if context == "status":
            prompt = (
                f"Explain the user's current training status based on these metrics and standings:\n"
                f"- Fitness (CTL): {stats.get('fitness_ctl')} (Standing: {standings['ctl_standing']})\n"
                f"- Fatigue (ATL): {stats.get('fatigue_atl')}\n"
                f"- Form (TSB): {stats.get('form_tsb')} (Standing: {standings['tsb_standing']})\n"
                f"- Estimated VO2 Max: {stats.get('latest_vo2_max', 'N/A')} (Standing: {standings['vo2_standing']})\n"
                f"- Acute-to-Chronic Workload Ratio (ACWR): {stats.get('acwr')}\n"
                f"- 7-Day Efficiency Factor: {stats.get('efficiency_factor_7d')}\n"
                f"- 7-Day Aerobic Decoupling: {stats.get('aerobic_decoupling_7d')}%\n\n"
                f"Give a HIGH-LEVEL EXECUTIVE SUMMARY of their readiness. "
                f"Focus on what action they need to take today. "
                f"Mention their ACWR (sweet spot 0.8-1.3). No fluff, no raw token dumps. Keep it professional and scannable."
            )
        elif context == "workout":
            prompt = (
                f"The athlete currently has a Form/TSB of {stats.get('form_tsb')} (Negative TSB means fatigued/in heavy training, positive means rested/tapering).\n"
                f"Their 7-day Aerobic Decoupling is {stats.get('aerobic_decoupling_7d')}% (Under 5% indicates good base aerobic fitness).\n"
                f"Their Estimated VO2 Max is: {stats.get('latest_vo2_max', 'N/A')}\n\n"
                f"We are recommending this workout: {workout.get('name')} ({workout.get('category')}).\n"
                f"Description: {workout.get('description')}\n\n"
                f"Briefly explain in 2-3 sentences why this specific workout is appropriate for their current training state, acting as their professional endurance coach. Talk directly to them."
            )

        weight_str = f"weighs {stats.get('weight_lbs')}lbs" if stats.get('weight_lbs') else "weighs 220lbs"
        sex_str = stats.get('sex', 'male')
        if sex_str == 'M': sex_str = 'male'
        elif sex_str == 'F': sex_str = 'female'

        user_race_type = stats.get('race_type')
        user_race_date = stats.get('race_date')
        user_goal_time = stats.get('goal_time')

        race_info = ""
        if user_race_type and user_race_date:
            race_info = f"training for a {user_race_type} on {user_race_date}"
            if user_goal_time:
                race_info += f" with a goal time of {user_goal_time}"
            race_info += "."

        system_prompt = (
            f"You are an expert running coach and one of your athletes is {race_info} The athlete is {sex_str}, and {weight_str}. "
            f"Use these rigid scientific benchmarks for context:\n"
            f"- CTL: <40 Developing, 40-70 Consistent, 70-100 Advanced, >100 Elite Pro.\n"
            f"- TSB: >15 Fresh (Peaking), -30 to -10 Optimal Training, <-30 High Overload.\n"
            f"Provide actionable advice as concise bullet points. NEVER contradict the user's current 'Standing' provided in the prompt. "
            f"CRITICAL CONSTRAINTS: "
            f"1) Recommending a MAXIMUM of ONE training action for the current day. "
            f"2) Do not hallucinate pseudo-science. Running depletes energy. Rest restores it. "
            f"3) Output plain text only. Do NOT use markdown or HTML tags. Use only simple hyphens (-) for bullet points. "
            f"4) KEEP IT EXTREMELY BRIEF. Provide exactly 1 short introductory sentence, followed by 2-3 short bullet points."
        )

        # Small model for groq here (fast, short answer) — get_ai_training_plan
        # and training_plan_chat deliberately use a bigger groq model since
        # they generate structured JSON plans, not a one-paragraph insight.
        model = "llama-3.1-8b-instant" if get_llm_provider() == "groq" else None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        try:
            # 30s timeout — previously this call had none at all (could hang
            # a request thread indefinitely); see
            # agent_prompting/improvement_plan.md Section 3.
            return call_llm(messages, model=model, timeout=30)
        except LLMConfigError as e:
            # Covers both "API key not set" and "unexpected response shape"
            # — the latter used to `return str(data)` here, i.e. a raw
            # provider response dict rendered as if it were coaching text.
            return str(e)

    except Exception as e:
        print(f"Error getting AI insight: {e}")
        return None

@app.get("/status/{user_id}")
def get_status(user_id: int, current_user: User = Depends(get_current_user)):
    # NOTE: user_id here is the Strava athlete_id, not User.id (see
    # agent_prompting/improvement_plan.md — the two are used inconsistently
    # across this file's routes).
    if user_id != current_user.strava_athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's status")

    status = calculate_training_status_logic(user_id)
    if not status:
        raise HTTPException(status_code=404, detail="No training data found for user")

    status['race_type'] = current_user.race_length
    status['race_date'] = str(current_user.race_date) if current_user.race_date else None
    status['goal_time'] = current_user.race_goal_time

    status['ai_insight'] = get_ai_insight(status, context="status")
    return status

@app.get("/recommend/{user_id}")
def get_recommendation(user_id: int, current_user: User = Depends(get_current_user)):
    # user_id here is the Strava athlete_id (see /status/{user_id} above).
    if user_id != current_user.strava_athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's recommendation")

    status = calculate_training_status_logic(user_id)
    if not status:
        raise HTTPException(status_code=404, detail="No training data found for user")
    
    tsb_val = status.get('form_tsb')
    tsb = float(tsb_val) if tsb_val is not None else 0.0
    
    target_category = get_target_category(tsb)
        
    con = get_db_connection()
    try:
        try:
            workouts = con.execute("SELECT name, description, url, category FROM dim_workouts WHERE category = ?", [target_category]).fetchall()
        except Exception as e:
            return {"error": "dim_workouts table not found. Run src/scrape_coros_workouts.py first."}

        if not workouts:
             fallback_map = {
                 "Anaerobic": "VO2Max",
                 "VO2Max": "Threshold", 
                 "Threshold": "Aerobic",
                 "Aerobic": "Recovery",
                 "Recovery": "Aerobic" 
             }
             orig_target = target_category
             target_category = fallback_map.get(target_category, "Aerobic")
             workouts = con.execute("SELECT name, description, url, category FROM dim_workouts WHERE category = ?", [target_category]).fetchall()
             
        if not workouts:
             workouts = con.execute("SELECT name, description, url, category FROM dim_workouts LIMIT 5").fetchall()
             if not workouts:
                return {"message": "No workouts found in library at all."}

        w = random.choice(workouts)
        
        w_dict = {
                "name": w[0],
                "description": w[1],
                "url": w[2],
                "category": w[3]
            }
        
        ai_reasoning = get_ai_insight(status, context="workout", workout=w_dict)
        
        return {
            "user_id": user_id,
            "current_tsb": tsb,
            "current_acwr": status.get("acwr"),
            "recommended_category": target_category,
            "latest_vo2_max": status.get("latest_vo2_max"),
        "ai_reasoning": ai_reasoning,
            "workout": w_dict
        }
    finally:
        con.close()

@app.get("/vo2max/{user_id}")
def get_vo2max(user_id: int, current_user: User = Depends(get_current_user)):
    # user_id here is the Strava athlete_id (see /status/{user_id} above).
    if user_id != current_user.strava_athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's VO2max")
    vo2max_data = calculate_vo2max(user_id)
    if not vo2max_data:
        raise HTTPException(status_code=404, detail="No stream data found to calculate VO2 Max for this user.")
    return vo2max_data

def get_ai_training_plan(stats, race_date: str, race_type: str, goal_time: Optional[str] = None, pace_zones: Optional[dict] = None):
    try:
        current_date_str = stats.get('date', datetime.now().strftime('%Y-%m-%d'))

        prompt = (
            f"The athlete is racing a {race_type} on {race_date}.\n"
        )
        if goal_time:
            prompt += f"Their goal time is {goal_time}.\n"
        else:
            prompt += "They have not provided a goal time. Please recommend a challenging but realistic goal time based on their current fitness level (especially VO2 max and Threshold pace) and include it in your response.\n"

        prompt += (
            f"Today's date is {current_date_str}.\n"
            f"Please create a daily training plan from tomorrow until the race date.\n\n"
            f"Current fitness stats to consider:\n"
            f"- Fitness (CTL): {stats.get('fitness_ctl', 'N/A')}\n"
            f"- Fatigue (ATL): {stats.get('fatigue_atl', 'N/A')}\n"
            f"- Form (TSB): {stats.get('form_tsb', 'N/A')}\n"
            f"- Estimated VO2 Max: {stats.get('latest_vo2_max', 'N/A')}\n"
            f"- 7-Day Efficiency Factor: {stats.get('efficiency_factor_7d', 'N/A')}\n"
            f"- 7-Day Aerobic Decoupling: {stats.get('aerobic_decoupling_7d', 'N/A')}%\n\n"
            f"Based on these metrics, tailor the difficulty and volume of the plan. "
            f"If fatigue is high, perhaps start with recovery. If fitness is high, challenge them appropriately.\n"
        )

        if pace_zones and "pace_zones_min_per_mile" in pace_zones:
            prompt += "\nUse the following exact pace zones based on their recent data when recommending how fast they should run:\n"
            for z_name, z_range in pace_zones["pace_zones_min_per_mile"].items():
                prompt += f"- {z_name}: {z_range}\n"
            prompt += "\nMake sure to explicitly mention these paces in the workout 'description' so the user knows what speed to run at. Just use the mean of the pace zone range when providing the pace in the description.\n\n"

        weight_str = f"weighs {stats.get('weight_lbs')}lbs" if stats.get('weight_lbs') else "weighs 220lbs"
        sex_str = stats.get('sex', 'male')
        if sex_str == 'M': sex_str = 'male'
        elif sex_str == 'F': sex_str = 'female'

        system_prompt = (
            f"You are an expert running coach. Your athlete is {sex_str} and {weight_str}. "
            "You must respond STRICTLY with a valid JSON object. Do not include markdown formatting like ```json or any text outside the JSON framework. "
            "The JSON object must have three keys:\n"
            "1. 'blurb': a short introductory and motivating message about the plan.\n"
            "2. 'recommended_goal_time': a string recommending a goal time based on their metrics. If they provided a goal time, just restate it.\n"
            "3. 'plan': a dictionary where keys are the dates in 'YYYY-MM-DD' format, and values are objects with keys: "
            "'type_of_workout' (e.g., Easy Run, Tempo, Long Run, Rest), 'training_focus' (the purpose of the workout), "
            "'approximate_distance' (e.g., '5 miles'), and 'description' (detailed instructions).\n"
            "CRITICAL TOKEN LIMIT: Do not generate more than 60 days of workouts at once. If the user asks for a plan longer than 8 weeks, generate the first 8 weeks and explain in your 'blurb' that you generated the first phase and they can ask you to generate the next phase later."
        )

        provider = get_llm_provider()
        if provider not in ("groq", "deepseek", "gemini"):
            return {"error": "This feature currently requires the LLM_PROVIDER to be set to groq, deepseek, or gemini in order to return properly formatted JSON."}

        model = "llama-3.3-70b-versatile" if provider == "groq" else None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        try:
            content = call_llm(messages, model=model, json_mode=True, max_tokens=8192, timeout=300)
        except LLMConfigError as e:
            return {"error": str(e)}
        except requests.exceptions.HTTPError as e:
            return {"error": str(e)}

        content_clean = clean_json_response(content)
        try:
            return json.loads(content_clean)
        except json.JSONDecodeError:
            return {"error": "Failed to parse JSON response from LLM", "raw_content": content_clean}

    except Exception as e:
        print(f"Error getting AI training plan: {e}")
        return {"error": str(e)}

@app.get("/schedule/{user_id}")
def get_race_schedule(user_id: int, race_date: str, race_type: str, goal_time: Optional[str] = None, current_user: User = Depends(get_current_user)):
    # user_id here is the Strava athlete_id (see /status/{user_id} above).
    if user_id != current_user.strava_athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's schedule")
    # Validations
    try:
        datetime.strptime(race_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid race_date format. Must be YYYY-MM-DD.")
        
    valid_race_types = ["5k", "5 mile", "10k", "10 mile", "half marathon", "marathon"]
    if race_type.lower() not in valid_race_types:
        raise HTTPException(status_code=400, detail=f"Invalid race_type. Must be one of: {', '.join(valid_race_types)}")
        
    status = calculate_training_status_logic(user_id)
    if not status:
        # Fallback to an empty stats dict if they have no training history
        status = {"user_id": user_id}
        
    pace_zones = calculate_pace_zones(user_id)
        
    plan = get_ai_training_plan(status, race_date, race_type, goal_time, pace_zones)
    
    if "error" in plan:
        raise HTTPException(status_code=500, detail=plan["error"])
        
    return {
        "user_id": user_id,
        "race_date": race_date,
        "race_type": race_type,
        "goal_time": goal_time,
        "pace_zones": pace_zones,
        "schedule": plan
    }

@app.get("/pace_zones/{user_id}")
def get_pace_zones(user_id: int, current_user: User = Depends(get_current_user)):
    # user_id here is the Strava athlete_id (see /status/{user_id} above).
    if user_id != current_user.strava_athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's pace zones")
    zones = calculate_pace_zones(user_id)
    if not zones:
        raise HTTPException(status_code=404, detail="No stream data found to calculate Pace Zones for this user.")
    return zones

@app.get("/analytics/{user_id}")
def get_analytics(user_id: int, current_user: User = Depends(get_current_user)):
    # user_id here is the Strava athlete_id (see /status/{user_id} above).
    if user_id != current_user.strava_athlete_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this athlete's analytics")
    con = get_db_connection()
    try:
        # Derive HR max from athlete's recorded data (fallback DEFAULT_MAX_HR)
        try:
            hr_max_row = con.execute(
                "SELECT MAX(max_heartrate) FROM dim_activity WHERE athlete_id = ? AND max_heartrate > 100",
                [user_id]
            ).fetchone()
            hr_max = float(hr_max_row[0]) if hr_max_row and hr_max_row[0] else float(DEFAULT_MAX_HR)
        except Exception:
            logger.exception(f"Failed to look up max_heartrate for athlete {user_id}; falling back to default.")
            hr_max = float(DEFAULT_MAX_HR)

        hr_query = """
        SELECT 
            CASE 
                WHEN value < ? * 0.60 THEN 'Zone 1 (Recovery)'
                WHEN value < ? * 0.70 THEN 'Zone 2 (Aerobic)'
                WHEN value < ? * 0.80 THEN 'Zone 3 (Tempo)'
                WHEN value < ? * 0.90 THEN 'Zone 4 (Threshold)'
                ELSE 'Zone 5 (Anaerobic)'
            END as zone,
            COUNT(*) as secs
        FROM stream_heartrate h
        JOIN dim_activity a ON h.activity_id = a.activity_id
        WHERE a.athlete_id = ?
        GROUP BY 1 ORDER BY 1
        """
        hr_data = con.execute(hr_query, [hr_max, hr_max, hr_max, hr_max, user_id]).fetchall()
        
        load_query = """
        SELECT type, SUM(distance) as total_dist
        FROM dim_activity
        WHERE athlete_id = ? AND start_date_local >= current_date - interval '30 days'
        GROUP BY type
        """
        load_data = con.execute(load_query, [user_id]).fetchall()
        
        return {
            "hr_zones": [{"zone": row[0], "time_seconds": row[1]} for row in hr_data],
            "load_dist": [{"type": row[0], "distance": row[1]} for row in load_data]
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        con.close()

@app.post("/settings/{user_id}")
def update_settings(user_id: int, settings: SettingsUpdate, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify these settings")
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="User not found")

    try:
        if settings.race_date:
            user.race_date = datetime.strptime(settings.race_date, '%Y-%m-%d').date()
        user.race_length = settings.race_length
        user.race_goal_time = settings.race_goal_time
        db.commit()
        return {"message": "Settings updated"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()

# ---- RACE CALENDAR ENDPOINTS ----

@app.get("/api/races/{user_id}")
def get_user_races(user_id: int, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view these races")
    db = SessionLocal()
    try:
        races = db.query(Race).filter(Race.user_id == user_id).order_by(Race.race_date.asc()).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "race_length": r.race_length,
                "race_date": str(r.race_date),
                "goal_time": r.goal_time
            }
            for r in races
        ]
    finally:
        db.close()

@app.post("/api/races/{user_id}")
def create_user_race(user_id: int, race_data: RaceCreate, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to create a race for this user")
    db = SessionLocal()
    try:
        r_date = datetime.strptime(race_data.race_date, '%Y-%m-%d').date()
        new_race = Race(
            user_id=user_id,
            name=race_data.name,
            race_length=race_data.race_length,
            race_date=r_date,
            goal_time=race_data.goal_time
        )
        db.add(new_race)
        db.commit()
        db.refresh(new_race)
        return {"message": "Race created", "id": new_race.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()

@app.delete("/api/races/{race_id}")
def delete_race(race_id: int, current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        race = db.query(Race).filter(Race.id == race_id).first()
        # 404 for both "doesn't exist" and "isn't yours" so this endpoint
        # doesn't confirm the existence of other users' race ids.
        if not race or race.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Race not found")
        db.delete(race)
        db.commit()
        return {"message": "Race deleted"}
    finally:
        db.close()


# ==========================================
# PROFILE PAGE
# ==========================================

@app.get("/profile")
def profile_page(request: Request):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


@app.post("/profile/{user_id}")
def update_profile(user_id: int, profile: ProfileUpdate, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this profile")
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        db.close()
        raise HTTPException(status_code=404, detail="User not found")
    try:
        # Check email uniqueness
        if profile.email != user.email:
            existing = db.query(User).filter(User.email == profile.email).first()
            if existing:
                raise HTTPException(status_code=400, detail="Email already in use by another account.")
        user.name = profile.name
        user.email = profile.email
        if profile.date_of_birth:
            user.date_of_birth = datetime.strptime(profile.date_of_birth, '%Y-%m-%d').date()
        if profile.gender:
            user.gender = profile.gender
        if profile.height is not None:
            user.height = profile.height
        if profile.weight is not None:
            user.weight = profile.weight
        db.commit()
        return {"message": "Profile updated"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


# ==========================================
# TRAINING CALENDAR PAGE
# ==========================================

@app.get("/training-calendar")
def training_calendar_page(request: Request):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("training_calendar.html", {"request": request, "user": user})


# ==========================================
# TRAINING PLAN CRUD ENDPOINTS
# ==========================================

def _plan_to_dict(plan):
    return {
        "id": plan.id,
        "user_id": plan.user_id,
        "name": plan.name,
        "race_id": plan.race_id,
        "created_at": str(plan.created_at),
        "updated_at": str(plan.updated_at),
    }


def _entry_to_dict(e):
    return {
        "id": e.id,
        "plan_id": e.plan_id,
        "date": str(e.date),
        "workout_type": e.workout_type,
        "training_focus": e.training_focus,
        "approximate_distance": e.approximate_distance,
        "description": e.description,
        "is_completed": e.is_completed,
        "strava_activity_id": e.strava_activity_id,
    }


@app.get("/api/training-plan/{user_id}")
def get_training_plan(user_id: int, current_user: User = Depends(get_current_user)):
    """Get the active training plan for a user including all entries and chat history."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this training plan")
    db = SessionLocal()
    try:
        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()
        if not plan:
            return {"plan": None, "entries": [], "chat_messages": []}
        entries = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).order_by(TrainingPlanEntry.date).all()
        messages = db.query(ChatMessage).filter(ChatMessage.plan_id == plan.id).order_by(ChatMessage.id).all()
        return {
            "plan": _plan_to_dict(plan),
            "entries": [_entry_to_dict(e) for e in entries],
            "chat_messages": [{"id": m.id, "role": m.role, "content": m.content, "created_at": str(m.created_at)} for m in messages],
        }
    finally:
        db.close()


@app.post("/api/training-plan/{user_id}")
def create_or_replace_training_plan(user_id: int, body: BulkEntriesCreate, current_user: User = Depends(get_current_user)):
    """Replace the user's training plan with a new set of entries (AI or manual bulk-add)."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this training plan")
    db = SessionLocal()
    try:
        old_plans = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).all()
        for p in old_plans:
            db.delete(p)
        db.commit()

        plan = TrainingPlan(user_id=user_id, name=body.name or "My Training Plan", race_id=body.race_id)
        db.add(plan)
        db.commit()
        db.refresh(plan)

        for entry_data in body.entries:
            entry = TrainingPlanEntry(
                plan_id=plan.id,
                date=datetime.strptime(entry_data.date, '%Y-%m-%d').date(),
                workout_type=entry_data.workout_type,
                training_focus=entry_data.training_focus,
                approximate_distance=entry_data.approximate_distance,
                description=entry_data.description,
                is_completed=entry_data.is_completed or False,
                strava_activity_id=entry_data.strava_activity_id,
            )
            db.add(entry)
        db.commit()
        return {"message": "Plan created", "plan_id": plan.id, "entry_count": len(body.entries)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@app.post("/api/training-plan/{user_id}/entry")
def add_plan_entry(user_id: int, entry_data: PlanEntryCreate, current_user: User = Depends(get_current_user)):
    """Add a single entry (day) to the user's plan."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this training plan")
    db = SessionLocal()
    try:
        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()
        if not plan:
            plan = TrainingPlan(user_id=user_id, name="My Training Plan")
            db.add(plan)
            db.commit()
            db.refresh(plan)
        entry = TrainingPlanEntry(
            plan_id=plan.id,
            date=datetime.strptime(entry_data.date, '%Y-%m-%d').date(),
            workout_type=entry_data.workout_type,
            training_focus=entry_data.training_focus,
            approximate_distance=entry_data.approximate_distance,
            description=entry_data.description,
            is_completed=entry_data.is_completed or False,
            strava_activity_id=entry_data.strava_activity_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return _entry_to_dict(entry)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@app.put("/api/training-plan/entry/{entry_id}")
def update_plan_entry(entry_id: int, update: PlanEntryUpdate, current_user: User = Depends(get_current_user)):
    """Update a specific entry."""
    db = SessionLocal()
    try:
        # Ownership check via TrainingPlanEntry -> TrainingPlan.user_id. 404
        # (not 403) for a mismatch too, so this doesn't confirm the existence
        # of another user's entry ids.
        entry = (
            db.query(TrainingPlanEntry)
            .join(TrainingPlan, TrainingPlanEntry.plan_id == TrainingPlan.id)
            .filter(TrainingPlanEntry.id == entry_id, TrainingPlan.user_id == current_user.id)
            .first()
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        if update.workout_type is not None:
            entry.workout_type = update.workout_type
        if update.training_focus is not None:
            entry.training_focus = update.training_focus
        if update.approximate_distance is not None:
            entry.approximate_distance = update.approximate_distance
        if update.description is not None:
            entry.description = update.description
        if update.is_completed is not None:
            entry.is_completed = update.is_completed
        if update.strava_activity_id is not None:
            entry.strava_activity_id = update.strava_activity_id
        db.commit()
        return _entry_to_dict(entry)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@app.delete("/api/training-plan/entry/{entry_id}")
def delete_plan_entry(entry_id: int, current_user: User = Depends(get_current_user)):
    """Delete a specific entry."""
    db = SessionLocal()
    try:
        entry = (
            db.query(TrainingPlanEntry)
            .join(TrainingPlan, TrainingPlanEntry.plan_id == TrainingPlan.id)
            .filter(TrainingPlanEntry.id == entry_id, TrainingPlan.user_id == current_user.id)
            .first()
        )
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        db.delete(entry)
        db.commit()
        return {"message": "Entry deleted"}
    finally:
        db.close()


@app.post("/api/training-plan/{user_id}/chat")
def training_plan_chat(user_id: int, msg: ChatMessageCreate, current_user: User = Depends(get_current_user)):
    """Send a message to the AI to modify the training plan and persist the conversation."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this training plan")
    db = SessionLocal()
    try:
        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()
        if not plan:
            plan = TrainingPlan(user_id=user_id, name="My Training Plan")
            db.add(plan)
            db.commit()
            db.refresh(plan)

        entries = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).order_by(TrainingPlanEntry.date).all()
        entries_summary = "\n".join(
            [f"  - {str(e.date)}: {e.workout_type} ({e.approximate_distance or 'N/A'}) — {e.description or ''}" for e in entries]
        ) if entries else "  (No entries yet — this is a blank plan.)"

        history = db.query(ChatMessage).filter(ChatMessage.plan_id == plan.id).order_by(ChatMessage.id.desc()).limit(20).all()
        history = list(reversed(history))

        user = db.query(User).filter(User.id == user_id).first()
        user_info = ""
        if user:
            user_info = f"Athlete: {user.name}, Gender: {user.gender or 'N/A'}, Weight: {user.weight or 'N/A'} kg."
            if user.race_date and user.race_length:
                user_info += f" Racing a {user.race_length} on {user.race_date}."

        today_str = datetime.now().strftime('%Y-%m-%d')
        system_prompt = (
            f"You are an expert running coach AI assistant helping to build and modify a training calendar. {user_info}\n"
            f"Today's date is {today_str}.\n"
            f"The current training plan has these entries:\n{entries_summary}\n\n"
            "When the user asks you to modify the plan, respond with a JSON object that has:\n"
            "1. 'message': a friendly conversational reply explaining what you've done or recommending changes.\n"
            "2. 'plan_updates': (optional) an object where keys are 'YYYY-MM-DD' dates and values are objects with keys: "
            "'workout_type', 'training_focus', 'approximate_distance', 'description'. "
            "Only include dates you are adding or changing. To remove a day, set 'workout_type' to 'Delete'.\n"
            "3. 'replace_all': (optional) boolean — if true, the full new plan will be in 'full_plan' instead of 'plan_updates'.\n"
            "4. 'full_plan': (optional) same structure as plan_updates but represents the complete new plan.\n"
            "If the user is just chatting (no plan changes needed), only include 'message'.\n"
            "CRITICAL TOKEN LIMIT: Do not generate more than 60 days of workouts at once. If the user asks for a plan longer than 8 weeks, generate the first 8 weeks and explain in your message that you generated the first phase and they can ask you to generate the next phase.\n"
            "CRITICAL: Always respond with valid JSON only. Do not wrap the JSON in ```json blocks or markdown. Output raw JSON."
        )

        messages_payload = [{"role": "system", "content": system_prompt}]
        for h in history:
            messages_payload.append({"role": h.role, "content": h.content})
        messages_payload.append({"role": "user", "content": msg.message})

        user_msg = ChatMessage(plan_id=plan.id, role="user", content=msg.message)
        db.add(user_msg)
        db.commit()

        provider = get_llm_provider()
        if provider not in ("groq", "deepseek", "gemini"):
            ai_reply = "AI chat requires LLM_PROVIDER=groq, deepseek, or gemini to be set."
            ai_msg = ChatMessage(plan_id=plan.id, role="assistant", content=ai_reply)
            db.add(ai_msg)
            db.commit()
            return {"message": ai_reply, "plan_updates": None, "entries": [_entry_to_dict(e) for e in entries]}

        model = "llama-3.3-70b-versatile" if provider == "groq" else None
        try:
            content = call_llm(messages_payload, model=model, json_mode=True, max_tokens=8192, timeout=300)
        except LLMConfigError as e:
            raise HTTPException(status_code=500, detail=str(e))

        content_clean = clean_json_response(content)

        try:
            parsed = json.loads(content_clean)
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON. Raw content: {content}")
            ai_reply = "Sorry, I couldn't process that properly."
            ai_msg = ChatMessage(plan_id=plan.id, role="assistant", content=ai_reply)
            db.add(ai_msg)
            db.commit()
            return {"message": ai_reply, "entries": [_entry_to_dict(e) for e in db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).order_by(TrainingPlanEntry.date).all()]}

        ai_reply = parsed.get("message", "Done.")
        ai_msg = ChatMessage(plan_id=plan.id, role="assistant", content=ai_reply)
        db.add(ai_msg)
        db.commit()

        if parsed.get("replace_all") and "full_plan" in parsed:
            db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).delete()
            db.commit()
            for date_str, w in parsed["full_plan"].items():
                if w.get("workout_type", "").lower() == "delete":
                    continue
                try:
                    entry = TrainingPlanEntry(
                        plan_id=plan.id,
                        date=datetime.strptime(date_str, '%Y-%m-%d').date(),
                        workout_type=w.get("workout_type"),
                        training_focus=w.get("training_focus"),
                        approximate_distance=w.get("approximate_distance"),
                        description=w.get("description"),
                    )
                    db.add(entry)
                except Exception:
                    pass
            db.commit()

        elif parsed.get("plan_updates"):
            for date_str, w in parsed["plan_updates"].items():
                try:
                    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                    if w.get("workout_type", "").lower() == "delete":
                        db.query(TrainingPlanEntry).filter(
                            TrainingPlanEntry.plan_id == plan.id,
                            TrainingPlanEntry.date == date_obj
                        ).delete()
                    else:
                        existing = db.query(TrainingPlanEntry).filter(
                            TrainingPlanEntry.plan_id == plan.id,
                            TrainingPlanEntry.date == date_obj
                        ).first()
                        if existing:
                            existing.workout_type = w.get("workout_type", existing.workout_type)
                            existing.training_focus = w.get("training_focus", existing.training_focus)
                            existing.approximate_distance = w.get("approximate_distance", existing.approximate_distance)
                            existing.description = w.get("description", existing.description)
                        else:
                            entry = TrainingPlanEntry(
                                plan_id=plan.id,
                                date=date_obj,
                                workout_type=w.get("workout_type"),
                                training_focus=w.get("training_focus"),
                                approximate_distance=w.get("approximate_distance"),
                                description=w.get("description"),
                            )
                            db.add(entry)
                except Exception:
                    pass
            db.commit()

        all_entries = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id == plan.id).order_by(TrainingPlanEntry.date).all()
        return {
            "message": ai_reply,
            "entries": [_entry_to_dict(e) for e in all_entries],
        }

    except HTTPException:
        # Deliberate, safe-to-show errors raised above (e.g. "GROQ_API_KEY
        # not set") — pass through as-is rather than falling into the
        # generic handler below and losing the specific message.
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Training plan chat error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        db.close()


@app.delete("/api/training-plan/{user_id}/chat")
def clear_training_plan_chat(user_id: int, current_user: User = Depends(get_current_user)):
    """Clear the AI chat history for the user's training plan."""
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this training plan")
    db = SessionLocal()
    try:
        plan = db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).order_by(TrainingPlan.id.desc()).first()
        if not plan:
            return {"message": "No active plan"}
            
        db.query(ChatMessage).filter(ChatMessage.plan_id == plan.id).delete()
        db.commit()
        return {"message": "Chat history cleared"}
    except Exception as e:
        db.rollback()
        logger.exception("Clear training plan chat error")
        raise HTTPException(status_code=500, detail="Internal server error.")
    finally:
        db.close()
