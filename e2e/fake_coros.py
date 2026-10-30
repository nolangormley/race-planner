"""A stand-in for the COROS Training Hub endpoints src/coros.py uses, with
synthetic nights (two weeks by default; scripts/demo.py asks for more).
The app is pointed at it through COROS_API_BASE."""
import hashlib
import math
from datetime import date, datetime, timedelta

from fastapi import FastAPI, Header, Query

EMAIL = "coros@example.com"
PASSWORD = "coros-pass"
TOKEN = "fake-coros-token"
USER_ID = "777"
NIGHTS = 14


def synthetic_nights(today, count=NIGHTS):
    """count nights ending today, newest first. Resting heart rate and HRV
    move with a weekly rhythm and improve a little toward today. There is no
    sleep duration, as in the real dayDetail (see src/coros.py)."""
    nights = []
    for offset in range(count):
        day = today - timedelta(days=offset)
        week = 2 * math.pi * offset / 7
        nights.append({
            "happenDay": int(day.strftime("%Y%m%d")),
            "rhr": round(48 + 2 * math.sin(week) + offset / 30),
            "avgSleepHrv": round(61 + 5 * math.cos(week) - offset / 20),
            "sleepHrvBase": round(60 - offset / 25),
            "trainingLoad": 35 + (offset * 13) % 30,
            "vo2max": 51 - offset // 45,
            "staminaLevel": 80,
        })
    return nights


def make_app(nights=NIGHTS):
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
        listed = [night for night in synthetic_nights(date.today(), nights) if start <= datetime.strptime(str(night["happenDay"]), "%Y%m%d").date() <= end]
        return {"result": "0000", "data": {"dayList": listed}}

    return app
