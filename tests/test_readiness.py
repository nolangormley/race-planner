from datetime import datetime, timedelta

from src.readiness import call, session_hint
from src.pipeline import StravaDB
from src.api.services import coros_sync
from src.api.services.readiness import energy_for

ATHLETE_ID = 424242
NOW = datetime(2026, 10, 30, 12, 0)
TODAY = NOW.date()


def test_the_morning_level_picks_the_call():
    assert call(90, 0)["label"] == "Go" and call(89, 0)["label"] == "Normal"
    assert call(75, 0)["label"] == "Normal" and call(74, 0)["label"] == "Easy"
    assert call(55, 0)["label"] == "Easy" and call(54, 0)["label"] == "Rest"
    assert call(95, 0)["badge"] == "good" and call(40, 0)["badge"] == "serious"
    assert call(80, 0) == {"label": "Normal", "advice": "Train as planned.", "badge": "accent", "reasons": []}


def test_a_falling_trend_makes_the_call_one_step_more_cautious_but_never_rest():
    assert call(80, -9)["label"] == "Normal"
    stepped = call(80, -10)
    assert stepped["label"] == "Easy" and stepped["reasons"] == ["woke 10 under your usual 90"]
    assert call(60, -30)["label"] == "Easy"
    assert call(50, -30) == {**call(50, 0), "reasons": []}
    assert call(80, 15)["label"] == "Normal"                 # a rising trend does not promote


def test_hint_only_for_hard_sessions_on_easy_or_rest_days():
    assert "swapping Intervals for an easy run" in session_hint("Easy", "Intervals")
    assert "swapping Long Run for a rest day" in session_hint("Rest", "Long Run")
    assert session_hint("Easy", "Easy Run") is None
    assert session_hint("Go", "Intervals") is None
    assert session_hint("Easy", None) is None


def coros_nights(last_day, count, hrv, rhr):
    return [
        {"day": last_day - timedelta(days=offset), "rhr": rhr, "hrv": hrv, "hrv_baseline": 60, "time_in_bed_min": None, "training_load": 40, "vo2max": None, "stamina": 80}
        for offset in range(count)
    ]


def warehouse_with(nights, sessions=()):
    """An in-memory warehouse with COROS nights and (start, trimp, name) sessions of 45 minutes."""
    warehouse = StravaDB(":memory:")
    coros_sync.upsert_daily(warehouse.con, ATHLETE_ID, nights)
    warehouse.con.execute("INSERT INTO dim_athlete (athlete_id, sex) VALUES (?, 'M')", [ATHLETE_ID])
    for activity_id, (start, trimp, name) in enumerate(sessions, start=1):
        warehouse.con.execute(
            "INSERT INTO dim_activity (activity_id, athlete_id, name, type, start_date_local, moving_time, elapsed_time) VALUES (?, ?, ?, 'Run', ?, 2700, 2700)",
            [activity_id, ATHLETE_ID, name, start],
        )
        warehouse.con.execute("INSERT INTO activity_effectiveness (activity_id, trimp_banister) VALUES (?, ?)", [activity_id, trimp])
    return warehouse


def test_energy_for_without_coros_nights_is_unavailable():
    assert energy_for(StravaDB(":memory:").con, ATHLETE_ID, NOW) == {"available": False}


def test_energy_for_judges_last_night_against_the_nights_before_it():
    nights = coros_nights(TODAY - timedelta(days=1), 40, hrv=62, rhr=48) + coros_nights(TODAY, 1, hrv=50, rhr=54)
    sessions = [
        (datetime.combine(TODAY - timedelta(days=1), datetime.min.time()) + timedelta(hours=17, minutes=30), 150, "Track"),
        (datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=7, minutes=30), 60, "Shakeout"),
    ]
    result = energy_for(warehouse_with(nights, sessions).con, ATHLETE_ID, NOW, planned_workout="Tempo")

    assert result["available"]
    assert result["night"]["hrv_baseline"] == 62 and result["night"]["rhr_baseline"] == 48
    assert result["reasons"][0].endswith("HRV 19% under your baseline, resting heart rate 6 bpm over your baseline")
    assert "-33 Track, yesterday" in result["reasons"] and "-13 Shakeout, today" in result["reasons"]
    assert [drain["cost"] for drain in result["drains"]] == [33, 13]
    assert result["call"]["label"] in ("Easy", "Rest")
    assert "consider swapping Tempo" in result["call"]["hint"]
    assert result["series"][-1] == {"t": NOW, "level": result["series"][-1]["level"]}
    assert round(result["series"][-1]["level"]) == result["current"]


def test_a_stale_reading_keeps_the_curve_but_drops_the_call():
    warehouse = warehouse_with(coros_nights(TODAY - timedelta(days=4), 30, hrv=62, rhr=48))
    stale = energy_for(warehouse.con, ATHLETE_ID, NOW)
    assert stale["available"] and stale["call"] is None
    assert stale["night"]["summary"] == "no COROS reading, so the usual rate"
    later = NOW + timedelta(days=2)              # the newest reading is six days old
    assert energy_for(warehouse.con, ATHLETE_ID, later)["available"]
    assert energy_for(warehouse.con, ATHLETE_ID, later, days=6) == {"available": False}


def test_days_sets_the_length_of_the_curve():
    warehouse = warehouse_with(coros_nights(TODAY, 30, hrv=62, rhr=48))
    assert len(energy_for(warehouse.con, ATHLETE_ID, NOW, days=1)["series"]) == 25
    assert len(energy_for(warehouse.con, ATHLETE_ID, NOW, days=7)["series"]) == 7 * 24 + 1
