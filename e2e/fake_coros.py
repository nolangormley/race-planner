"""A stand-in for the COROS Training Hub endpoints src/coros.py uses, with
two weeks of synthetic nights. The app is pointed at it through COROS_API_BASE."""
import hashlib
from datetime import date, datetime, timedelta

from fastapi import FastAPI, Header, Query

EMAIL = "coros@example.com"
PASSWORD = "coros-pass"
TOKEN = "fake-coros-token"
USER_ID = "777"
NIGHTS = 14


def synthetic_nights(today):
    nights = []
    for offset in range(NIGHTS):
        day = today - timedelta(days=offset)
        nights.append({
            "happenDay": int(day.strftime("%Y%m%d")),
            "rhr": 48 + offset % 4,
            "avgSleepHrv": 58 + offset % 7,
            "sleepHrvBase": 60,
            "tib": 420 + 10 * (offset % 3),
            "trainingLoad": 40 + offset,
            "vo2max": 51,
            "staminaLevel": 80,
        })
    return nights


def make_app():
    app = FastAPI()

    def refused():
        return {"result": "1003", "message": "token expired"}

    @app.post("/account/login")
    async def login(body: dict):
        if body.get("account") == EMAIL and body.get("pwd") == hashlib.md5(PASSWORD.encode()).hexdigest():
            return {"result": "0000", "message": "OK", "data": {"accessToken": TOKEN, "userId": USER_ID}}
        return {"result": "1001", "message": "account or password error"}

    @app.get("/dashboard/query")
    def dashboard(accesstoken: str = Header(None)):
        if accesstoken != TOKEN:
            return refused()
        return {"result": "0000", "data": {"summaryInfo": {}}}

    @app.get("/analyse/dayDetail/query")
    def day_detail(startDay: str = Query(...), endDay: str = Query(...), accesstoken: str = Header(None)):
        if accesstoken != TOKEN:
            return refused()
        start = datetime.strptime(startDay, "%Y%m%d").date()
        end = datetime.strptime(endDay, "%Y%m%d").date()
        nights = [night for night in synthetic_nights(date.today()) if start <= datetime.strptime(str(night["happenDay"]), "%Y%m%d").date() <= end]
        return {"result": "0000", "data": {"dayList": nights}}

    return app
