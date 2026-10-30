import hashlib
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

from src import coros
from src.pipeline import StravaDB
from src.api.services import coros_sync
from src.api.services.metrics import coros_resting_hr, resolve_resting_hr


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, json, headers))
        return self.responses.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params, headers))
        return self.responses.pop(0)


def test_login_hashes_the_password_and_keeps_only_the_token():
    session = FakeSession([FakeResponse({"result": "0000", "data": {"accessToken": "tok", "userId": 42}})])
    auth = coros.login("me@example.com", "hunter2", "us", session=session)
    assert auth == {"access_token": "tok", "user_id": "42"}
    _, url, body, _ = session.calls[0]
    assert url == "https://teamapi.coros.com/account/login"
    assert body == {"account": "me@example.com", "accountType": 2, "pwd": hashlib.md5(b"hunter2").hexdigest()}


def test_refused_login_and_expired_token_are_auth_errors():
    session = FakeSession([FakeResponse({"result": "1001", "message": "account or password error"})])
    with pytest.raises(coros.CorosAuthError):
        coros.login("me@example.com", "wrong", session=session)
    session = FakeSession([FakeResponse({"result": "1003", "message": "token expired"})])
    with pytest.raises(coros.CorosAuthError):
        coros.CorosClient("old", "42", session=session).verify()
    session = FakeSession([FakeResponse({}, status_code=429)])
    with pytest.raises(coros.CorosError, match="rate limit"):
        coros.CorosClient("tok", "42", session=session).verify()


def test_network_failures_are_coros_errors():
    class Unreachable:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("connection reset")

        def get(self, *args, **kwargs):
            raise requests.Timeout("read timed out")

    with pytest.raises(coros.CorosError, match="Could not reach COROS"):
        coros.login("me@example.com", "pw", session=Unreachable())
    with pytest.raises(coros.CorosError, match="Could not reach COROS"):
        coros.CorosClient("tok", "42", session=Unreachable()).verify()


def test_daily_rows_are_normalized_and_fetched_in_chunks():
    session = FakeSession([
        FakeResponse({"result": "0000", "data": {"dayList": [
            {"happenDay": 20260101, "rhr": 48, "avgSleepHrv": 61, "sleepHrvBase": 58, "tib": 430, "trainingLoad": 55, "vo2max": 0, "staminaLevel": 80},
        ]}}),
        FakeResponse({"result": "0000", "data": {"dayList": [{"happenDay": "20260401", "rhr": 0, "avgSleepHrv": None}]}}),
    ])
    client = coros.CorosClient("tok", "42", "eu", session=session)
    rows = client.daily(date(2026, 1, 1), date(2026, 4, 30))

    assert [call[2] for call in session.calls] == [
        {"startDay": "20260101", "endDay": "20260331"},
        {"startDay": "20260401", "endDay": "20260430"},
    ]
    assert session.calls[0][1].startswith("https://teameuapi.coros.com/")
    assert session.calls[0][3]["accessToken"] == "tok"
    assert json.loads(session.calls[0][3]["yfheader"]) == {"userId": "42"}
    assert rows[0] == {"day": date(2026, 1, 1), "rhr": 48.0, "hrv": 61.0, "hrv_baseline": 58.0, "time_in_bed_min": None, "training_load": 55.0, "vo2max": None, "stamina": 80.0}
    assert rows[1]["day"] == date(2026, 4, 1) and rows[1]["rhr"] is None and rows[1]["hrv"] is None


class FakeClient:
    def __init__(self, rows, error=None):
        self.rows = rows
        self.error = error
        self.ranges = []

    def daily(self, start, end):
        self.ranges.append((start, end))
        if self.error:
            raise self.error
        return [row for row in self.rows if start <= row["day"] <= end]


class FakeDb:
    def commit(self):
        pass

    def rollback(self):
        pass


def nights(today, count, rhr):
    return [
        {"day": today - timedelta(days=offset), "rhr": rhr, "hrv": 60, "hrv_baseline": 58, "time_in_bed_min": 430, "training_load": 40, "vo2max": None, "stamina": 80}
        for offset in range(count)
    ]


def athlete(**overrides):
    values = {"id": 1, "strava_athlete_id": 424242, "resting_hr": None, "max_hr": None, "coros_synced_at": None, "coros_error": None, "coros_access_token": "tok"}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_sync_stores_nights_and_the_resting_heart_rate_becomes_the_default():
    warehouse = StravaDB(":memory:")
    now = datetime.now().replace(microsecond=0)
    user = athlete()
    changes = []

    summary = coros_sync.sync_coros(FakeDb(), user, FakeClient(nights(now.date(), 20, 48)), warehouse.con, now, on_resting_hr_change=lambda rhr: changes.append(rhr) or True)

    assert summary["rows"] == 20 and summary["resting_hr"] == 48 and summary["recompute_started"]
    assert changes == [48]
    assert user.coros_synced_at == now and user.coros_error is None
    assert warehouse.con.execute("SELECT COUNT(*) FROM coros_daily").fetchone()[0] == 20
    assert coros_resting_hr(warehouse.con, 424242) == 48
    assert resolve_resting_hr(warehouse.con, user) == 48
    user.resting_hr = 52
    assert resolve_resting_hr(warehouse.con, user) == 52          # a typed value wins
    user.resting_hr = None

    # The next sync only asks for the last few nights and does not recompute
    # when the median barely moves.
    client = FakeClient(nights(now.date(), 25, 47))
    summary = coros_sync.sync_coros(FakeDb(), user, client, warehouse.con, now, on_resting_hr_change=lambda rhr: changes.append(rhr) or True)
    assert client.ranges == [(now.date() - timedelta(days=3), now.date())]
    assert warehouse.con.execute("SELECT COUNT(*) FROM coros_daily").fetchone()[0] == 20
    assert not summary["recompute_started"] and changes == [48]


def test_sync_records_an_expired_session_on_the_user():
    warehouse = StravaDB(":memory:")
    user = athlete()
    with pytest.raises(coros.CorosAuthError):
        coros_sync.sync_coros(FakeDb(), user, FakeClient([], error=coros.CorosAuthError("COROS: token expired")), warehouse.con)
    assert user.coros_error.startswith("Session expired.") and user.coros_synced_at is None
    assert not coros_sync.coros_sync_due(user, datetime(2026, 11, 2, 6, 0))


def test_nightly_sync_is_due_once_a_day_after_five():
    now = datetime(2026, 11, 2, 6, 0)
    user = athlete()
    assert coros_sync.coros_sync_due(user, now)
    assert not coros_sync.coros_sync_due(user, datetime(2026, 11, 2, 3, 0))
    user.coros_synced_at = now - timedelta(hours=10)
    assert not coros_sync.coros_sync_due(user, now)
    user.coros_synced_at = now - timedelta(hours=21)
    assert coros_sync.coros_sync_due(user, now)
    user.coros_access_token = None
    assert not coros_sync.coros_sync_due(user, now)


def test_unlink_forgets_the_nights_and_the_session():
    warehouse = StravaDB(":memory:")
    now = datetime.now()
    user = athlete(coros_user_id="42", coros_region="us", coros_linked_at=now)
    coros_sync.sync_coros(FakeDb(), user, FakeClient(nights(now.date(), 10, 45)), warehouse.con, now)
    changes = []
    coros_sync.unlink_coros(FakeDb(), user, warehouse.con, on_resting_hr_change=changes.append)
    assert warehouse.con.execute("SELECT COUNT(*) FROM coros_daily").fetchone()[0] == 0
    assert user.coros_access_token is None and user.coros_synced_at is None
    assert changes == [60]                                        # back to the plain default
