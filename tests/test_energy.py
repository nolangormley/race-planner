from datetime import datetime, time, timedelta

from src.energy import energy

NOW = datetime(2026, 10, 30, 21, 0)         # a Friday evening, after the day's run
TODAY = NOW.date()


def at(days_ago, hour, minute=0):
    return datetime.combine(TODAY - timedelta(days=days_ago), time(hour, minute))


def history(count=40, hrv=60, rhr=48):
    """Steady nights before today with the small spread of real readings."""
    return [
        {"day": TODAY - timedelta(days=offset), "hrv": hrv + offset % 3 - 1, "rhr": rhr + (1 if offset % 3 == 0 else 0), "sleep_min": None}
        for offset in range(1, count)
    ]


def last_night(hrv=60, rhr=48, sleep_min=None):
    return [{"day": TODAY, "hrv": hrv, "rhr": rhr, "sleep_min": sleep_min}]


def run(start, trimp, minutes=45, name="Easy Run"):
    return {"activity_id": int(start.timestamp()), "name": name, "type": "Run", "start": start, "seconds": minutes * 60, "trimp": trimp}


def easy_days(count=14, trimp=60):
    """An easy run every evening, today's first."""
    return [run(at(offset, 17, 30), trimp) for offset in range(count)]


def morning_after(hrv=60, rhr=48, sleep_min=None, activities=None):
    return energy(history() + last_night(hrv, rhr, sleep_min), easy_days() if activities is None else activities, NOW)["morning"]


def cost_today(trimp):
    """Energy now without and with a session this evening, the days before alike."""
    nights = history() + last_night()
    without = energy(nights, easy_days()[1:], NOW)["current"]
    with_session = energy(nights, easy_days()[1:] + [run(at(0, 17, 30), trimp)], NOW)["current"]
    return without - with_session


# ---- Calibration targets ----

def test_a_normal_or_good_night_after_an_easy_day_refills_to_80_to_95():
    assert 80 <= morning_after() <= 95                     # HRV and resting heart rate at baseline
    assert 80 <= morning_after(hrv=64, rhr=46) <= 95       # both better than baseline


def test_an_easy_run_costs_10_to_15_and_a_hard_session_30_to_40():
    assert 10 <= cost_today(60) <= 15
    assert 30 <= cost_today(150) <= 40


def test_a_bad_night_refills_noticeably_less():
    assert morning_after(hrv=48, rhr=53) <= morning_after() - 15     # HRV 20% under, resting heart rate 5 bpm up


# ---- Monotonic behaviour ----

def test_worse_hrv_means_less_recharge():
    mornings = [morning_after(hrv=hrv) for hrv in (72, 66, 62, 60, 58, 54, 50, 45, 40)]
    assert mornings == sorted(mornings, reverse=True)
    assert mornings[3] > mornings[4] > mornings[5] > mornings[6]     # outside the normal range every step counts


def test_a_higher_resting_heart_rate_means_less_recharge():
    mornings = [morning_after(rhr=rhr) for rhr in (45, 48, 50, 52, 55)]
    assert mornings == sorted(mornings, reverse=True)
    assert mornings[1] > mornings[3] > mornings[4]


def test_more_trimp_means_more_drain():
    costs = [cost_today(trimp) for trimp in (0, 30, 60, 100, 150, 200)]
    assert costs == sorted(costs) and len(set(costs)) == len(costs)


def test_the_level_stays_between_0_and_100():
    drained = energy(history() + last_night(), [run(at(0, 9), 1000, minutes=180)], NOW)
    assert drained["current"] == 0
    assert min(point["level"] for point in drained["series"]) == 0
    rested = energy(history() + last_night(hrv=80, rhr=40), [], NOW)
    assert 90 <= max(point["level"] for point in rested["series"]) <= 100


# ---- Missing inputs ----

def test_a_night_without_readings_charges_at_the_usual_rate():
    assert morning_after(hrv=None, rhr=None) == morning_after()
    night = energy(history() + last_night(None, None), [], NOW)["night"]
    assert night["summary"] == "no COROS reading, so the usual rate"


def test_resting_heart_rate_counts_without_hrv():
    assert morning_after(hrv=None, rhr=55) < morning_after(hrv=None, rhr=48)


def test_a_short_history_counts_as_normal():
    nights = [{"day": TODAY - timedelta(days=offset), "hrv": 40, "rhr": 60, "sleep_min": None} for offset in range(1, 4)]
    result = energy(nights + last_night(hrv=80, rhr=40), [], NOW)
    assert result["night"]["summary"] == "your baseline is still building"
    assert result["morning"] == energy([], [], NOW)["morning"]


def test_without_any_input_the_level_settles_at_the_rest_day_level():
    result = energy([], [], NOW)
    assert 85 <= result["morning"] <= 92
    assert result["trend"] == 0 and result["drains"] == []


def test_a_measured_night_charges_by_its_length():
    assert morning_after(sleep_min=360) < morning_after() < morning_after(sleep_min=540)


# ---- Schedule, baselines, output ----

def test_an_early_run_ends_the_night_and_stays_out_of_the_morning_level():
    result = energy(history() + last_night(), easy_days()[1:] + [run(at(0, 5, 30), 150)], NOW)
    assert result["woke_at"] == at(0, 5, 30)
    assert abs(result["morning"] - morning_after(activities=easy_days()[1:])) <= 2


def test_a_night_is_not_part_of_its_own_baseline():
    night = energy(history() + last_night(hrv=90), [], NOW)["night"]
    assert night["hrv_baseline"] == 60 and night["rhr_baseline"] == 48


def test_the_series_is_hourly_over_the_window_and_ends_now():
    result = energy(history() + last_night(), easy_days(), NOW, days=7)
    times = [point["t"] for point in result["series"]]
    assert times[0] == NOW - timedelta(days=7) and times[-1] == NOW
    assert len(times) == 7 * 24 + 1
    assert [drain["cost"] for drain in result["drains"]] == [13] * 7
    assert [night["day"] for night in result["nights"]][-1] == TODAY and len(result["nights"]) == 7


def test_reasons_name_the_night_and_the_largest_drains():
    activities = easy_days()[1:] + [run(at(0, 7, 30), 150, name="Intervals")]
    result = energy(history() + last_night(hrv=54, rhr=48), activities, NOW)
    overnight, intervals, easy = result["reasons"]
    assert overnight.startswith("+") and overnight.endswith("overnight: HRV 10% under baseline, resting heart rate in your normal range")
    assert intervals == "-33 Intervals, today"
    assert easy == "-13 Easy Run, yesterday"


def test_the_trend_compares_this_morning_with_the_usual_one():
    result = energy(history() + last_night(hrv=50, rhr=53), easy_days(), NOW)
    assert result["trend"] == result["morning"] - result["usual"]
    assert result["trend"] <= -20
