"""Builds a training plan skeleton from a start date to race day.

Deterministic and inspectable, no language model. Weekly volume ramps from
the athlete's current weekly distance toward a peak that suits the race
distance, no faster than 10% a week, with a cutback every fourth week and a
taper before the race. Each week has a long run, one or two quality
sessions, easy runs on the other training days and rest otherwise. A
quality session's description is built from its planned distance, so the
warm-up, the work and the cool-down add up to it; a day too short for the
shortest session is an easy run with strides instead. The easy runs take
up the rounding of the other days, so each full week before race week adds
up to its target to the half unit. The output is a list of day entries the
calendar stores as-is, so every session can be edited afterwards.
"""
import math
from datetime import date, timedelta

METERS_PER_MILE = 1609.344

# How far above the current weekly volume the plan may peak, how much of a
# week the long run takes, which quality sessions a race favours, and how
# many days before the race volume drops.
RACE_PROFILES = {
    "5k": {"distance_m": 5000.0, "peak_multiplier": 1.3, "long_run_share": 0.25, "quality": ["Intervals", "Tempo"], "taper_days": 7},
    "5 mile": {"distance_m": 8046.72, "peak_multiplier": 1.3, "long_run_share": 0.26, "quality": ["Intervals", "Tempo"], "taper_days": 7},
    "10k": {"distance_m": 10000.0, "peak_multiplier": 1.35, "long_run_share": 0.28, "quality": ["Tempo", "Intervals"], "taper_days": 8},
    "10 mile": {"distance_m": 16093.44, "peak_multiplier": 1.4, "long_run_share": 0.30, "quality": ["Tempo", "Intervals"], "taper_days": 10},
    "half marathon": {"distance_m": 21097.5, "peak_multiplier": 1.45, "long_run_share": 0.33, "quality": ["Tempo", "Intervals"], "taper_days": 10},
    "marathon": {"distance_m": 42195.0, "peak_multiplier": 1.6, "long_run_share": 0.38, "quality": ["Tempo", "Intervals"], "taper_days": 18},
}
MAX_LONG_RUN_M = {"5k": 16000, "5 mile": 18000, "10k": 20000, "10 mile": 24000, "half marathon": 26000, "marathon": 35000}
MIN_WEEKLY_M = 10000.0
RAMP_PER_WEEK = 1.10
CUTBACK_EVERY = 4
CUTBACK_FACTOR = 0.75

# Quality sessions. The warm-up and cool-down are easy running of at least
# 1500 m and 800 m, rounded up to a quarter unit (1 mile and 0.5 mile, or
# 1.5 km and 1 km). A tempo block is 45% of the day and at least 2400 m,
# rounded up to a half unit (1.5 miles or 2.5 km). Intervals are 800 m reps
# at 5K effort with 400 m jogs between them, over up to 60% of the day, 3 to
# 8 reps.
WARM_UP_M = 1500.0
COOL_DOWN_M = 800.0
TEMPO_MIN_M = 2400.0
TEMPO_SHARE = 0.45
INTERVAL_SHARE = 0.6
REP_M = 800
JOG_M = 400
MIN_REPS = 3
MAX_REPS = 8


def week_start(day):
    return day - timedelta(days=day.weekday())


def unit_meters(metric):
    return 1000.0 if metric else METERS_PER_MILE


def to_step(value, step=0.5, rounding=round):
    """Rounds value to a multiple of step."""
    return rounding(value / step) * step


def distance_text(value, metric):
    """A distance in the athlete's unit as the calendar shows it: '1 mile', '2.5 miles', '8 km'."""
    text = f"{value:g}"
    return f"{text} km" if metric else f"{text} {'mile' if value == 1 else 'miles'}"


def round_distance(meters, metric):
    """Rounds to a half unit in the athlete's unit and formats it for the calendar."""
    return distance_text(to_step(meters / unit_meters(metric)), metric)


def weekly_targets(current_weekly_m, weeks, profile, taper_weeks):
    """Weekly distance targets in meters, ramped, with cutbacks and a taper."""
    base = max(current_weekly_m, MIN_WEEKLY_M)
    peak = base * profile["peak_multiplier"]
    targets = []
    volume = base
    reached = base
    for index in range(weeks):
        weeks_left = weeks - index
        if weeks_left <= taper_weeks:
            # Taper from the biggest week the plan reached: race week at
            # half of it, the week before at 70%, a third marathon taper
            # week at 85%. A short plan never gets to the peak.
            factor = {1: 0.5, 2: 0.7, 3: 0.85}[weeks_left]
            targets.append(reached * factor)
            continue
        if index > 0 and index % CUTBACK_EVERY == CUTBACK_EVERY - 1:
            targets.append(volume * CUTBACK_FACTOR)
            continue
        targets.append(volume)
        reached = max(reached, volume)
        volume = min(peak, volume * RAMP_PER_WEEK)
    return targets


def session_description(kind, race_label):
    if kind == "Easy Run":
        return "Conversational pace, heart rate in zone 2. Finish feeling like you could do it again."
    if kind == "Long Run":
        return "Steady and relaxed; practice fuelling. Last 15 minutes at a comfortably firm effort."
    if kind == "Race":
        return f"Race day: {race_label}. Start controlled, even effort, empty the tank in the last third."
    return ""


def shortest_ends(metric):
    """The shortest warm-up and cool-down in the athlete's unit."""
    unit_m = unit_meters(metric)
    return to_step(WARM_UP_M / unit_m, 0.25, math.ceil), to_step(COOL_DOWN_M / unit_m, 0.25, math.ceil)


def easy_ends(easy, metric):
    """Splits the easy running around a quality block into a warm-up and a
    cool-down in quarter units: the cool-down is half of it, rounded down to
    a half unit and not shorter than the shortest cool-down; the warm-up is
    the rest."""
    cool_down = max(shortest_ends(metric)[1], to_step(easy / 2, 0.5, math.floor))
    return to_step(easy - cool_down, 0.25), cool_down


def interval_description(distance, metric):
    """The description of an intervals day of distance (in the athlete's
    unit, to the half unit), or None when three reps do not fit between the
    shortest warm-up and cool-down."""
    unit_m = unit_meters(metric)
    room_m = min(INTERVAL_SHARE * distance, distance - sum(shortest_ends(metric))) * unit_m
    reps = min(MAX_REPS, int((room_m + JOG_M) // (REP_M + JOG_M)))
    if reps < MIN_REPS:
        return None
    block = (reps * REP_M + (reps - 1) * JOG_M) / unit_m
    warm_up, cool_down = easy_ends(distance - block, metric)
    return (
        f"Warm up {distance_text(warm_up, metric)} easy, then {reps} x {REP_M} m at 5K effort "
        f"with {JOG_M} m jog recoveries, cool down {distance_text(cool_down, metric)} easy."
    )


def tempo_description(distance, metric):
    """The description of a tempo day of distance (in the athlete's unit, to
    the half unit), or None when the shortest tempo block does not fit
    between the shortest warm-up and cool-down."""
    room = to_step(distance - sum(shortest_ends(metric)), 0.5, math.floor)
    tempo_min = to_step(TEMPO_MIN_M / unit_meters(metric), 0.5, math.ceil)
    if room < tempo_min:
        return None
    tempo = max(tempo_min, min(to_step(TEMPO_SHARE * distance), room))
    warm_up, cool_down = easy_ends(distance - tempo, metric)
    return (
        f"Warm up {distance_text(warm_up, metric)} easy, then {distance_text(tempo, metric)} at threshold "
        f"(comfortably hard, about one-hour race effort), cool down {distance_text(cool_down, metric)} easy."
    )


def easy_runs(week_target, long_m, quality_m, quality_sessions, count, metric):
    """The meters of a week's easy runs: whole half units, as even as
    possible, that bring the week to its target to the half unit once the
    long run and the quality days are rounded. The runs farther from the
    long run take any extra half unit. Each run is at least half a unit, so
    a week too small for its run days goes over its target."""
    unit_m = unit_meters(metric)
    steps = round(week_target / unit_m * 2) - round(long_m / unit_m * 2) - quality_sessions * round(quality_m / unit_m * 2)
    base, extra = divmod(steps, count)
    return [max(1, base + (ii >= count - extra)) * unit_m / 2 for ii in range(count)]


def build_plan(start, race_date, race_length, current_weekly_m, metric=False, days_per_week=5, long_run_day=6, quality_sessions=2, race_label=None):
    """Entries for every day from start to race_date inclusive.

    start/race_date are dates; long_run_day is Python's weekday (0 Monday,
    6 Sunday); days_per_week counts running days including the long run;
    quality_sessions is 1 or 2. Returns a list of dicts with date (ISO),
    workout_type, training_focus, approximate_distance and description.
    """
    profile = RACE_PROFILES[race_length.lower()]
    race_label = race_label or race_length
    days_per_week = max(3, min(7, days_per_week))
    quality_sessions = max(0, min(2, quality_sessions, days_per_week - 2))

    first_week = week_start(start)
    last_week = week_start(race_date)
    weeks = (last_week - first_week).days // 7 + 1
    taper_weeks = 3 if profile["taper_days"] > 14 else 2 if profile["taper_days"] > 7 else 1
    taper_weeks = min(taper_weeks, max(1, weeks - 1))
    targets = weekly_targets(current_weekly_m, weeks, profile, taper_weeks)

    # Quality days sit two and four days before the long run; easy days fill
    # the remaining training days closest to the long run; the rest is rest.
    quality_days = [(long_run_day - 5) % 7, (long_run_day - 3) % 7][:quality_sessions]
    other_days = [(long_run_day - offset) % 7 for offset in (1, 2, 4, 6, 3, 5) if (long_run_day - offset) % 7 not in quality_days]
    easy_days = other_days[: max(0, days_per_week - 1 - quality_sessions)]

    entries = []
    for index in range(weeks):
        week_target = targets[index]
        monday = first_week + timedelta(weeks=index)
        race_week = monday == last_week
        long_m = min(week_target * profile["long_run_share"], MAX_LONG_RUN_M[race_length.lower()])
        quality_m = week_target * 0.18
        easy_total = max(0.0, week_target - long_m - quality_m * quality_sessions)
        easy_m = easy_total / len(easy_days) if easy_days else 0.0
        easy = dict(zip(easy_days, easy_runs(week_target, long_m, quality_m, quality_sessions, len(easy_days), metric)))
        for offset in range(7):
            day = monday + timedelta(days=offset)
            if day < start or day > race_date:
                continue
            if day == race_date:
                entries.append(entry(day, "Race", race_label, profile["distance_m"], metric, race_label))
                continue
            if race_week and (race_date - day).days <= 1:
                entries.append(entry(day, "Rest", "Race prep", None, metric, race_label, "Rest or a 15-minute shakeout. Lay out your kit."))
                continue
            if offset == long_run_day and not race_week:
                entries.append(entry(day, "Long Run", "Endurance", long_m, metric, race_label))
            elif offset in quality_days:
                kind = profile["quality"][quality_days.index(offset) % len(profile["quality"])]
                entries.append(quality_entry(day, kind, quality_m * (0.7 if race_week else 1.0), metric))
            elif offset in easy_days or (race_week and offset == long_run_day):
                entries.append(entry(day, "Easy Run", "Aerobic base", easy_m if race_week else easy[offset], metric, race_label))
            else:
                entries.append(entry(day, "Rest", "Recovery", None, metric, race_label))
    return entries


def quality_entry(day, kind, meters, metric):
    """A tempo or intervals day, or an easy run with strides when the day is
    too short for the session."""
    distance = to_step(meters / unit_meters(metric))
    description = tempo_description(distance, metric) if kind == "Tempo" else interval_description(distance, metric)
    if description is None:
        strides = f"Easy {distance_text(distance, metric)} with 4 x 20 s strides near the end: fast but relaxed, walking back between them."
        return entry(day, "Easy Run", "Leg speed", meters, metric, None, strides)
    return entry(day, kind, "Threshold" if kind == "Tempo" else "VO2 max", meters, metric, None, description)


def entry(day, kind, focus, meters, metric, race_label, description=None):
    return {
        "date": day.isoformat(),
        "workout_type": kind,
        "training_focus": focus,
        "approximate_distance": round_distance(meters, metric) if meters else None,
        "description": description if description is not None else session_description(kind, race_label),
    }


def summarize(entries, metric=False):
    """Weeks, peak weekly distance and session counts, for the confirmation message."""
    from collections import defaultdict
    weekly = defaultdict(float)
    counts = defaultdict(int)
    unit_m = 1000.0 if metric else METERS_PER_MILE
    for item in entries:
        counts[item["workout_type"]] += 1
        if item["approximate_distance"]:
            amount = float(item["approximate_distance"].split()[0])
            weekly[week_start(date.fromisoformat(item["date"]))] += amount * unit_m
    return {
        "weeks": len(weekly),
        "peak_weekly_m": max(weekly.values()) if weekly else 0.0,
        "sessions": dict(counts),
    }
