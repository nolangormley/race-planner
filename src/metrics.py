import pandas as pd
import math
import re
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


def flat_equivalent_speed(speed, grade):
    """The speed on the flat that costs as much oxygen as `speed` on `grade`
    (a fraction). Works on numbers, arrays and Series.

    From the ACSM running equation: the horizontal cost is 0.2 x speed,
    uphill adds 0.9 x speed x grade, and downhill gives back half that rate
    (0.45). A 5% climb costs 22.5% more than the flat; a 5% descent about
    11% less.
    """
    uphill = np.clip(grade, 0, None)
    downhill = np.clip(grade, None, 0)
    return speed * (1 + (0.9 * uphill + 0.45 * downhill) / 0.2)


def calculate_vo2max_from_df(df, hr_max=190, hr_rest=60):
    """
    Given a DataFrame with 'speed' and 'hr' columns (and optionally 'grade',
    a fraction), estimates VO2 max per sample. Returns a DataFrame with
    'vo2_max_est' calculated.

    The oxygen cost of running is the ACSM equation: 0.2 x speed (m/min),
    with the grade folded in by flat_equivalent_speed, plus 3.5 resting.
    """
    df_calc = df.copy()
    speed_per_min = df_calc['speed'] * 60
    grade = df_calc['grade'] if 'grade' in df_calc.columns else 0.0
    df_calc['vo2_current'] = 0.2 * flat_equivalent_speed(speed_per_min, grade) + 3.5
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


# Planned-workout load, for projecting fitness and form along a training plan.
# A session's TRIMP is the athlete's typical TRIMP per kilometre of easy
# running, scaled by how hard the workout type is.
WORKOUT_LOAD_FACTORS = {
    "rest": 0.0, "recovery": 0.8, "easy run": 1.0, "long run": 1.1, "tempo": 1.35,
    "intervals": 1.45, "race": 1.6, "cross training": 0.6, "strength": 0.4,
}
DEFAULT_WORKOUT_KM = {
    "recovery": 6.0, "easy run": 8.0, "long run": 16.0, "tempo": 10.0, "intervals": 10.0,
    "race": 10.0, "cross training": 8.0, "strength": 5.0,
}
METERS_PER_MILE = 1609.344


def planned_distance_km(text):
    """'6 miles', '8 km', '10K', '5 mi' to kilometres; None when there is no number.
    A bare number is miles, matching the calendar's convention."""
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(k(?:m|ilomet\w*)?|mi(?:les?)?|m\b)?", str(text), re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit.startswith("k"):
        return amount
    if unit == "m":
        return amount / 1000
    return amount * METERS_PER_MILE / 1000


def estimate_entry_load(workout_type, distance_km, trimp_per_km):
    kind = (workout_type or "").strip().lower()
    factor = WORKOUT_LOAD_FACTORS.get(kind, 1.0)
    if factor == 0:
        return 0.0
    km = distance_km if distance_km else DEFAULT_WORKOUT_KM.get(kind, 8.0)
    return trimp_per_km * factor * km


def project_training_load(ctl, atl, daily_loads, ctl_decay=42, atl_decay=7):
    """Steps fitness and fatigue forward one day per load, starting from the
    current values. Returns a list of (ctl, atl, tsb) per day, where tsb is
    the form going into that day (yesterday's fitness minus fatigue), the
    same convention as the history."""
    projected = []
    for load in daily_loads:
        tsb = ctl - atl
        ctl = ctl + (load - ctl) / ctl_decay
        atl = atl + (load - atl) / atl_decay
        projected.append((ctl, atl, tsb))
    return projected


# Best efforts: the fastest time over a distance inside a run, from the
# per-second velocity stream. The basis for race predictions, since a
# runner's fastest real efforts say more than an aerobic ceiling does.
BEST_EFFORT_DISTANCES_M = {"1 mile": 1609.344, "5K": 5000.0, "10K": 10000.0, "Half marathon": 21097.5}


def best_effort_seconds(time_offsets, velocities, distance_m):
    """Fastest time to cover distance_m within one activity, or None when the
    activity is shorter. time_offsets are seconds, velocities m/s, aligned."""
    times = np.asarray(time_offsets, dtype=float)
    speeds = np.nan_to_num(np.asarray(velocities, dtype=float))
    if len(times) < 2:
        return None
    gaps = np.diff(times, prepend=times[0])
    gaps[gaps > 30] = 0  # a paused watch covers no distance
    cumulative = np.cumsum(speeds * gaps)
    if cumulative[-1] < distance_m:
        return None
    ends = np.searchsorted(cumulative, cumulative + distance_m, side="left")
    valid = ends < len(times)
    if not valid.any():
        return None
    durations = times[ends[valid]] - times[valid]
    return float(durations.min())


def riegel_prediction(seconds, from_distance_m, to_distance_m, exponent=1.06):
    """Riegel (1981): race time scales with distance to the power 1.06."""
    return seconds * (to_distance_m / from_distance_m) ** exponent
