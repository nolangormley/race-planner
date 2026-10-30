"""COROS Training Hub client for the parts Race Planner reads.

This is the private API behind the Training Hub web app, not a public
one: COROS can change it at any time, it rate-limits, and logging in on
the web or in the phone app can invalidate a token held here. We log in
once with the athlete's email and password, keep only the access token,
and read the daily wellness rows (resting heart rate, overnight HRV,
time in bed, training load) that the watch uploads each night.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta

import requests

BASE_URLS = {
    "us": "https://teamapi.coros.com",
    "eu": "https://teameuapi.coros.com",
    "asia": "https://teamcnapi.coros.com",
}
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
OK_RESULT = "0000"
TIMEOUT_S = 30
CHUNK_DAYS = 90


class CorosError(Exception):
    """COROS refused the request or answered with something unexpected."""


class CorosAuthError(CorosError):
    """The login was refused or the token is no longer valid."""


def base_url(region):
    # COROS_API_BASE points every region at a stand-in server in tests.
    override = os.getenv("COROS_API_BASE")
    if override:
        return override.rstrip("/")
    if region not in BASE_URLS:
        raise CorosError(f"Unknown COROS region {region!r}; use one of {', '.join(BASE_URLS)}.")
    return BASE_URLS[region]


def parse_response(response):
    """The JSON body of a COROS reply, or the matching error."""
    if response.status_code == 429:
        raise CorosError("COROS is rate limiting requests; try again in a few minutes.")
    if response.status_code in (401, 403):
        raise CorosAuthError("COROS rejected the session; connect again.")
    try:
        body = response.json()
    except ValueError:
        raise CorosError(f"COROS answered HTTP {response.status_code} without JSON.")
    result = str(body.get("result", ""))
    if result != OK_RESULT:
        message = body.get("message") or f"result {result}"
        if any(word in message.lower() for word in ("token", "login", "log in", "expired", "password", "account")):
            raise CorosAuthError(f"COROS: {message}")
        raise CorosError(f"COROS: {message}")
    return body


def login(email, password, region="us", session=None):
    """Logs in the way the web app does and returns {"access_token", "user_id"}.
    The password is sent to COROS once (MD5-hashed, as their app does) and
    not kept."""
    http = session or requests.Session()
    response = http.post(
        f"{base_url(region)}/account/login",
        json={"account": email, "accountType": 2, "pwd": hashlib.md5(password.encode()).hexdigest()},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_S,
    )
    data = parse_response(response).get("data") or {}
    if not data.get("accessToken"):
        raise CorosAuthError("COROS did not return a session token.")
    return {"access_token": data["accessToken"], "user_id": str(data.get("userId") or "")}


def number(value, zero_is_missing=True):
    if value is None or value == "":
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if zero_is_missing and value == 0:
        return None
    return value


def parse_day(value):
    return datetime.strptime(str(value), "%Y%m%d").date()


def normalize_day(item):
    """One dayList entry as our column names; missing or zero readings are None."""
    return {
        "day": parse_day(item["happenDay"]),
        "rhr": number(item.get("rhr")),
        "hrv": number(item.get("avgSleepHrv")),
        "hrv_baseline": number(item.get("sleepHrvBase")),
        "time_in_bed_min": number(item.get("tib")),
        "training_load": number(item.get("trainingLoad")),
        "vo2max": number(item.get("vo2max")),
        "stamina": number(item.get("staminaLevel")),
    }


class CorosClient:
    def __init__(self, access_token, user_id, region="us", session=None):
        self.access_token = access_token
        self.user_id = user_id
        self.base = base_url(region)
        self.http = session or requests.Session()

    def headers(self):
        return {"accessToken": self.access_token, "yfheader": json.dumps({"userId": self.user_id}), "User-Agent": USER_AGENT}

    def get(self, path, params=None):
        response = self.http.get(f"{self.base}{path}", params=params, headers=self.headers(), timeout=TIMEOUT_S)
        return parse_response(response).get("data") or {}

    def verify(self):
        """Raises CorosAuthError when the token no longer works."""
        self.get("/dashboard/query")

    def daily(self, start, end):
        """Wellness rows for every day from start to end inclusive, oldest
        first, fetched in 90-day chunks."""
        rows = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=CHUNK_DAYS - 1))
            data = self.get("/analyse/dayDetail/query", {"startDay": chunk_start.strftime("%Y%m%d"), "endDay": chunk_end.strftime("%Y%m%d")})
            rows.extend(normalize_day(item) for item in data.get("dayList") or [] if item.get("happenDay"))
            chunk_start = chunk_end + timedelta(days=1)
        rows.sort(key=lambda row: row["day"])
        return rows
