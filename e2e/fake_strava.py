"""A stand-in for the parts of Strava's API the pipeline uses, with a few
synthetic runs. The app is pointed at it through STRAVA_API_BASE."""
import math
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Query

ATHLETE_ID = 424242
RUN_SECONDS = 2400


def synthetic_runs(count=3):
    """Runs on different recent days: 40 minutes at ~3.3 m/s with a faster
    ten-minute stretch, heart rate around 150, flat course."""
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


def make_app():
    app = FastAPI()
    runs = synthetic_runs()

    @app.post("/oauth/token")
    def token():
        return {"access_token": "fake-access-token", "refresh_token": "fake-refresh-token", "expires_at": 4102444800}

    @app.get("/api/v3/athlete")
    def athlete():
        return {"id": ATHLETE_ID, "username": "e2e", "firstname": "E2E", "lastname": "Runner", "sex": "M", "weight": 70.0}

    @app.get("/api/v3/athlete/activities")
    def activities(page: int = Query(1), per_page: int = Query(30), after: int = Query(None)):
        return runs if page == 1 else []

    @app.get("/api/v3/activities/{activity_id}/streams")
    def streams(activity_id: int):
        return synthetic_streams(activity_id)

    return app
