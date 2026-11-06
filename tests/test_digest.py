from datetime import date, datetime
from types import SimpleNamespace

import duckdb
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.database import Base, User, Race, TrainingPlan, TrainingPlanEntry
from src.api.services import digest
from src.pipeline import StravaDB

MONDAY_MORNING = datetime(2026, 11, 2, 7, 30)
ATHLETE = 424242


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
    assert "HRV        62 ms (COROS baseline 58)" in body
    assert "In bed     7h 10m a night" in body
    assert "22:31 on Oct 28" in body
    assert "Tue  Tempo · 5 miles" in body
    assert "Winter Half · half marathon · in 83 days (Jan 24)" in body
    assert body.endswith("Race Planner  https://example.test")


def test_compose_digest_reports_the_morning_energy_and_the_call():
    empty = {"runs": 0, "distance_m": 0.0, "moving_s": 0.0, "climb_m": 0.0}
    today = {
        "morning": 64,
        "reasons": ["+31 overnight: HRV 12% under baseline", "-33 Intervals, yesterday"],
        "call": {"label": "Easy", "hint": "Readiness says easy today: consider swapping Tempo for an easy run."},
    }
    _, body = digest.compose_digest("Nolan", date(2026, 11, 2), empty, empty, None, [], [], [], None, energy=today)
    assert (
        "Recovery (nightly averages)\n"
        "  Today      Easy, energy 64 of 100 on waking\n"
        "             +31 overnight: HRV 12% under baseline\n"
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


def test_digest_html_shows_the_week_and_escapes_what_users_typed():
    week = {"runs": 4, "distance_m": 13.3 * 1609.344, "moving_s": 2 * 3600 + 39 * 60, "climb_m": 318}
    previous = {"runs": 4, "distance_m": 16.0 * 1609.344, "moving_s": 3 * 3600, "climb_m": 300}
    energy = {
        "morning": 58,
        "reasons": ["+63 overnight, no COROS reading, so the usual rate", "-26 Morning Run, yesterday", "HRV under baseline"],
        "call": {"label": "Easy", "badge": "warning", "advice": "Keep today easy and move quality to another day.",
                 "hint": "Intervals are planned today."},
    }

    html = digest.digest_html(
        "Nolan Gormley", date(2026, 11, 6), week, previous, {"planned": 2, "done": 1, "planned_m": 8 * 1609.344},
        [(date(2026, 11, 3), "Intervals", "3.5 miles")], [("<b>Turkey</b> Trot", "5k", date(2026, 11, 26))],
        [("5K", 22 * 60 + 31, date(2026, 10, 28))], {"fitness_ctl": 65.2, "fatigue_atl": 68.4, "form_tsb": -3.2},
        app_url="https://example.test/", recovery={"rhr": 48.2, "hrv": 76.1, "hrv_baseline": 71}, energy=energy,
    )

    assert "Hi Nolan, here is your week in running." in html
    assert "Last week · Oct 26 – Nov 1" in html and "13.3 mi" in html
    assert "2.7 mi less than the week before" in html
    assert "1 of 2 planned sessions done" in html and "13.3 of 8.0 mi planned" in html
    assert "−3" in html and "Training and recovery are in balance." in html
    assert ">+63<" in html and "Overnight, no COROS reading, so the usual rate" in html
    assert ">−26<" in html and "HRV under baseline" in html
    assert "Intervals are planned today." in html and "76 ms" in html and "Baseline 71" in html
    assert "22:31" in html and "3.5 miles" in html
    assert "&lt;b&gt;Turkey&lt;/b&gt; Trot" in html and "<b>Turkey" not in html
    assert "5K · Thu, Nov 26" in html and ">20<" in html
    assert 'href="https://example.test/dashboard"' in html and 'href="https://example.test/profile"' in html
    assert "13.3 mi over 4 runs · today: Easy · &lt;b&gt;Turkey&lt;/b&gt; Trot in 20 days" in html


def test_digest_html_without_data_still_reads():
    empty = {"runs": 0, "distance_m": 0.0, "moving_s": 0.0, "climb_m": 0.0}
    html = digest.digest_html(None, date(2026, 11, 2), empty, empty, None, [], [], [], None)
    assert "Hi there," in html and "The same distance as the week before" in html
    assert "Nothing planned yet." in html
    for absent in ("Fitness", "Recovery", "Races", "New bests", "planned sessions", "<img", "Open Race Planner", "None"):
        assert absent not in html, absent


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[User.__table__, Race.__table__, TrainingPlan.__table__, TrainingPlanEntry.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(digest, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()


def test_send_due_digests_sends_once_and_stamps_the_user(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(digest, "send_email", lambda to, subject, body, html: sent.append((to, subject, html)))
    monkeypatch.setattr(digest, "build_digest", lambda db, user, now=None: ("Your week", "body", "<p>html</p>"))
    db_session.add_all([
        User(email="in@example.com", password="x", name="In", weekly_digest=True),
        User(email="out@example.com", password="x", name="Out", weekly_digest=False),
    ])
    db_session.commit()

    assert digest.send_due_digests(MONDAY_MORNING) == 1
    assert sent == [("in@example.com", "Your week", "<p>html</p>")]
    assert digest.send_due_digests(MONDAY_MORNING) == 0
    stamped = db_session.query(User).filter_by(email="in@example.com").one()
    assert stamped.digest_sent_at == MONDAY_MORNING


def test_the_plan_line_counts_a_session_done_when_the_dashboard_does(db_session, tmp_path, monkeypatch):
    """The dashboard's week card (renderWeek in dashboard-tables.js) calls a
    session done when it is ticked or a run is on its day, except an
    intervals or tempo day whose runs were analysed without that structure."""
    path = str(tmp_path / "warehouse.duckdb")
    warehouse = StravaDB(path)
    warehouse.con.execute("INSERT INTO dim_athlete (athlete_id, sex) VALUES (?, 'M')", [ATHLETE])
    for number, (day, kind) in enumerate([
        (date(2026, 10, 27), "unchecked"),     # the structure pass has not looked at it
        (date(2026, 10, 28), None),            # looked at, nothing found
        (date(2026, 10, 29), "intervals"),
        (date(2026, 10, 31), None),
    ], start=1):
        activity_id = 20_000_000_000 + number
        warehouse.con.execute(
            "INSERT INTO dim_activity (activity_id, athlete_id, type, start_date_local, distance) VALUES (?, ?, 'Run', ?, 5000)",
            [activity_id, ATHLETE, datetime.combine(day, datetime.min.time()).replace(hour=7)],
        )
        if kind != "unchecked":
            warehouse.con.execute("INSERT INTO activity_structure (activity_id, kind, rep_count) VALUES (?, ?, 0)", [activity_id, kind])
    warehouse.con.close()
    monkeypatch.setattr(digest, "get_db_connection", lambda: duckdb.connect(path))
    monkeypatch.setattr(digest, "calculate_training_status_logic", lambda *args: None)

    user = User(id=1, email="runner@example.com", password="x", name="Runner", strava_athlete_id=ATHLETE)
    db_session.add_all([user, TrainingPlan(id=1, user_id=1, name="Plan")])
    db_session.add_all([
        TrainingPlanEntry(plan_id=1, date=day, workout_type=workout, is_completed=ticked) for day, workout, ticked in [
            (date(2026, 10, 26), "Rest", False),
            (date(2026, 10, 27), "Tempo", False),         # done: the run waits for the pass
            (date(2026, 10, 28), "Intervals", False),     # not done: ran, not the session
            (date(2026, 10, 29), "Intervals", False),     # done
            (date(2026, 10, 30), "Easy Run", False),      # not done: no run
            (date(2026, 10, 31), "Long Run", False),      # done: a run that day
            (date(2026, 11, 1), "Easy Run", True),        # done: ticked
        ]
    ])
    db_session.commit()

    _, body, _ = digest.build_digest(db_session, user, MONDAY_MORNING)
    assert "Plan       4 of 6 planned sessions done" in body


def activity(sport="Run", kind=None, checked=True):
    return {"type": sport, "kind": kind, "checked": checked}


@pytest.mark.parametrize("workout, runs, done", [
    ("Easy Run", [], False),
    ("Easy Run", [activity("Walk", checked=False)], True),                # any activity on foot, as on the dashboard
    ("Tempo", [activity(checked=False)], True),                           # waits for the structure pass
    ("Tempo", [activity(kind="intervals")], False),                       # ran, not the session
    ("Tempo", [activity(), activity(checked=False)], True),               # one run still waits
    ("Tempo", [activity(), activity("Walk", checked=False)], False),      # the pass never looks at a walk
    ("Intervals", [activity("VirtualRun", checked=False)], True),         # nothing looked at
])
def test_a_session_is_done_when_the_dashboard_says_so(workout, runs, done):
    assert digest.session_done(SimpleNamespace(workout_type=workout, is_completed=False), runs) is done


def test_record_labels_match_the_best_effort_distances():
    assert digest.distance_label(21097.5) == "Half marathon"
    assert digest.distance_label(1609.344) == "1 mile"
    assert digest.distance_label(5000.0) == "5K"
    assert digest.distance_label(15000.0) == "15 km"

