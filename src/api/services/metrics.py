"""CTL/ATL/TSB/VO2max/pace-zone calculations against the DuckDB warehouse.

Extracted from main.py, where this ~250-line metrics engine lived as bare
functions mixed in with route handlers and LLM prompt-building code (see
agent_prompting/improvement_plan.md Section 6). Pure move — no behavior
change; see the improvement plan for the actual metrics fixes that already
landed in Phase 2 (this module's calculate_training_status_logic already
reflects that reconciliation).
"""
import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from src.metrics import (
    calculate_ctl_atl,
    calculate_tsb,
    calculate_acwr,
    get_target_category,
    calculate_vo2max_from_df,
    clean_val,
    steady_state_mask,
    add_grade,
)
from src.api.deps import get_db_connection, DEFAULT_MAX_HR, DEFAULT_REST_HR

logger = logging.getLogger("race_planner.api")


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


def detected_max_hr(con, athlete_id):
    """The highest heart rate in the athlete's activities, or DEFAULT_MAX_HR.
    The profile's max_hr overrides it wherever it is used."""
    try:
        row = con.execute(
            "SELECT MAX(max_heartrate) FROM dim_activity WHERE athlete_id = ? AND max_heartrate > 100", [athlete_id]
        ).fetchone()
    except Exception:
        logger.exception("Failed to look up max heart rate for athlete %s", athlete_id)
        return float(DEFAULT_MAX_HR)
    return float(row[0]) if row and row[0] else float(DEFAULT_MAX_HR)


def calculate_training_status_logic(user_id: int, hr_max=None, hr_rest=None):
    """Computes CTL/ATL/TSB/ACWR/VO2 history for an athlete.

    Reads daily training load from activity_effectiveness (populated by
    src/pipeline.py's run_analyze_effectiveness, which integrates TRIMP
    sample-by-sample over the real HR stream) instead of recomputing TRIMP/EF
    independently here from dim_activity summary fields. This used to be a
    second, hand-written implementation of the Banister TRIMP formula that
    disagreed with pipeline.py's in three ways: an opposite default sex
    coefficient when an athlete's sex was unknown, a 60x unit difference in
    efficiency factor (no `* 60` here), and a Run-only activity filter where
    pipeline.py had none. There is now exactly one TRIMP/EF implementation
    (src/pipeline.py), and this function just aggregates its output to a
    daily series — see agent_prompting/improvement_plan.md Section 2.

    NOTE: because pipeline.py's version has no activity-type filter, daily
    load here now includes all activity types with an HR stream (e.g.
    cycling), not just runs — a deliberate behavior change, since CTL/ATL are
    meant to track overall training stress rather than running load alone.
    """
    con = get_db_connection()
    try:
        try:
            df = con.execute("""
                SELECT
                    da.start_date_local::DATE as activity_date,
                    SUM(ae.trimp_banister) as daily_load,
                    AVG(ae.efficiency_factor) as daily_ef,
                    AVG(ae.aerobic_decoupling) as daily_decoup
                FROM dim_activity da
                JOIN activity_effectiveness ae ON da.activity_id = ae.activity_id
                WHERE da.athlete_id = ?
                GROUP BY 1
                ORDER BY 1
            """, [user_id]).fetchdf()
        except Exception as e:
            print(f"Error computing daily training load from activity_effectiveness: {e}")
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

        vo2max_data = calculate_vo2max(user_id, hr_max=hr_max, hr_rest=hr_rest)
        latest_vo2_max = vo2max_data.get('latest_vo2_max') if vo2max_data else None

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


def calculate_vo2max(user_id: int, hr_max=None, hr_rest=None):
    """VO2 max estimated from steady running: for each steady second, the
    grade-adjusted oxygen cost of the pace divided by the share of heart
    rate reserve, extrapolated to max (see src.metrics.steady_state_mask and
    calculate_vo2max_from_df). Samples under 65% of reserve are left out:
    extrapolating from easy jogging is where the estimate is noisiest.

    Returns the per-run medians of the last 14 runs with a rolling 7-run
    median, and that rolling value as latest_vo2_max.
    """
    con = get_db_connection()
    try:
        hr_max = hr_max or detected_max_hr(con, user_id)
        hr_rest = hr_rest or DEFAULT_REST_HR

        try:
            df = con.execute("""
            SELECT
                a.activity_id,
                a.start_date_local::DATE as activity_date,
                v.time_offset,
                v.value as speed,
                h.value as hr,
                alt.value as altitude
            FROM dim_activity a
            JOIN stream_velocity v ON a.activity_id = v.activity_id
            JOIN stream_heartrate h ON a.activity_id = h.activity_id AND v.time_offset = h.time_offset
            LEFT JOIN stream_altitude alt ON a.activity_id = alt.activity_id AND v.time_offset = alt.time_offset
            WHERE a.athlete_id = ? AND a.type = 'Run'
              AND v.value > 1.5
              AND h.value > 100
            ORDER BY a.activity_id, v.time_offset
            """, [user_id]).fetchdf()
        except Exception as e:
            print(f"Error querying streams: {e}")
            return None

        if df.empty:
            return None

        df = add_grade(df)
        df = df[steady_state_mask(df)]
        if df.empty:
            return None
        df = calculate_vo2max_from_df(df, hr_max, hr_rest)
        if df is None or df.empty:
            return None
        df = df[df['pct_hrr'] >= 0.65]
        if df.empty:
            return None

        results = df.groupby('activity_date')['vo2_max_est'].median().reset_index()
        results = results.sort_values('activity_date')
        results['vo2_max_rolling_7d'] = results['vo2_max_est'].rolling(window=7, min_periods=1).median()

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
            "hr_max_used": hr_max,
            "hr_rest_used": hr_rest,
            "history": history_list
        }
    finally:
        con.close()


def speed_to_pace_str(speed_ms):
    if speed_ms <= 0 or pd.isna(speed_ms):
        return "N/A"
    mins = 26.8224 / speed_ms
    m = int(mins)
    s = int((mins - m) * 60)
    return f"{m}:{s:02d} /mi"


METERS_PER_MILE = 1609.344


def format_pace(seconds_per_mile):
    minutes = int(seconds_per_mile // 60)
    seconds = int(seconds_per_mile % 60)
    return f"{minutes}'{seconds:02d}\""


def pace_zones_from_threshold_speed(t_speed):
    """Coros-style pace zones around a threshold speed in m/s.

    Returns (labels, speeds): labels are min/mile strings, speeds are the
    zone edges in m/s for chart shading. Zone edges as a share of threshold
    pace: recovery slower than 140%, aerobic endurance 119-140%, aerobic
    power 106-119%, threshold 94.5-106%, anaerobic endurance 85-94.5%,
    anaerobic power faster than 85%.
    """
    t_pace_sec = METERS_PER_MILE / t_speed
    p140 = t_pace_sec * 1.40
    p119 = t_pace_sec * 1.19
    p106 = t_pace_sec * 1.06
    p0945 = t_pace_sec * 0.945
    p085 = t_pace_sec * 0.85

    # The upper bound loses one second so neighbouring zones do not overlap.
    labels = {
        "Recovery": f">{format_pace(p140)}",
        "Aerobic Endurance": f"{format_pace(p119)}-{format_pace(p140)}",
        "Aerobic Power": f"{format_pace(p106)}-{format_pace(p119 - 1)}",
        "Threshold": f"{format_pace(p0945)}-{format_pace(p106 - 1)}",
        "Anaerobic Endurance": f"{format_pace(p085)}-{format_pace(p0945 - 1)}",
        "Anaerobic Power": f"<{format_pace(p085)}",
    }
    speed_at = lambda sec_per_mile: METERS_PER_MILE / sec_per_mile
    speeds = {
        "Recovery": {"min": 0, "max": speed_at(p140)},
        "Aerobic Endurance": {"min": speed_at(p140), "max": speed_at(p119)},
        "Aerobic Power": {"min": speed_at(p119), "max": speed_at(p106)},
        "Threshold": {"min": speed_at(p106), "max": speed_at(p0945)},
        "Anaerobic Endurance": {"min": speed_at(p0945), "max": speed_at(p085)},
        "Anaerobic Power": {"min": speed_at(p085), "max": 10.0},
    }
    return labels, speeds


def calculate_pace_zones(user_id: int, hr_max=None):
    # NOTE: previously this connection was never closed on any code path in
    # this function (get_db_connection() with no matching finally). Wrapped
    # the whole DB-touching section in try/finally to fix the leak.
    con = get_db_connection()
    try:
        hr_max = hr_max or detected_max_hr(con, user_id)

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
    finally:
        con.close()

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

    result, speeds = pace_zones_from_threshold_speed(t_speed)

    return {
        "user_id": user_id,
        "hr_max_used": hr_max,
        "pace_zones_min_per_mile": result,
        "pace_zones_speeds": speeds
    }


def coros_resting_hr(con, athlete_id, days=14):
    """Median overnight resting heart rate from the linked COROS account over
    the last nights, or None without readings."""
    row = con.execute(
        "SELECT median(rhr) FROM coros_daily WHERE athlete_id = ? AND rhr IS NOT NULL AND day >= ?",
        [athlete_id, date.today() - timedelta(days=days)],
    ).fetchone()
    return int(round(row[0])) if row and row[0] else None


def resolve_resting_hr(con, user):
    """The resting heart rate the heart-rate-reserve model uses: the profile
    value, else the COROS median, else the default."""
    if user.resting_hr:
        return user.resting_hr
    if user.strava_athlete_id:
        from_coros = coros_resting_hr(con, user.strava_athlete_id)
        if from_coros:
            return from_coros
    return DEFAULT_REST_HR
