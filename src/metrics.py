import pandas as pd
import math
import numpy as np

def calculate_ctl_atl(loads, ctl_decay=42, atl_decay=7):
    """
    Calculates the Chronic Training Load (CTL) and Acute Training Load (ATL)
    using Exponentially Weighted Moving Average (EWMA) over a list of daily loads.
    Returns two lists (ctl, atl) of the same length as `loads`.
    """
    ctl = [0.0]
    atl = [0.0]
    for i in range(len(loads)):
        daily_strain = loads[i]
        curr_ctl = ctl[-1] + (daily_strain - ctl[-1]) / ctl_decay
        curr_atl = atl[-1] + (daily_strain - atl[-1]) / atl_decay
        ctl.append(curr_ctl)
        atl.append(curr_atl)
    
    return ctl[1:], atl[1:]

def calculate_tsb(ctl, atl):
    """
    Calculates Training Stress Balance (TSB) from CTL and ATL.
    Also known as 'Form'.
    """
    if isinstance(ctl, list) and isinstance(atl, list):
        return [c - a for c, a in zip(ctl, atl)]
    return ctl - atl

def calculate_acwr(ctl, atl):
    """
    Calculates Acute-to-Chronic Workload Ratio (ACWR) from CTL and ATL.
    Also known as ATL/CTL. Returns 0 if CTL is 0.
    """
    if isinstance(ctl, pd.Series) and isinstance(atl, pd.Series):
         return np.where(ctl > 0, atl / ctl, 0.0)
    if isinstance(ctl, list) and isinstance(atl, list):
        return [(a / c if c > 0 else 0.0) for c, a in zip(ctl, atl)]
    return atl / ctl if ctl > 0 else 0.0

def get_target_category(tsb):
    """
    Maps TSB to a target training category.
    """
    if tsb > 5:
        return "Anaerobic"
    elif -10 <= tsb <= 5:
        return "VO2Max"
    elif -30 <= tsb < -10:
        return "Aerobic"
    else:
        return "Recovery"

def steady_state_mask(df, warmup_seconds=300, window=60):
    """Which samples of a per-second running frame are steady enough to
    estimate VO2 max from. df has activity_id, time_offset, speed and hr.

    Heart rate lags pace by a minute or two, so the start of a run, every
    surge and every downhill read as "fast pace at a low heart rate" and
    inflate the estimate. A sample counts as steady when it is past the
    warm-up and within 5% of its one-minute mean pace and 3 bpm of its
    one-minute mean heart rate.
    """
    grouped = df.groupby('activity_id')
    speed_mean = grouped['speed'].transform(lambda s: s.rolling(window, center=True, min_periods=window // 2).mean())
    hr_mean = grouped['hr'].transform(lambda s: s.rolling(window, center=True, min_periods=window // 2).mean())
    steady = (
        (df['time_offset'] >= warmup_seconds)
        & ((df['speed'] - speed_mean).abs() <= 0.05 * speed_mean)
        & ((df['hr'] - hr_mean).abs() <= 3)
    )
    return steady.fillna(False)


def add_grade(df, window=30):
    """Adds a 'grade' column (rise over run across the last `window` samples)
    from the altitude stream, clipped to a plausible running range. Without
    an altitude column the grade is zero."""
    if 'altitude' not in df.columns:
        df['grade'] = 0.0
        return df
    grouped = df.groupby('activity_id')
    climb = grouped['altitude'].transform(lambda s: s.diff(window))
    distance = grouped['speed'].transform(lambda s: s.rolling(window).sum())
    df['grade'] = (climb / distance).clip(-0.08, 0.12).fillna(0.0)
    return df


def calculate_vo2max_from_df(df, hr_max=190, hr_rest=60):
    """
    Given a DataFrame with 'speed' and 'hr' columns (and optionally 'grade',
    a fraction), estimates VO2 max per sample. Returns a DataFrame with
    'vo2_max_est' calculated.

    The oxygen cost of running is the ACSM equation: 0.2 x speed (m/min) plus
    a grade term, plus 3.5 resting. Downhill gives back less than the uphill
    term costs, so the downhill coefficient is halved.
    """
    df_calc = df.copy()
    speed_per_min = df_calc['speed'] * 60
    grade = df_calc['grade'] if 'grade' in df_calc.columns else 0.0
    uphill = grade.clip(lower=0) if hasattr(grade, 'clip') else max(grade, 0)
    downhill = grade.clip(upper=0) if hasattr(grade, 'clip') else min(grade, 0)
    df_calc['vo2_current'] = 0.2 * speed_per_min + 0.9 * speed_per_min * uphill + 0.45 * speed_per_min * downhill + 3.5
    df_calc['pct_hrr'] = (df_calc['hr'] - hr_rest) / (hr_max - hr_rest)
    # avoid division by zero or negative
    df_calc = df_calc[df_calc['pct_hrr'] > 0]
    
    if df_calc.empty:
        return None

    df_calc['vo2_max_est'] = ((df_calc['vo2_current'] - 3.5) / df_calc['pct_hrr']) + 3.5
    return df_calc

def clean_val(val, decimals=1):
    """
    Cleans a value (usually from pandas) for JSON serialization, handling NaNs
    and rounding it.
    """
    if pd.isna(val) or val is None:
        return None
    return round(float(val), decimals)


# Daniels and Gilbert (1979): the oxygen cost of running at a speed in
# meters per minute, and the share of VO2 max a runner can hold for a race
# lasting a given number of minutes. Together they turn a VO2 max (VDOT)
# into race predictions.
def daniels_cost(speed_m_per_min):
    return 0.000104 * speed_m_per_min ** 2 + 0.182258 * speed_m_per_min - 4.60


def daniels_fraction(minutes):
    return 0.8 + 0.1894393 * math.exp(-0.012778 * minutes) + 0.2989558 * math.exp(-0.1932605 * minutes)


def race_time_for_vdot(vdot, distance_m):
    """Predicted race time in seconds: the duration at which the cost of the
    speed needed equals the share of VDOT that duration allows. The gap
    between the two falls as the time grows, so bisection finds it."""
    low, high = 2.0, 900.0
    for _ in range(80):
        mid = (low + high) / 2
        if daniels_cost(distance_m / mid) > daniels_fraction(mid) * vdot:
            low = mid
        else:
            high = mid
    return (low + high) / 2 * 60


def vdot_from_race(distance_m, seconds):
    """The VDOT implied by a race result, the inverse of race_time_for_vdot."""
    minutes = seconds / 60
    return daniels_cost(distance_m / minutes) / daniels_fraction(minutes)
