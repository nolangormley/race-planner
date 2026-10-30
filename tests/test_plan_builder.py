from collections import defaultdict
from datetime import date, timedelta

from src.plan_builder import build_plan, summarize, week_start, METERS_PER_MILE


def weekly_distance(entries):
    totals = defaultdict(float)
    for item in entries:
        if item["approximate_distance"]:
            totals[week_start(date.fromisoformat(item["date"]))] += float(item["approximate_distance"].split()[0])
    return [totals[week] for week in sorted(totals)]


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
