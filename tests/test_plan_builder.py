import re
from collections import defaultdict
from datetime import date, timedelta

import pytest

from src.plan_builder import (
    build_plan, interval_session, summarize, tempo_session, week_start, weekly_targets, METERS_PER_MILE, RACE_PROFILES,
)

PART = r"(\d+(?:\.\d+)?) (?:miles?|km)"


def weekly_distance(entries):
    totals = defaultdict(float)
    for item in entries:
        if item["approximate_distance"]:
            totals[week_start(date.fromisoformat(item["date"]))] += float(item["approximate_distance"].split()[0])
    return [totals[week] for week in sorted(totals)]


def session_parts(description, metric):
    """The warm-up, the work (the reps with the jogs between them, or the
    tempo block) and the cool-down a quality session describes, in the
    plan's unit."""
    unit_m = 1000.0 if metric else METERS_PER_MILE
    warm_up = float(re.search(rf"Warm up {PART} easy", description).group(1))
    cool_down = float(re.search(rf"cool down {PART} easy", description).group(1))
    reps = re.search(r"(\d+) x 800 m at 5K effort with 400 m jog", description)
    if reps:
        work = (int(reps.group(1)) * 800 + (int(reps.group(1)) - 1) * 400) / unit_m
    else:
        work = float(re.search(rf"then {PART} at threshold", description).group(1))
    return warm_up, work, cool_down


def test_half_marathon_plan_covers_every_day_and_ends_on_race_day():
    start = date(2026, 11, 2)                 # a Monday
    race_day = start + timedelta(weeks=10, days=6)   # a Sunday, 11 weeks out
    entries = build_plan(start, race_day, "half marathon", current_weekly_m=25 * METERS_PER_MILE, race_label="Pittsburgh Half")

    assert [item["date"] for item in entries] == [(start + timedelta(days=offset)).isoformat() for offset in range(77)]
    assert entries[-1]["workout_type"] == "Race" and entries[-1]["approximate_distance"] == "13 miles"
    assert entries[-2]["workout_type"] == "Rest"                       # day before the race
    long_runs = [item for item in entries if item["workout_type"] == "Long Run"]
    assert all(date.fromisoformat(item["date"]).weekday() == 6 for item in long_runs)   # Sundays
    assert len(long_runs) == 10                                        # every week but race week


def test_volume_ramps_slowly_cuts_back_and_tapers():
    start = date(2026, 11, 2)
    race_day = start + timedelta(weeks=10, days=6)
    weekly = weekly_distance(build_plan(start, race_day, "half marathon", current_weekly_m=25 * METERS_PER_MILE))

    assert 24 <= weekly[0] <= 27                                        # starts at the current volume
    for before, after in zip(weekly, weekly[1:3]):
        assert after <= before * 1.12 + 0.5                            # no more than ~10% a week
    assert weekly[3] < weekly[2]                                        # cutback in week 4
    assert max(weekly) <= 25 * 1.45 + 1                                 # capped at the race's peak multiplier
    assert weekly[-1] < weekly[-2] < weekly[-3]                         # two-week taper into the race


def test_short_plan_still_has_quality_and_a_taper():
    start = date(2026, 11, 3)
    race_day = date(2026, 11, 26)             # 3.5 weeks to a 5K
    entries = build_plan(start, race_day, "5k", current_weekly_m=20 * METERS_PER_MILE, days_per_week=4, quality_sessions=1)
    kinds = {item["workout_type"] for item in entries}
    assert "Intervals" in kinds and "Race" in kinds
    assert "Tempo" not in kinds                                        # one quality session a week
    summary = summarize(entries)
    assert summary["weeks"] == 4
    assert summary["sessions"]["Race"] == 1


def test_metric_distances_and_long_run_day():
    start = date(2026, 11, 2)
    entries = build_plan(start, start + timedelta(weeks=6, days=5), "10k", current_weekly_m=30000, metric=True, long_run_day=5)
    long_runs = [item for item in entries if item["workout_type"] == "Long Run"]
    assert long_runs and all(date.fromisoformat(item["date"]).weekday() == 5 for item in long_runs)   # Saturdays
    assert all(item["approximate_distance"].endswith("km") for item in entries if item["approximate_distance"])


def test_short_marathon_plan_tapers_from_the_biggest_week_it_reached():
    # Six weeks out: three build weeks never reach the marathon peak, so the
    # taper must step down from the third week, not up toward the peak.
    targets = weekly_targets(40000, 6, RACE_PROFILES["marathon"], taper_weeks=3)
    build, taper = targets[:3], targets[3:]
    assert taper[0] < max(build)
    assert taper[0] > taper[1] > taper[2]


@pytest.mark.parametrize("metric", [False, True])
def test_quality_sessions_add_up_to_their_planned_distance(metric):
    start = date(2026, 11, 2)
    for race in RACE_PROFILES:
        for weekly_miles in (6, 15, 25, 40, 70):
            entries = build_plan(start, start + timedelta(weeks=12, days=6), race, weekly_miles * METERS_PER_MILE, metric=metric)
            for item in entries:
                if item["workout_type"] not in ("Tempo", "Intervals"):
                    continue
                warm_up, work, cool_down = session_parts(item["description"], metric)
                planned = float(item["approximate_distance"].split()[0])
                assert round((warm_up + work + cool_down) * 2) / 2 == planned, item
                assert warm_up >= (1.5 if metric else 1.0) and (1.0 if metric else 0.5) <= cool_down <= warm_up, item


def test_interval_reps_follow_the_planned_distance():
    reps = [re.search(r"(\d+) x 800 m", interval_session(miles, metric=False)[1]).group(1) for miles in (3.5, 4.5, 5, 6, 8, 10.5, 15)]
    assert reps == ["3", "3", "4", "5", "6", "8", "8"]


def test_tempo_block_is_45_percent_of_the_day():
    assert tempo_session(4.5, metric=False) == (4.5, "Warm up 1.5 miles easy, then 2 miles at threshold (comfortably hard, about one-hour race effort), cool down 1 mile easy.")
    assert tempo_session(8, metric=True) == (8, "Warm up 2.5 km easy, then 3.5 km at threshold (comfortably hard, about one-hour race effort), cool down 2 km easy.")


def test_a_quality_day_too_short_for_the_session_grows_to_fit_it():
    # A 20-mile week's taper leaves 1.5 miles for each quality day.
    assert interval_session(1.5, metric=False) == (3.5, "Warm up 1 mile easy, then 3 x 800 m at 5K effort with 400 m jog recoveries, cool down 0.5 miles easy.")
    assert tempo_session(1.5, metric=False) == (3, "Warm up 1 mile easy, then 1.5 miles at threshold (comfortably hard, about one-hour race effort), cool down 0.5 miles easy.")
    assert interval_session(2.5, metric=True)[0] == 6
    assert tempo_session(2.5, metric=True)[0] == 5
