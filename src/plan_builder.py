"""Builds a training plan skeleton from a start date to race day.

Deterministic and inspectable, no language model. Weekly volume ramps from
the athlete's current weekly distance toward a peak that suits the race
distance, no faster than 10% a week, with a cutback every fourth week and a
taper before the race. Each week has a long run, one or two quality
sessions, easy runs on the other training days and rest otherwise. The
output is a list of day entries the calendar stores as-is, so every session
can be edited afterwards.
"""
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


def week_start(day):
    return day - timedelta(days=day.weekday())


def round_distance(meters, metric):
    """Rounds to a half unit in the athlete's unit and formats it for the calendar."""
    unit_m = 1000.0 if metric else METERS_PER_MILE
    value = round(meters / unit_m * 2) / 2
    text = f"{value:g}"
    return f"{text} km" if metric else f"{text} {'mile' if value == 1 else 'miles'}"


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


def session_description(kind, meters, metric, race_label):
    unit = "km" if metric else "mi"
    if kind == "Easy Run":
        return "Conversational pace, heart rate in zone 2. Finish feeling like you could do it again."
    if kind == "Long Run":
        return "Steady and relaxed; practice fuelling. Last 15 minutes at a comfortably firm effort."
    if kind == "Tempo":
        tempo_m = meters * 0.45
        return f"Warm up 15 min easy, then {round_distance(tempo_m, metric)} at threshold (comfortably hard, 25-30 min race effort), cool down 10 min."
    if kind == "Intervals":
        reps = max(4, min(8, int(meters / 1600)))
        return f"Warm up 15 min, then {reps} x 800 m at 5K effort with 400 m jog recovery, cool down 10 min."
    if kind == "Race":
        return f"Race day: {race_label}. Start controlled, even effort, empty the tank in the last third."
    return ""


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
                focus = "Threshold" if kind == "Tempo" else "VO2 max"
                entries.append(entry(day, kind, focus, quality_m * (0.7 if race_week else 1.0), metric, race_label))
            elif offset in easy_days or (race_week and offset == long_run_day):
                entries.append(entry(day, "Easy Run", "Aerobic base", easy_m, metric, race_label))
            else:
                entries.append(entry(day, "Rest", "Recovery", None, metric, race_label))
    return entries


def entry(day, kind, focus, meters, metric, race_label, description=None):
    return {
        "date": day.isoformat(),
        "workout_type": kind,
        "training_focus": focus,
        "approximate_distance": round_distance(meters, metric) if meters else None,
        "description": description if description is not None else session_description(kind, meters or 0, metric, race_label),
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
