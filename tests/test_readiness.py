from datetime import date, datetime, timedelta
from types import SimpleNamespace

from src.readiness import readiness, session_hint
from src.pipeline import StravaDB
from src.api.services import coros_sync
from src.api.services.readiness import readiness_for


def test_good_night_and_fresh_legs_are_a_go():
    call = readiness(hrv=66, hrv_baseline=60, rhr=47, rhr_usual=49, sleep_min=450, tsb=8)
    assert call["label"] == "Go" and call["score"] == 4 and call["badge"] == "good"
    assert call["reasons"] == ["HRV at or above baseline", "resting heart rate at or under your usual", "7h 30m in bed", "form +8, fresh"]


def test_depressed_hrv_and_a_high_resting_heart_rate_say_easy():
    call = readiness(hrv=50, hrv_baseline=60, rhr=55, rhr_usual=49, sleep_min=400, tsb=-5)
    assert call["label"] == "Easy" and call["score"] == -2
    assert call["reasons"] == ["HRV 17% under baseline", "resting heart rate +6 bpm"]


def test_everything_off_says_rest():
    call = readiness(hrv=44, hrv_baseline=60, rhr=58, rhr_usual=49, sleep_min=280, tsb=-35)
    assert call["label"] == "Rest" and call["score"] == -8
    assert "only 4h 40m in bed" in call["reasons"] and "form -35, overreaching" in call["reasons"]


def test_no_overnight_reading_means_no_call():
    assert readiness(tsb=-20) is None
    assert readiness(sleep_min=400, tsb=3) is None
    assert readiness(hrv=60, hrv_baseline=None, rhr=None, rhr_usual=49) is None


def test_hint_only_for_hard_sessions_on_easy_or_rest_days():
    assert "swapping Intervals for an easy run" in session_hint("Easy", "Intervals")
    assert "swapping Long Run for a rest day" in session_hint("Rest", "Long Run")
    assert session_hint("Easy", "Easy Run") is None
    assert session_hint("Go", "Intervals") is None
    assert session_hint("Easy", None) is None


def nights(today, count, hrv, rhr):
    return [
        {"day": today - timedelta(days=offset), "rhr": rhr, "hrv": hrv, "hrv_baseline": 60, "time_in_bed_min": 430, "training_load": 40, "vo2max": None, "stamina": 80}
        for offset in range(count)
    ]


def test_readiness_for_uses_last_night_and_the_usual_resting_heart_rate():
    warehouse = StravaDB(":memory:")
    today = date.today()
    assert readiness_for(warehouse.con, 424242, tsb=0, today=today) == {"available": False}

    coros_sync.upsert_daily(warehouse.con, 424242, nights(today, 14, hrv=62, rhr=48))
    coros_sync.upsert_daily(warehouse.con, 424242, [{"day": today, "rhr": 54, "hrv": 51, "hrv_baseline": 60, "time_in_bed_min": 350, "training_load": 40, "vo2max": None, "stamina": 80}])
    call = readiness_for(warehouse.con, 424242, tsb=-12, today=today, planned_workout="Tempo")
    assert call["available"] and call["night"] == today.isoformat()
    assert call["label"] == "Easy"                     # HRV -15%, +6 bpm, short night, carrying load
    assert call["inputs"]["rhr_usual"] == 48
    assert "swapping Tempo for an easy run" in call["hint"]

    stale = readiness_for(warehouse.con, 424242, today=today + timedelta(days=5))
    assert stale == {"available": False}
