"""Finds the structure of a run from its pace: repeated fast reps with
recoveries ("intervals"), one or two long fast blocks ("tempo"), or
nothing in particular.

The detector reads only the streams. It knows nothing of the plan or the
athlete's zones, so every rule is relative to the run itself:

1. Resample speed, altitude and heart rate to one sample a second. A gap
   of more than 10 s between samples is a stop: speed 0 inside it.
2. With an altitude stream, turn speed into its flat equivalent with the
   ACSM grade cost (metrics.flat_equivalent_speed) over a centred 30 s
   grade, so a climb at the same effort does not read as a recovery and a
   descent does not read as a rep.
3. Smooth with a centred 15 s rolling median (removes GPS spikes), then a
   centred 10 s rolling mean.
4. Split the moving seconds (faster than 1 m/s) into a slow and a fast
   group with one-dimensional two-means. When the fast centre is less than
   12% faster than the slow one, the run is steady: no structure.
5. Label each second work or rest with hysteresis around the midpoint of
   the two centres (a quarter of the gap either side), so noise near the
   midpoint does not flicker. A stop is always rest.
6. Fold segments shorter than 20 s into their neighbours, shortest first.
   Smoothing shaves a few seconds off both ends of every rep (most next to
   a standing rest), so move each rep's edges back out to where the
   unsmoothed pace crosses the midpoint, at most 12 s and never into the
   next rep. Then drop work segments shorter than 45 s or 150 m (strides,
   surges).
7. Intervals: three or more reps with a median rep under 8 minutes.
   Tempo: one or two work blocks of 12 minutes or more each.
   Anything else: no structure.

Known blind spots: a run-walk easy run reads as intervals (the walks look
like recoveries), and three or more long reps (3 x 10 min) are neither.
"""
import numpy as np
import pandas as pd

from src.metrics import flat_equivalent_speed

GAP_S = 10
GRADE_WINDOW_S = 30
GRADE_RANGE = (-0.08, 0.12)        # the plausible running range metrics.add_grade clips to
MEDIAN_WINDOW_S = 15
MEAN_WINDOW_S = 10
MOVING_SPEED = 1.0                 # m/s; slower is standing or barely walking
MIN_SEPARATION = 1.12              # fast centre over slow centre
HYSTERESIS_SHARE = 0.25
MIN_SEGMENT_S = 20
EDGE_S = 12
MIN_REP_S = 45
MIN_REP_M = 150
INTERVAL_MIN_REPS = 3
INTERVAL_MAX_MEDIAN_S = 480
TEMPO_MIN_BLOCK_S = 720
TEMPO_MAX_BLOCKS = 2
MIN_RUN_S = 300

NO_STRUCTURE = {"kind": None, "reps": [], "recoveries": []}


def as_floats(values):
    return np.array([np.nan if value is None else value for value in values], dtype=float)


def resample(times, grid, values):
    """`values` interpolated onto the one-second grid; NaN everywhere when
    the stream has fewer than two readings."""
    array = as_floats(values)
    valid = ~np.isnan(array)
    if valid.sum() < 2:
        return np.full(grid.shape, np.nan)
    return np.interp(grid, times[valid], array[valid])


def stopped(times, grid):
    """Grid seconds inside a recording gap longer than GAP_S."""
    previous = np.searchsorted(times, grid, side="right") - 1
    following = np.minimum(previous + 1, len(times) - 1)
    gap = times[following] - times[previous]
    return (gap > GAP_S) & (grid > times[previous]) & (grid < times[following])


def centred_grade(altitude, speed):
    """Rise over run across a centred GRADE_WINDOW_S window. Centred, unlike
    metrics.add_grade's trailing window, because a finished run can look
    ahead, and a trailing grade lags every change of slope by 15 s."""
    count = len(altitude)
    half = GRADE_WINDOW_S // 2
    index = np.arange(count)
    low = np.clip(index - half, 0, count - 1)
    high = np.clip(index + half, 0, count - 1)
    covered = np.concatenate([[0.0], np.cumsum(speed)])
    run = covered[high] - covered[low]
    rise = altitude[high] - altitude[low]
    grade = np.divide(rise, run, out=np.zeros(count), where=run > 5)
    return np.clip(grade, *GRADE_RANGE)


def smoothed(values):
    series = pd.Series(values)
    series = series.rolling(MEDIAN_WINDOW_S, center=True, min_periods=1).median()
    return series.rolling(MEAN_WINDOW_S, center=True, min_periods=1).mean().to_numpy()


def two_means(values):
    """Centres of a slow and a fast group of one-dimensional values."""
    slow, fast = np.percentile(values, [20, 80])
    for _ in range(50):
        if fast <= slow:
            break
        is_fast = values > (slow + fast) / 2
        if is_fast.all() or not is_fast.any():
            break
        next_slow, next_fast = values[~is_fast].mean(), values[is_fast].mean()
        if abs(next_slow - slow) < 1e-6 and abs(next_fast - fast) < 1e-6:
            break
        slow, fast = next_slow, next_fast
    return slow, fast


def label_work(speed, moving, slow, fast):
    midpoint = (slow + fast) / 2
    margin = HYSTERESIS_SHARE * (fast - slow)
    work = np.zeros(len(speed), dtype=bool)
    state = bool(moving[0] and speed[0] > midpoint)
    for second, value in enumerate(speed):
        if not moving[second]:
            state = False
        elif state and value < midpoint - margin:
            state = False
        elif not state and value > midpoint + margin:
            state = True
        work[second] = state
    return work


def segments(labels):
    """(start, end, is_work) runs of equal labels; end is exclusive."""
    changes = np.flatnonzero(np.diff(labels.astype(int))) + 1
    starts = np.concatenate([[0], changes])
    ends = np.concatenate([changes, [len(labels)]])
    return [(int(start), int(end), bool(labels[start])) for start, end in zip(starts, ends)]


def fold_short_segments(labels):
    labels = labels.copy()
    while True:
        runs = segments(labels)
        if len(runs) < 2:
            return labels
        start, end, is_work = min(runs, key=lambda run: run[1] - run[0])
        if end - start >= MIN_SEGMENT_S:
            return labels
        labels[start:end] = not is_work


def refine_edges(labels, effort, midpoint):
    work = [(start, end) for start, end, is_work in segments(labels) if is_work]
    refined = np.zeros(len(labels), dtype=bool)
    previous_end = -1
    for position, (start, end) in enumerate(work):
        next_start = work[position + 1][0] if position + 1 < len(work) else len(labels) + 1
        steps = 0
        while start - 1 > previous_end and steps < EDGE_S and effort[start - 1] > midpoint:
            start -= 1
            steps += 1
        steps = 0
        while end < next_start - 1 and steps < EDGE_S and effort[end] > midpoint:
            end += 1
            steps += 1
        refined[start:end] = True
        previous_end = end
    return refined


def stats(start, end, grid, speed, heartrate):
    seconds = end - start
    distance = float(speed[start:end].sum())
    beats = heartrate[start:end]
    beats = beats[~np.isnan(beats)]
    return {
        "start_offset": int(grid[start]),
        "end_offset": int(grid[start]) + seconds,
        "distance": round(distance, 1),
        "moving_time": seconds,
        "average_speed": round(distance / seconds, 3),
        "average_heartrate": round(float(beats.mean()), 1) if beats.size else None,
        "max_heartrate": round(float(beats.max()), 1) if beats.size else None,
    }


def classify(reps):
    durations = [rep["moving_time"] for rep in reps]
    if len(reps) >= INTERVAL_MIN_REPS and np.median(durations) < INTERVAL_MAX_MEDIAN_S:
        return "intervals"
    if 1 <= len(reps) <= TEMPO_MAX_BLOCKS and min(durations) >= TEMPO_MIN_BLOCK_S:
        return "tempo"
    return None


def detect_structure(time_s, velocity_mps, altitude_m=None, heartrate=None):
    """The structure of one run. Streams are equal-length lists aligned on
    time_s (seconds); None marks a missing reading. Returns {"kind":
    "intervals" | "tempo" | None, "reps": [...], "recoveries": [...]}; reps
    and recoveries are empty when kind is None."""
    times = as_floats(time_s)
    keep = ~np.isnan(times)
    if keep.sum() < 2:
        return NO_STRUCTURE
    order = np.argsort(times[keep], kind="stable")
    times = times[keep][order]

    def aligned(values):
        return [values[index] for index in np.flatnonzero(keep)[order]] if values is not None else None

    grid = np.arange(int(times[0]), int(times[-1]) + 1)
    if len(grid) < MIN_RUN_S:
        return NO_STRUCTURE
    speed = resample(times, grid, aligned(velocity_mps))
    if np.isnan(speed).all():
        return NO_STRUCTURE
    paused = stopped(times, grid)
    speed = np.where(paused | np.isnan(speed), 0.0, np.maximum(speed, 0.0))
    beats = resample(times, grid, aligned(heartrate)) if heartrate is not None else np.full(grid.shape, np.nan)
    beats[paused] = np.nan

    raw_effort = speed
    if altitude_m is not None:
        altitude = resample(times, grid, aligned(altitude_m))
        if not np.isnan(altitude).all():
            raw_effort = flat_equivalent_speed(speed, centred_grade(altitude, speed))

    moving = smoothed(speed) >= MOVING_SPEED
    effort = smoothed(raw_effort)
    if moving.sum() < MIN_RUN_S:
        return NO_STRUCTURE
    slow, fast = two_means(effort[moving])
    if slow <= 0 or fast / slow < MIN_SEPARATION:
        return NO_STRUCTURE

    labels = fold_short_segments(label_work(effort, moving, slow, fast))
    labels = refine_edges(labels, raw_effort, (slow + fast) / 2)
    for start, end, is_work in segments(labels):
        if is_work and (end - start < MIN_REP_S or speed[start:end].sum() < MIN_REP_M):
            labels[start:end] = False

    work = [(start, end) for start, end, is_work in segments(labels) if is_work]
    reps = [{"index": number, **stats(start, end, grid, speed, beats)} for number, (start, end) in enumerate(work, start=1)]
    kind = classify(reps) if reps else None
    if kind is None:
        return NO_STRUCTURE

    recoveries = []
    for rep, (_, end), (next_start, _) in zip(reps, work, work[1:]):
        recovery = {"after_rep": rep["index"], **stats(end, next_start, grid, speed, beats)}
        rep["recovery_time"] = recovery["moving_time"]
        recoveries.append(recovery)
    reps[-1]["recovery_time"] = None
    return {"kind": kind, "reps": reps, "recoveries": recoveries}
