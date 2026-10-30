from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.database import Base, User
from src.api.services import digest

MONDAY_MORNING = datetime(2026, 11, 2, 7, 30)


def user_like(**overrides):
    values = {"weekly_digest": True, "email": "runner@example.com", "digest_sent_at": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_digest_is_due_monday_morning_once_a_week():
    assert digest.digest_due(user_like(), MONDAY_MORNING)
    assert not digest.digest_due(user_like(weekly_digest=False), MONDAY_MORNING)
    assert not digest.digest_due(user_like(), datetime(2026, 11, 2, 5, 0))                                 # before the send hour
    assert not digest.digest_due(user_like(), datetime(2026, 11, 3, 7, 30))                                # Tuesday
    assert not digest.digest_due(user_like(digest_sent_at=datetime(2026, 11, 2, 6, 5)), MONDAY_MORNING)    # already sent this week
    assert digest.digest_due(user_like(digest_sent_at=datetime(2026, 10, 26, 6, 5)), MONDAY_MORNING)       # sent last week


def test_compose_digest_reads_like_a_summary():
    week = {"runs": 4, "distance_m": 28.4 * 1609.344, "moving_s": 4 * 3600 + 12 * 60, "climb_m": 381}
    previous = {"runs": 3, "distance_m": 25.3 * 1609.344, "moving_s": 3 * 3600, "climb_m": 300}
    plan = {"planned": 4, "done": 3, "planned_m": 30 * 1609.344}
    sessions = [(date(2026, 11, 3), "Tempo", "5 miles"), (date(2026, 11, 8), "Long Run", "10 miles")]
    races = [("Winter Half", "half marathon", date(2027, 1, 24))]
    records = [("5K", 22 * 60 + 31, date(2026, 10, 28))]
    status = {"fitness_ctl": 54.2, "fatigue_atl": 61.0, "form_tsb": -6.8, "target_category": "Productive"}

    subject, body = digest.compose_digest(
        "Nolan Gormley", date(2026, 11, 2), week, previous, plan, sessions, races, records, status, app_url="https://example.test",
        recovery={"rhr": 48.2, "hrv": 61.7, "hrv_baseline": 58, "sleep_min": 430},
    )

    assert subject == "Your week in running: 28.4 mi over 4 runs"
    assert "Hi Nolan," in body
    assert "Last week (Oct 26 to Nov 1)" in body
    assert "Distance   28.4 mi (+3.1 mi on the week before)" in body
    assert "Time       4h 12m" in body
    assert "Climb      1,250 ft" in body
    assert "3 of 4 planned sessions done, 28.4 mi of 30.0 mi planned" in body
    assert "Form       -7  (neutral)" in body
    assert "Resting HR 48 bpm" in body
    assert "HRV        62 ms (baseline 58)" in body
    assert "In bed     7h 10m a night" in body
    assert "22:31 on Oct 28" in body
    assert "Tue  Tempo · 5 miles" in body
    assert "Winter Half · half marathon · in 83 days (Jan 24)" in body
    assert body.endswith("Race Planner  https://example.test")


def test_compose_digest_reports_the_morning_energy_and_the_call():
    empty = {"runs": 0, "distance_m": 0.0, "moving_s": 0.0, "climb_m": 0.0}
    today = {
        "morning": 64,
        "reasons": ["+31 overnight: HRV 12% under your baseline", "-33 Intervals, yesterday"],
        "call": {"label": "Easy", "hint": "Readiness says easy today: consider swapping Tempo for an easy run."},
    }
    _, body = digest.compose_digest("Nolan", date(2026, 11, 2), empty, empty, None, [], [], [], None, energy=today)
    assert (
        "Recovery (nightly averages)\n"
        "  Today      Easy, energy 64 of 100 on waking\n"
        "             +31 overnight: HRV 12% under your baseline\n"
        "             -33 Intervals, yesterday\n"
        "             Readiness says easy today: consider swapping Tempo for an easy run.\n"
    ) in body


def test_compose_digest_without_data_still_reads():
    empty = {"runs": 0, "distance_m": 0.0, "moving_s": 0.0, "climb_m": 0.0}
    subject, body = digest.compose_digest(None, date(2026, 11, 2), empty, empty, None, [], [], [], None)
    assert subject == "Your week in running: 0.0 mi over 0 runs"
    assert "Hi there," in body
    assert "Nothing planned yet" in body
    assert "Fitness" not in body and "Races" not in body


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(digest, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()


def test_send_due_digests_sends_once_and_stamps_the_user(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(digest, "send_email", lambda to, subject, body: sent.append((to, subject)))
    monkeypatch.setattr(digest, "build_digest", lambda db, user, now=None: ("Your week", "body"))
    db_session.add_all([
        User(email="in@example.com", password="x", name="In", weekly_digest=True),
        User(email="out@example.com", password="x", name="Out", weekly_digest=False),
    ])
    db_session.commit()

    assert digest.send_due_digests(MONDAY_MORNING) == 1
    assert sent == [("in@example.com", "Your week")]
    assert digest.send_due_digests(MONDAY_MORNING) == 0
    stamped = db_session.query(User).filter_by(email="in@example.com").one()
    assert stamped.digest_sent_at == MONDAY_MORNING
