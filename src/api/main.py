from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import random
import requests
from dotenv import load_dotenv
from sklearn.linear_model import LinearRegression

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from src.metrics import calculate_ctl_atl, calculate_tsb, get_target_category, calculate_vo2max_from_df, clean_val, calculate_acwr
from src.api.auth_routes import router as auth_router
from src.api.database import SessionLocal, User, Race, TrainingPlan, TrainingPlanEntry, ChatMessage
from sqlalchemy.orm import Session

load_dotenv()
# Also try loading .env.dev, overriding variables from .env if both exist
if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env.dev')):
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env.dev'), override=True)
elif os.path.exists('.env.dev'):
    load_dotenv('.env.dev', override=True)

app = FastAPI()

app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

DB_PATH = os.getenv('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'strava_warehouse.duckdb'))

def get_db_connection():
    try:
        # read_only=False allows API to perform CRUD operations.
        # Connections must be closed quickly to prevent holding the writer lock.
        return duckdb.connect(DB_PATH, read_only=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")

# ==========================================
# FRONTEND ROUTES
# ==========================================

@app.get("/")
def read_root(request: Request):
    user_id = request.cookies.get("user_id")
    if user_id:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)

@app.get("/dashboard")
def dashboard(request: Request):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    db = SessionLocal()
    user = db.query(User).filter(User.id == int(user_id)).first()
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
        
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

@app.get("/workout_streams/{activity_id}")
def view_workout_streams(request: Request, activity_id: int):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    db = SessionLocal()
    user = db.query(User).filter(User.id == int(user_id)).first()
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("streams.html", {"request": request, "activity_id": activity_id, "user": user})

@app.get("/warehouse")
def view_warehouse(request: Request):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request})

# ==========================================
# CRUD FOR ACTIVITIES (Read, Delete)
# ==========================================

@app.get("/api/activities")
def get_activities(limit: Optional[int] = 50):
    con = get_db_connection()
    try:
        query = """
        SELECT 
            da.*, 
            ae.trimp_banister as training_load
        FROM dim_activity da
        LEFT JOIN activity_effectiveness ae ON da.activity_id = ae.activity_id
        ORDER BY da.start_date DESC
        LIMIT ?
        """
        activities = con.execute(query, [limit]).fetchall()
        columns = [desc[0] for desc in con.description]
        return [dict(zip(columns, row)) for row in activities]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

@app.get("/api/activities/{activity_id}")
def get_activity(activity_id: int):
    con = get_db_connection()
    try:
        activity = con.execute("SELECT * FROM dim_activity WHERE activity_id = ?", [activity_id]).fetchone()
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found")
        columns = [desc[0] for desc in con.description]
        return dict(zip(columns, activity))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

@app.get("/api/activities/{activity_id}/streams")
def get_activity_streams(activity_id: int):
    con = get_db_connection()
    try:
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

@app.get("/api/activities/{activity_id}/effectiveness")
def get_activity_effectiveness(activity_id: int):
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
        WHERE da.activity_id = ?
        """
        row = con.execute(query, [activity_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Activity not found")
        
        columns = [desc[0] for desc in con.description]
        data = dict(zip(columns, row))
        
        # Cleanup: convert NaNs or similar if needed (tuple fetch doesn't usually have them but good to be safe)
        return data
    except Exception as e:
        print(f"Effectiveness error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

@app.get("/api/activities/{activity_id}/ai_summary")
def get_activity_ai_summary(activity_id: int):
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
        WHERE da.activity_id = ?
        """
        row = con.execute(query, [activity_id]).fetchone()
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
    except Exception as e:
        print(f"AI summary error for activity {activity_id}: {e}")
        return {"error": str(e)}
    finally:
        con.close()

@app.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int):
    con = get_db_connection()
    try:
        # Delete from all tables sequentially.
        tables = [
            "stream_altitude", "stream_cadence", "stream_heartrate", "stream_moving",
            "stream_temperature", "stream_velocity", "stream_watts", "activity_effectiveness",
            "dim_activity"
        ]
        for table in tables:
            try:
                con.execute(f"DELETE FROM {table} WHERE activity_id = ?", [activity_id])
            except Exception as e:
                print(f"Failed deleting from {table}: {e}")
        return {"message": "Activity deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

# ==========================================
# CRUD FOR WORKOUTS (Create, Read, Update, Delete)
# ==========================================
class WorkoutCreate(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = "Manual"

@app.get("/api/workouts")
def get_workouts():
    con = get_db_connection()
    try:
        workouts = con.execute("SELECT * FROM dim_workouts ORDER BY workout_id DESC").fetchall()
        columns = [desc[0] for desc in con.description]
        return [dict(zip(columns, row)) for row in workouts]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

@app.post("/api/workouts")
def create_workout(workout: WorkoutCreate):
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
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

@app.put("/api/workouts/{workout_id}")
def update_workout(workout_id: int, workout: WorkoutCreate):
    con = get_db_connection()
    try:
        con.execute(
            "UPDATE dim_workouts SET name=?, description=?, category=?, tags=?, url=?, source=? WHERE workout_id=?",
            [workout.name, workout.description, workout.category, workout.tags, workout.url, workout.source, workout_id]
        )
        return {"message": "Workout updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

@app.delete("/api/workouts/{workout_id}")
def delete_workout(workout_id: int):
    con = get_db_connection()
    try:
        con.execute("DELETE FROM dim_workouts WHERE workout_id=?", [workout_id])
        return {"message": "Workout deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()


# ==========================================
# ORIGINAL ENDPOINTS & LOGIC (Refactored DB close)
# ==========================================

@app.get("/users/")
def get_users():
    con = get_db_connection()
    try:
        # Check if table exists
        try:
            users = con.execute("SELECT * FROM dim_athlete").fetchall()
        except:
             return []
             
        columns = [desc[0] for desc in con.description]
        result = []
        for user in users:
            result.append(dict(zip(columns, user)))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        con.close()

def calculate_training_status_logic(user_id: int):
    con = get_db_connection()
    try:
        # --- Get athlete sex for TRIMP Banister sex coefficient ---
        try:
            athlete_row = con.execute(
                "SELECT sex, weight FROM dim_athlete WHERE athlete_id = ?", [user_id]
            ).fetchone()
            sex = athlete_row[0] if athlete_row else 'M'
            weight_kg = athlete_row[1] if athlete_row else None
        except Exception:
            sex = 'M'
            weight_kg = None

        # TRIMP Banister sex coefficient: 1.92 for males, 1.67 for females
        sex_coeff = 1.67 if sex == 'F' else 1.92
        hr_rest = 60  # assumed resting heart rate

        # --- Compute daily TRIMP + EF directly from dim_activity (no separate table needed) ---
        # TRIMP Banister = duration_min * hr_ratio * 0.64 * exp(sex_coeff * hr_ratio)
        # Efficiency Factor (EF) = avg_speed / avg_heartrate  (proxy: m/s per bpm)
        try:
            df = con.execute(f"""
                SELECT
                    da.start_date_local::DATE as activity_date,
                    SUM(
                        CASE
                            WHEN da.average_heartrate IS NOT NULL
                              AND da.max_heartrate IS NOT NULL
                              AND da.max_heartrate > da.average_heartrate
                              AND da.max_heartrate > {hr_rest}
                            THEN
                                (da.moving_time / 60.0)
                                * GREATEST(0.0, (da.average_heartrate - {hr_rest}) / (da.max_heartrate - {hr_rest}))
                                * 0.64
                                * EXP({sex_coeff} * GREATEST(0.0, (da.average_heartrate - {hr_rest}) / (da.max_heartrate - {hr_rest})))
                            ELSE 0
                        END
                    ) as daily_load,
                    AVG(
                        CASE
                            WHEN da.average_heartrate IS NOT NULL AND da.average_heartrate > 0
                                 AND da.average_speed IS NOT NULL AND da.average_speed > 0
                            THEN da.average_speed / da.average_heartrate
                            ELSE NULL
                        END
                    ) as daily_ef,
                    CAST(NULL AS DOUBLE) as daily_decoup
                FROM dim_activity da
                WHERE da.athlete_id = ?
                  AND da.type = 'Run'
                  AND da.moving_time > 0
                GROUP BY 1
                ORDER BY 1
            """, [user_id]).fetchdf()
        except Exception as e:
            print(f"Error computing TRIMP from dim_activity: {e}")
            return None

        if df.empty:
            return None


        # 2. Reindex
        start_date = df['activity_date'].min()
        end_date = pd.Timestamp(datetime.now().date())
        
        if start_date > end_date:
            start_date = end_date

        date_range = pd.date_range(start=start_date, end=end_date, freq='D')
        daily_data = pd.DataFrame({'date': date_range.date})
        daily_data['activity_date'] = pd.to_datetime(daily_data['date'])
        df['activity_date'] = pd.to_datetime(df['activity_date'])
        
        merged = pd.merge(daily_data, df, on='activity_date', how='left')
        merged['daily_load'] = merged['daily_load'].fillna(0)
        
        # Treat 0s as NaNs for EF and decoup so they don't skew the average or display as 0
        merged['daily_ef'] = merged['daily_ef'].replace(0, np.nan)
        merged['daily_decoup'] = merged['daily_decoup'].replace(0, np.nan)
        
        # 3. Calculate CTL, ATL, TSB
        loads = merged['daily_load'].values
        ctl, atl = calculate_ctl_atl(loads)
            
        merged['CTL'] = ctl
        merged['ATL'] = atl
        merged['TSB'] = calculate_tsb(merged['CTL'], merged['ATL'])
        merged['ACWR'] = calculate_acwr(merged['CTL'], merged['ATL'])
        
        # Calculate Rolling Averages for Insights (7-day)
        merged['EF_7d'] = merged['daily_ef'].rolling(window=7, min_periods=1).mean()
        merged['Decoup_7d'] = merged['daily_decoup'].rolling(window=7, min_periods=1).mean()
        
        # Get last known actual values
        merged['latest_ef'] = merged['daily_ef'].ffill()
        merged['latest_decoup'] = merged['daily_decoup'].ffill()
        
        today_stats = merged.iloc[-1]
        tsb = today_stats['TSB']

        target_category = get_target_category(tsb)
        
        vo2max_data = calculate_vo2max(user_id)
        latest_vo2_max = vo2max_data.get('latest_vo2_max') if vo2max_data else None
        
        # weight_kg and sex already fetched at top of function; derive weight_lbs here
        weight_lbs = round(weight_kg * 2.20462) if weight_kg else None

        history_df = merged.tail(90)
        history_list = []
        for _, row in history_df.iterrows():
            history_list.append({
                "date": str(row['date']),
                "fitness": clean_val(row['CTL']),
                "fatigue": clean_val(row['ATL']),
                "form": clean_val(row['TSB']),
                "daily_ef": clean_val(row['daily_ef'], 2),
                "daily_decoup": clean_val(row['daily_decoup'], 1)
            })

        # --- 14-day Linear Regression CTL Forecast ---
        forecast_list = []
        try:
            ctl_series = merged['CTL'].values
            n = len(ctl_series)
            # Use last 42 days (the CTL decay constant) as the training window
            window = min(42, n)
            X_train = np.arange(window).reshape(-1, 1).astype(float)
            y_train = ctl_series[-window:]
            valid_mask = ~np.isnan(y_train)
            if valid_mask.sum() >= 5:
                reg = LinearRegression()
                reg.fit(X_train[valid_mask], y_train[valid_mask])
                last_date = merged.iloc[-1]['date']
                for i in range(1, 15):
                    proj_x = np.array([[window - 1 + i]]).astype(float)
                    proj_ctl = float(reg.predict(proj_x)[0])
                    proj_date = (pd.Timestamp(last_date) + timedelta(days=i)).date()
                    forecast_list.append({
                        "date": str(proj_date),
                        "projected_ctl": round(proj_ctl, 2)
                    })
        except Exception as fe:
            print(f"Forecast error: {fe}")

        return {
            "date": str(today_stats['date']),
            "fitness_ctl": clean_val(today_stats['CTL']),
            "fatigue_atl": clean_val(today_stats['ATL']),
            "form_tsb":    clean_val(today_stats['TSB']),
            "acwr":        clean_val(today_stats['ACWR'], 2),
            "target_category": target_category,
            "efficiency_factor_7d": clean_val(today_stats.get('EF_7d'), 2),
            "aerobic_decoupling_7d": clean_val(today_stats.get('Decoup_7d'), 1),
            "latest_daily_ef": clean_val(today_stats.get('latest_ef'), 2),
            "latest_daily_decoup": clean_val(today_stats.get('latest_decoup'), 1),
            "latest_vo2_max": latest_vo2_max,
            "history": history_list,
            "forecast": forecast_list
        }
    finally:
        con.close()

def get_performance_standing(stats):
    ctl = stats.get('fitness_ctl') or 0
    tsb = stats.get('form_tsb') or 0
    vo2 = stats.get('latest_vo2_max') or 0
    gender = (stats.get('sex') or 'M').upper()
    
    # CTL Standing
    ctl_lbl = 'Developing'
    if ctl > 100: ctl_lbl = 'Elite Pro'
    elif ctl > 70: ctl_lbl = 'Advanced Enthusiast'
    elif ctl > 40: ctl_lbl = 'Consistent Runner'
    
    # TSB Standing
    tsb_lbl = 'Balanced'
    if tsb > 15: tsb_lbl = 'Fresh (Peaking)'
    elif tsb < -30: tsb_lbl = 'High Overload'
    elif tsb < -10: tsb_lbl = 'Optimal Training'

    # VO2 Max (Simplified Table for Prompt Context)
    vo2_lbl = 'Average'
    if vo2 > 0:
        if gender == 'M':
            if vo2 > 52: vo2_lbl = 'Excellent/Superior'
            elif vo2 > 45: vo2_lbl = 'Good'
        else:
            if vo2 > 46: vo2_lbl = 'Excellent/Superior'
            elif vo2 > 39: vo2_lbl = 'Good'

    return {
        "ctl_standing": ctl_lbl,
        "tsb_standing": tsb_lbl,
        "vo2_standing": vo2_lbl
    }

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
            
        llm_provider = os.getenv("LLM_PROVIDER", "local").lower()
        
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

        if llm_provider == "groq":
            groq_api_key = os.getenv("GROQ_API_KEY")
            if not groq_api_key:
                return "Groq API key not set in environment (GROQ_API_KEY)."
                
            url = "https://api.groq.com/openai/v1/chat/completions"
            payload = {
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]
            }
            headers = {
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json"
            }
            try:
                response = requests.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    return data["choices"][0]["message"]["content"]
                return str(data)
            except requests.exceptions.HTTPError as e:
                print(f"HTTPError on Groq API call: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Groq error response body: {e.response.text}")
                raise
        elif llm_provider == "deepseek":
            deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
            if not deepseek_api_key:
                return "Deepseek API key not set in environment (DEEPSEEK_API_KEY)."
                
            url = "https://api.deepseek.com/v1/chat/completions"
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]
            }
            headers = {
                "Authorization": f"Bearer {deepseek_api_key}",
                "Content-Type": "application/json"
            }
            try:
                response = requests.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    return data["choices"][0]["message"]["content"]
                return str(data)
            except requests.exceptions.HTTPError as e:
                print(f"HTTPError on Deepseek API call: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Deepseek error response body: {e.response.text}")
                raise
        else:
            url = "http://localhost:1234/api/v1/chat"
            payload = {
                "model": "mistralai/ministral-3-3b",
                "system_prompt": system_prompt,
                "input": prompt
            }
            headers = {"Content-Type": "application/json"}
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if "choices" in data and len(data["choices"]) > 0 and "message" in data["choices"][0]:
                return data["choices"][0]["message"]["content"]
            elif "output" in data and len(data["output"]) > 0 and "content" in data["output"][0]:
                return data["output"][0]["content"]
            elif "message" in data:
                return data["message"]
            elif "response" in data:
                return data["response"]
            else:
                return str(data)
            
    except Exception as e:
        print(f"Error getting AI insight: {e}")
        return None

@app.get("/status/{user_id}")
def get_status(user_id: int):
    status = calculate_training_status_logic(user_id)
    if not status:
        raise HTTPException(status_code=404, detail="No training data found for user")
        
    db = SessionLocal()
    user = db.query(User).filter(User.strava_athlete_id == user_id).first()
    if user:
        status['race_type'] = user.race_length
        status['race_date'] = str(user.race_date) if user.race_date else None
        status['goal_time'] = user.race_goal_time
    db.close()
    
    status['ai_insight'] = get_ai_insight(status, context="status")
    return status

@app.get("/recommend/{user_id}")
def get_recommendation(user_id: int):
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
            return {"error": "dim_workouts table not found. Run ingest_workouts.py first."}

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

def calculate_vo2max(user_id: int):
    con = get_db_connection()
    try:
        try:
            max_hr_query = con.execute("SELECT MAX(max_heartrate) FROM dim_activity WHERE athlete_id=?", [user_id]).fetchone()
            hr_max = max_hr_query[0] if max_hr_query and max_hr_query[0] and max_hr_query[0] > 100 else 190
        except:
            hr_max = 190

        hr_rest = 60
        
        try:
            query = """
            SELECT 
                a.activity_id,
                a.start_date_local::DATE as activity_date,
                v.value as speed,
                h.value as hr
            FROM dim_activity a
            JOIN stream_velocity v ON a.activity_id = v.activity_id
            JOIN stream_heartrate h ON a.activity_id = h.activity_id AND v.time_offset = h.time_offset
            WHERE a.athlete_id = ? AND a.type = 'Run'
              AND v.value > 1.5
              AND h.value > 100
            """
            df = con.execute(query, [user_id]).fetchdf()
        except Exception as e:
            print(f"Error querying streams: {e}")
            return None
            
        if df.empty:
            return None
            
        df = calculate_vo2max_from_df(df, hr_max, hr_rest)
        if df is None or df.empty:
            return None
        
        results = df.groupby('activity_date')['vo2_max_est'].median().reset_index()
        results = results.sort_values('activity_date')
        
        results['vo2_max_rolling_7d'] = results['vo2_max_est'].rolling(window=7, min_periods=1).mean()

        latest = results.iloc[-1]
        
        history_list = []
        for _, row in results.tail(14).iterrows():
            history_list.append({
                "date": str(row['activity_date']),
                "vo2_max_estimate": clean_val(row['vo2_max_est'], 2),
                "vo2_max_rolling_7d": clean_val(row['vo2_max_rolling_7d'], 2)
            })

        return {
            "user_id": user_id,
            "latest_vo2_max": clean_val(latest['vo2_max_rolling_7d'], 2),
            "history": history_list
        }
    finally:
        con.close()

@app.get("/vo2max/{user_id}")
def get_vo2max(user_id: int):
    vo2max_data = calculate_vo2max(user_id)
    if not vo2max_data:
        raise HTTPException(status_code=404, detail="No stream data found to calculate VO2 Max for this user.")
    return vo2max_data

def clean_json_response(content: str) -> str:
    """Helper to strip markdown formatting from LLM JSON responses."""
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()

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
        
        llm_provider = os.getenv("LLM_PROVIDER", "local").lower()
        
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
            "'approximate_distance' (e.g., '5 miles'), and 'description' (detailed instructions)."
        )

        if llm_provider == "groq":
            groq_api_key = os.getenv("GROQ_API_KEY")
            if not groq_api_key:
                return {"error": "Groq API key not set in environment (GROQ_API_KEY)."}
                
            url = "https://api.groq.com/openai/v1/chat/completions"
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"}
            }
            headers = {
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json"
            }
            try:
                response = requests.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    import json
                    content = clean_json_response(data["choices"][0]["message"]["content"])
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return {"error": "Failed to parse JSON response from LLM", "raw_content": content}
                return {"error": "Invalid response format from Groq API."}
            except requests.exceptions.HTTPError as e:
                print(f"HTTPError on Groq API call: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Groq error response body: {e.response.text}")
                return {"error": str(e)}
        elif llm_provider == "deepseek":
            deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
            if not deepseek_api_key:
                return {"error": "Deepseek API key not set in environment (DEEPSEEK_API_KEY)."}
                
            url = "https://api.deepseek.com/v1/chat/completions"
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"}
            }
            headers = {
                "Authorization": f"Bearer {deepseek_api_key}",
                "Content-Type": "application/json"
            }
            try:
                response = requests.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    import json
                    content = clean_json_response(data["choices"][0]["message"]["content"])
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return {"error": "Failed to parse JSON response from LLM", "raw_content": content}
                return {"error": "Invalid response format from Deepseek API."}
            except requests.exceptions.HTTPError as e:
                print(f"HTTPError on Deepseek API call: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"Deepseek error response body: {e.response.text}")
                return {"error": str(e)}
        else:
            return {"error": "This feature currently requires the LLM_PROVIDER to be set to groq or deepseek in order to return properly formatted JSON."}

    except Exception as e:
        print(f"Error getting AI training plan: {e}")
        return {"error": str(e)}

@app.get("/schedule/{user_id}")
def get_race_schedule(user_id: int, race_date: str, race_type: str, goal_time: Optional[str] = None):
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

def speed_to_pace_str(speed_ms):
    if speed_ms <= 0 or pd.isna(speed_ms):
        return "N/A"
    mins = 26.8224 / speed_ms
    m = int(mins)
    s = int((mins - m) * 60)
    return f"{m}:{s:02d} /mi"

def calculate_pace_zones(user_id: int):
    con = get_db_connection()
    try:
        max_hr_query = con.execute("SELECT MAX(max_heartrate) FROM dim_activity WHERE athlete_id=?", [user_id]).fetchone()
        hr_max = max_hr_query[0] if max_hr_query and max_hr_query[0] and max_hr_query[0] > 100 else 190
    except:
        hr_max = 190

    # Limit to recent activities for more accurate current zones (e.g. last 90 days)
    try:
        query = """
        SELECT 
            v.value as speed,
            h.value as hr
        FROM dim_activity a
        JOIN stream_velocity v ON a.activity_id = v.activity_id
        JOIN stream_heartrate h ON a.activity_id = h.activity_id AND v.time_offset = h.time_offset
        WHERE a.athlete_id = ? AND a.type = 'Run'
          AND v.value > 1.8 AND v.value < 8.0
          AND h.value > 80 AND h.value <= ?
          AND a.start_date_local >= current_date - interval '90 days'
        """
        df = con.execute(query, [user_id, hr_max + 10]).fetchdf()
    except Exception as e:
        print(f"Error querying streams for pace zones: {e}")
        return None
        
    if df.empty:
        return None

    # Calculate Threshold Speed (T_speed)
    # Target HR for threshold is typically 85%-92% of HR Max.
    # To avoid hills throwing off the pace, we take the 80th percentile of speed in this HR range.
    threshold_df = df[(df['hr'] >= hr_max * 0.85) & (df['hr'] <= hr_max * 0.92) & (df['speed'] > 2.0)]
    
    if len(threshold_df) >= 30:
        t_speed = float(threshold_df['speed'].quantile(0.80))
    else:
        # Fallback to general high end speed if no threshold data
        t_speed = float(df['speed'].quantile(0.90))

    # Convert t_speed to pace in seconds per mile
    t_pace_sec = 26.8224 / t_speed * 60  # convert m/s to seconds per mile (1609.34 / t_speed)
    
    # Coros Percentages of Threshold Pace:
    # Recovery: > 140%
    # Aerobic Endurance: 119% - 140%
    # Aerobic Power: 106% - 119%
    # Threshold: 94.5% - 106%
    # Anaerobic Endurance: 85% - 94.5%
    # Anaerobic Power: < 85%
    
    def format_pace(sec):
        m = int(sec // 60)
        s = int(sec % 60)
        return f"{m}'{s:02d}\""

    p140 = t_pace_sec * 1.40
    p119 = t_pace_sec * 1.19
    p106 = t_pace_sec * 1.06
    p0945 = t_pace_sec * 0.945
    p085 = t_pace_sec * 0.85
    
    # We subtract 1 second from the upper bound to prevent overlap, just like Coros
    result = {
        "Recovery": f">{format_pace(p140)}",
        "Aerobic Endurance": f"{format_pace(p119)}-{format_pace(p140)}",
        "Aerobic Power": f"{format_pace(p106)}-{format_pace(p119 - 1)}",
        "Threshold": f"{format_pace(p0945)}-{format_pace(p106 - 1)}",
        "Anaerobic Endurance": f"{format_pace(p085)}-{format_pace(p0945 - 1)}",
        "Anaerobic Power": f"<{format_pace(p085)}"
    }

    # Internal values for shading charts (in m/s)
    # v = 26.8224 / sec
    speeds = {
        "Recovery": {"min": 0, "max": 26.8224/p140 if p140 > 0 else 0},
        "Aerobic Endurance": {"min": 26.8224/p140 if p140 > 0 else 0, "max": 26.8224/p119 if p119 > 0 else 0},
        "Aerobic Power": {"min": 26.8224/p119 if p119 > 0 else 0, "max": 26.8224/p106 if p106 > 0 else 0},
        "Threshold": {"min": 26.8224/p106 if p106 > 0 else 0, "max": 26.8224/p0945 if p0945 > 0 else 0},
        "Anaerobic Endurance": {"min": 26.8224/p0945 if p0945 > 0 else 0, "max": 26.8224/p085 if p085 > 0 else 0},
        "Anaerobic Power": {"min": 26.8224/p085 if p085 > 0 else 0, "max": 10.0}
    }

    return {
        "user_id": user_id,
        "hr_max_used": hr_max,
        "pace_zones_min_per_mile": result,
        "pace_zones_speeds": speeds
    }

@app.get("/pace_zones/{user_id}")
def get_pace_zones(user_id: int):
    zones = calculate_pace_zones(user_id)
    if not zones:
        raise HTTPException(status_code=404, detail="No stream data found to calculate Pace Zones for this user.")
    return zones

@app.get("/analytics/{user_id}")
def get_analytics(user_id: int):
    con = get_db_connection()
    try:
        # Derive HR max from athlete's recorded data (fallback 190)
        try:
            hr_max_row = con.execute(
                "SELECT MAX(max_heartrate) FROM dim_activity WHERE athlete_id = ? AND max_heartrate > 100",
                [user_id]
            ).fetchone()
            hr_max = float(hr_max_row[0]) if hr_max_row and hr_max_row[0] else 190.0
        except Exception:
            hr_max = 190.0

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

class SettingsUpdate(BaseModel):
    race_length: str
    race_date: str
    race_goal_time: str

@app.post("/settings/{user_id}")
def update_settings(user_id: int, settings: SettingsUpdate):
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

class RaceCreate(BaseModel):
    name: Optional[str] = None
    race_length: str
    race_date: str
    goal_time: Optional[str] = None

@app.get("/api/races/{user_id}")
def get_user_races(user_id: int):
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
def create_user_race(user_id: int, race_data: RaceCreate):
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
def delete_race(race_id: int):
    db = SessionLocal()
    try:
        race = db.query(Race).filter(Race.id == race_id).first()
        if not race:
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
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    db = SessionLocal()
    user = db.query(User).filter(User.id == int(user_id)).first()
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


class ProfileUpdate(BaseModel):
    name: str
    email: str
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    height: Optional[float] = None  # stored in meters
    weight: Optional[float] = None  # stored in kg

@app.post("/profile/{user_id}")
def update_profile(user_id: int, profile: ProfileUpdate):
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
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    db = SessionLocal()
    user = db.query(User).filter(User.id == int(user_id)).first()
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("training_calendar.html", {"request": request, "user": user})


# ==========================================
# TRAINING PLAN CRUD ENDPOINTS
# ==========================================

class PlanEntryCreate(BaseModel):
    date: str
    workout_type: Optional[str] = None
    training_focus: Optional[str] = None
    approximate_distance: Optional[str] = None
    description: Optional[str] = None
    is_completed: Optional[bool] = False
    strava_activity_id: Optional[int] = None


class PlanEntryUpdate(BaseModel):
    workout_type: Optional[str] = None
    training_focus: Optional[str] = None
    approximate_distance: Optional[str] = None
    description: Optional[str] = None
    is_completed: Optional[bool] = None
    strava_activity_id: Optional[int] = None


class BulkEntriesCreate(BaseModel):
    entries: List[PlanEntryCreate]
    name: Optional[str] = None
    race_id: Optional[int] = None


class ChatMessageCreate(BaseModel):
    message: str


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
def get_training_plan(user_id: int):
    """Get the active training plan for a user including all entries and chat history."""
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
def create_or_replace_training_plan(user_id: int, body: BulkEntriesCreate):
    """Replace the user's training plan with a new set of entries (AI or manual bulk-add)."""
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
def add_plan_entry(user_id: int, entry_data: PlanEntryCreate):
    """Add a single entry (day) to the user's plan."""
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
def update_plan_entry(entry_id: int, update: PlanEntryUpdate):
    """Update a specific entry."""
    db = SessionLocal()
    try:
        entry = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.id == entry_id).first()
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
def delete_plan_entry(entry_id: int):
    """Delete a specific entry."""
    db = SessionLocal()
    try:
        entry = db.query(TrainingPlanEntry).filter(TrainingPlanEntry.id == entry_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        db.delete(entry)
        db.commit()
        return {"message": "Entry deleted"}
    finally:
        db.close()


@app.post("/api/training-plan/{user_id}/chat")
def training_plan_chat(user_id: int, msg: ChatMessageCreate):
    """Send a message to the AI to modify the training plan and persist the conversation."""
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

        llm_provider = os.getenv("LLM_PROVIDER", "local").lower()

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
            "CRITICAL: Always respond with valid JSON only. Do not wrap the JSON in ```json blocks or markdown. Output raw JSON."
        )

        messages_payload = [{"role": "system", "content": system_prompt}]
        for h in history:
            messages_payload.append({"role": h.role, "content": h.content})
        messages_payload.append({"role": "user", "content": msg.message})

        user_msg = ChatMessage(plan_id=plan.id, role="user", content=msg.message)
        db.add(user_msg)
        db.commit()

        if llm_provider not in ["groq", "deepseek"]:
            ai_reply = "AI chat requires LLM_PROVIDER=groq or deepseek to be set."
            ai_msg = ChatMessage(plan_id=plan.id, role="assistant", content=ai_reply)
            db.add(ai_msg)
            db.commit()
            return {"message": ai_reply, "plan_updates": None, "entries": [_entry_to_dict(e) for e in entries]}

        import json as json_lib
        
        if llm_provider == "groq":
            groq_api_key = os.getenv("GROQ_API_KEY")
            if not groq_api_key:
                raise HTTPException(status_code=500, detail="GROQ_API_KEY not set")
    
            url_api = "https://api.groq.com/openai/v1/chat/completions"
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": messages_payload,
                "response_format": {"type": "json_object"},
            }
            headers = {"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}
            
        elif llm_provider == "deepseek":
            deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
            if not deepseek_api_key:
                raise HTTPException(status_code=500, detail="DEEPSEEK_API_KEY not set")

            url_api = "https://api.deepseek.com/v1/chat/completions"
            payload = {
                "model": "deepseek-v4-pro",
                "messages": messages_payload,
                "response_format": {"type": "json_object"},
            }
            headers = {"Authorization": f"Bearer {deepseek_api_key}", "Content-Type": "application/json"}

        response = requests.post(url_api, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        content_clean = clean_json_response(content)
        
        try:
            parsed = json_lib.loads(content_clean)
        except json_lib.JSONDecodeError as e:
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

    except Exception as e:
        db.rollback()
        print(f"Training plan chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.delete("/api/training-plan/{user_id}/chat")
def clear_training_plan_chat(user_id: int):
    """Clear the AI chat history for the user's training plan."""
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
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
