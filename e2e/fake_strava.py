"""A stand-in for the parts of Strava's API the pipeline uses, with
synthetic runs. The app is pointed at it through STRAVA_API_BASE.

The browser tests use the default: three 40-minute runs a week apart.
scripts/demo.py asks for a longer, varied history (synthetic_runs with
span_days), whose streams follow each run's Strava workout_type."""
import math
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Query

ATHLETE_ID = 424242
RUN_SECONDS = 2400
HISTORY_ID_BASE = 9_100_000
METERS_PER_MILE = 1609.344
# Strava's workout_type values for runs.
EASY, RACE, LONG_RUN, WORKOUT = 0, 1, 2, 3
WARMUP_SECONDS = 600
COOLDOWN_SECONDS = 300


def synthetic_runs(count=3, span_days=None):
    """Runs on different recent days, newest first.

    Without span_days these are the browser-test runs: a week apart, 40
    minutes at ~3.3 m/s with a faster ten-minute stretch, heart rate around
    150, flat course. With span_days, count varied runs spread evenly over
    the last span_days days (see history_run)."""
    if span_days is not None:
        return [history_run(index, count, span_days) for index in range(count)]
    now = datetime.now(timezone.utc)
    runs = []
    for index in range(count):
        start = (now - timedelta(days=3 + 7 * index)).replace(hour=7, minute=30, second=0, microsecond=0)
        runs.append({
            "id": 9_000_000 + index,
            "name": f"E2E run {index + 1}",
            "type": "Run",
            "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "start_date_local": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "distance": 8000.0 + 200 * index,
            "moving_time": RUN_SECONDS,
            "elapsed_time": RUN_SECONDS + 60,
            "total_elevation_gain": 40.0,
            "average_speed": (8000.0 + 200 * index) / RUN_SECONDS,
            "max_speed": 4.2,
            "average_heartrate": 150.0,
            "max_heartrate": 185.0,
            "average_cadence": 85.0,
            "athlete": {"id": ATHLETE_ID},
        })
    return runs


def is_tempo(run):
    """Workouts alternate between tempo and intervals."""
    return run["workout_type"] == WORKOUT and run["id"] // 4 % 2 == 0


def race_distance(run):
    """Races alternate between a 5K and a 10K."""
    return 5000 if (run["id"] - HISTORY_ID_BASE) // 15 % 2 == 0 else 10000


def race_level(run):
    return 1.30 if race_distance(run) == 5000 else 1.22


def history_run(index, count, span_days):
    """One run of a varied history; index 0 is the most recent.

    About every sixth week is a 5K or 10K race, every fifth run a long run
    and every fourth a tempo or interval session; the rest are easy. Easy
    pace gets faster and easy heart rate lower from the oldest run to the
    newest, as in a block of training that works. Distance, maxima and climb
    come from the streams, so the summary and the streams agree."""
    days_ago = 1 + round(index * (span_days - 1) / max(1, count - 1))
    start = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(hour=7, minute=0, second=0, microsecond=0)
    fitness = 1 - days_ago / span_days
    kind = RACE if index % 15 == 11 else LONG_RUN if index % 5 == 2 else WORKOUT if index % 4 == 1 else EASY
    run = {
        "id": HISTORY_ID_BASE + index,
        "type": "Run",
        "workout_type": kind,
        "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_date_local": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "athlete": {"id": ATHLETE_ID},
    }
    easy_speed = 2.55 + 0.23 * fitness
    easy_heartrate = 148 - 3 * fitness
    if kind == RACE:
        run["moving_time"] = WARMUP_SECONDS + round(race_distance(run) / (race_level(run) * easy_speed)) + COOLDOWN_SECONDS
    else:
        run["moving_time"] = 60 * {EASY: 35 + 5 * (index % 3), LONG_RUN: 75 + 5 * (index % 4), WORKOUT: 50}[kind]
    run["elapsed_time"] = run["moving_time"] + (600 if kind == RACE else 90)
    profile = intensity_profile(run)
    run["average_speed"] = easy_speed * sum(profile) / len(profile)
    above_easy = {RACE: 16, LONG_RUN: 2, EASY: 0}.get(kind, 8 if is_tempo(run) else 10)
    run["average_heartrate"] = round(easy_heartrate + above_easy, 1)
    run["name"] = {EASY: "Easy run", LONG_RUN: "Long run", RACE: f"{race_distance(run) // 1000}K race"}.get(kind) or ("Tempo run" if is_tempo(run) else "6 x 3 min")

    streams = shaped_streams(run)
    velocity, heartrate, altitude = (streams[key]["data"] for key in ("velocity_smooth", "heartrate", "altitude"))
    run["distance"] = round(sum(velocity), 1)
    run["max_speed"] = round(max(velocity), 2)
    run["max_heartrate"] = float(max(heartrate))
    run["total_elevation_gain"] = round(sum(max(0.0, after - before) for before, after in zip(altitude, altitude[1:])), 1)
    run["average_cadence"] = round(sum(streams["cadence"]["data"]) / len(velocity), 1)
    return run


def intensity_profile(run):
    """Speed relative to easy pace for each second of the run. Tempo: 20
    minutes at threshold between a warm-up and a cool-down. Intervals: 6 x 3
    minutes hard with 2-minute jogs. Races: the race distance at an even
    effort between a warm-up and a cool-down. Long runs sit a little under
    easy pace."""
    wobble = (run["id"] % 97) / 97 * 2 * math.pi
    profile = []
    for second in range(run["moving_time"]):
        block = second - WARMUP_SECONDS
        if run["workout_type"] in (WORKOUT, RACE) and block < 0:
            level = 0.95
        elif run["workout_type"] == RACE:
            level = race_level(run) if second < run["moving_time"] - COOLDOWN_SECONDS else 0.88
        elif is_tempo(run):
            level = 1.25 if block < 1200 else 0.92
        elif run["workout_type"] == WORKOUT:
            level = (1.38 if block % 300 < 180 else 0.80) if block < 1800 else 0.92
        elif run["workout_type"] == LONG_RUN:
            level = 0.95
        else:
            level = 1.0
        profile.append(level * (1 + 0.025 * math.sin(second / 45 + wobble)))
    return profile


def shaped_streams(run):
    """Streams for a history run that agree with its summary: velocity
    averages average_speed and heart rate averages average_heartrate. Heart
    rate follows effort with a lag and drifts up over the run; the course rolls."""
    profile = intensity_profile(run)
    mean_level = sum(profile) / len(profile)
    velocity = [run["average_speed"] * level / mean_level for level in profile]
    wobble = (run["id"] % 97) / 97 * 2 * math.pi
    drift = 10 if run["workout_type"] == LONG_RUN else 5
    lagged, effort = [], -20.0
    for second, level in enumerate(profile):
        target = 90 * (level - 1) + drift * second / len(profile) + 2 * math.sin(second / 60 + wobble)
        effort += (target - effort) / 25
        lagged.append(effort)
    shift = run["average_heartrate"] - sum(lagged) / len(lagged)
    hills = 25 if run["workout_type"] == LONG_RUN else 12
    seconds = range(len(profile))
    return {
        "time": {"data": list(seconds)},
        "velocity_smooth": {"data": velocity},
        "heartrate": {"data": [round(value + shift) for value in lagged]},
        "altitude": {"data": [300 + hills * math.sin(second / 420 + wobble) + 4 * math.sin(second / 95 + 2 * wobble) for second in seconds]},
        "cadence": {"data": [max(76, min(95, round(84 + 12 * (level - 1)))) for level in profile]},
        "moving": {"data": [True] * len(profile)},
    }


def synthetic_streams(activity_id):
    seconds = list(range(RUN_SECONDS))
    velocity = [3.8 if 900 <= second < 1500 else 3.3 for second in seconds]
    heartrate = [int(150 + (12 if 900 <= second < 1500 else 0) + 3 * math.sin(second / 60)) for second in seconds]
    return {
        "time": {"data": seconds},
        "velocity_smooth": {"data": velocity},
        "heartrate": {"data": heartrate},
        "altitude": {"data": [300.0 + 2 * math.sin(second / 300) for second in seconds]},
        "cadence": {"data": [85] * RUN_SECONDS},
        "moving": {"data": [True] * RUN_SECONDS},
    }


def laps_between(streams, starts):
    """Laps from each start index to the next, with Strava's lap fields."""
    velocity, heartrate = streams["velocity_smooth"]["data"], streams["heartrate"]["data"]
    altitude, cadence = streams["altitude"]["data"], streams["cadence"]["data"]
    ends = starts[1:] + [len(velocity)]
    laps = []
    for index, (start, end) in enumerate(zip(starts, ends)):
        seconds = end - start
        distance = sum(velocity[start:end])
        laps.append({
            "lap_index": index + 1, "name": f"Lap {index + 1}", "start_index": start, "end_index": end - 1,
            "elapsed_time": seconds, "moving_time": seconds, "distance": distance, "average_speed": distance / seconds,
            "max_speed": max(velocity[start:end]), "average_heartrate": sum(heartrate[start:end]) / seconds,
            "max_heartrate": max(heartrate[start:end]), "average_cadence": sum(cadence[start:end]) / seconds,
            "total_elevation_gain": sum(max(0.0, after - before) for before, after in zip(altitude[start:end], altitude[start + 1:end])),
        })
    return laps


def mile_laps(streams):
    """Auto-laps every mile, as a watch records them, and the part-mile at the end."""
    velocity = streams["velocity_smooth"]["data"]
    starts, covered, next_mile = [0], 0.0, METERS_PER_MILE
    for second, speed in enumerate(velocity[:-1]):
        covered += speed
        if covered >= next_mile:
            starts.append(second + 1)
            next_mile += METERS_PER_MILE
    return laps_between(streams, starts)


def started_epoch(run):
    return datetime.strptime(run["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def make_app(runs=None):
    """The fake Strava app serving runs (default: the three browser-test runs)."""
    app = FastAPI()
    runs = runs if runs is not None else synthetic_runs()
    by_id = {run["id"]: run for run in runs}

    def streams_for(activity_id):
        run = by_id.get(activity_id)
        return shaped_streams(run) if run and "workout_type" in run else synthetic_streams(activity_id)

    @app.post("/oauth/token")
    def token():
        return {"access_token": "fake-access-token", "refresh_token": "fake-refresh-token", "expires_at": 4102444800}

    @app.get("/api/v3/athlete")
    def athlete():
        return {"id": ATHLETE_ID, "username": "e2e", "firstname": "E2E", "lastname": "Runner", "sex": "M", "weight": 70.0}

    @app.get("/api/v3/athlete/activities")
    def activities(page: int = Query(1), per_page: int = Query(30), after: int = Query(None)):
        listed = [run for run in runs if after is None or started_epoch(run) > after]
        first = (page - 1) * per_page
        return listed[first:first + per_page]

    @app.get("/api/v3/activities/{activity_id}/streams")
    def streams(activity_id: int):
        return streams_for(activity_id)

    @app.get("/api/v3/activities/{activity_id}/laps")
    def laps(activity_id: int):
        if "workout_type" in by_id.get(activity_id, {}):
            return mile_laps(streams_for(activity_id))
        return laps_between(streams_for(activity_id), list(range(0, RUN_SECONDS, 600)))

    return app
