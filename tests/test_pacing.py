import pytest

from src.pacing import (
    METERS_PER_MILE, course_profile, format_clock, grade_factor, parse_clock, parse_gpx_course, plan_splits,
)

HALF_MARATHON_M = 21097.5

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Rolling 4K</name><trkseg>
    <trkpt lat="40.4400" lon="-79.9500"><ele>100</ele></trkpt>
    <trkpt lat="40.4490" lon="-79.9500"><ele>120</ele></trkpt>
    <trkpt lat="40.4580" lon="-79.9500"><ele>140</ele></trkpt>
    <trkpt lat="40.4670" lon="-79.9500"><ele>130</ele></trkpt>
    <trkpt lat="40.4760" lon="-79.9500"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""


def test_clock_parsing_and_formatting():
    assert parse_clock("1:45:00") == 6300 and parse_clock("45:00") == 2700
    assert parse_clock("") is None and parse_clock("fast") is None and parse_clock("1:2:3:4") is None
    assert format_clock(6300) == "1:45:00" and format_clock(2700) == "45:00" and format_clock(59.6) == "1:00"


def test_even_splits_share_the_goal_to_the_second():
    splits = plan_splits(10000, 45 * 60, 1000, "even")
    assert len(splits) == 10
    assert all(split["split_seconds"] == 270 for split in splits)
    assert splits[-1]["cumulative_seconds"] == 2700
    assert abs(splits[0]["seconds_per_km"] - 270) < 1e-6

    half = plan_splits(HALF_MARATHON_M, 6300, METERS_PER_MILE, "even")
    assert len(half) == 14 and half[-1]["length_m"] == pytest.approx(HALF_MARATHON_M - 13 * METERS_PER_MILE)
    assert sum(split["split_seconds"] for split in half) == 6300 and half[-1]["cumulative_seconds"] == 6300
    assert half[0]["seconds_per_mile"] == pytest.approx(6300 / (HALF_MARATHON_M / METERS_PER_MILE))


def test_negative_split_runs_the_second_half_about_two_percent_faster():
    splits = plan_splits(10000, 2700, 1000, "negative")
    first_half = sum(split["split_seconds"] for split in splits[:5])
    second_half = sum(split["split_seconds"] for split in splits[5:])
    assert first_half + second_half == 2700
    assert 1.015 < first_half / second_half < 1.025
    paces = [split["seconds_per_km"] for split in splits]
    assert paces == sorted(paces, reverse=True)                 # a steady ramp, not a step


def test_grade_cost_rises_uphill_and_gives_back_less_downhill():
    assert grade_factor(0.0) == pytest.approx(1.0)
    assert 1.25 < grade_factor(0.05) < 1.35
    assert 0.85 < grade_factor(-0.05) < 0.95
    assert grade_factor(0.5) == grade_factor(0.2)               # clipped
    assert grade_factor(0.05) - 1 > 1 - grade_factor(-0.05)     # asymmetric


def test_hilly_course_slows_the_climbs_and_still_meets_the_goal():
    # Up 4% for the first half, down 4% for the second.
    profile = [{"distance_m": step * 100.0, "elevation_m": 0.0, "grade": 0.04 if step <= 50 else -0.04} for step in range(1, 101)]
    splits = plan_splits(10000, 2700, 1000, "even", profile)
    assert splits[-1]["cumulative_seconds"] == 2700
    uphill = splits[0]["seconds_per_km"]
    downhill = splits[-1]["seconds_per_km"]
    assert uphill > 270 > downhill
    assert splits[0]["grade"] == pytest.approx(0.04) and splits[-1]["grade"] == pytest.approx(-0.04)
    assert splits[0]["climb_m"] == pytest.approx(40.0)


def test_gpx_course_becomes_a_per_100m_profile():
    course = parse_gpx_course(GPX)
    assert 3950 < course["length_m"] < 4060
    assert 39 <= len(course["points"]) <= 42
    assert course["points"][0]["distance_m"] == 100.0
    assert course["points"][-1]["distance_m"] == pytest.approx(course["length_m"])
    assert course["ascent_m"] > 25 and course["descent_m"] > 15
    grades = [point["grade"] for point in course["points"]]
    assert max(grades) > 0.01 and min(grades) < -0.01
    assert all(abs(grade) <= 0.2 for grade in grades)


def test_short_course_is_stretched_with_a_warning():
    course = parse_gpx_course(GPX)
    points, warnings = course_profile(course, 5000)
    assert points[-1]["distance_m"] == pytest.approx(5000)
    assert warnings and "stretched" in warnings[0]
    points, warnings = course_profile(course, course["length_m"] * 1.01)
    assert not warnings


def test_bad_files_are_rejected():
    with pytest.raises(ValueError):
        parse_gpx_course("<gpx><trk><trkseg><trkpt lat='1' lon='1'/></trkseg></trk></gpx>")
    with pytest.raises(ValueError):
        parse_gpx_course("not xml")
    with pytest.raises(ValueError):
        plan_splits(10000, 2700, 1000, "chaotic")


def test_goal_fallback_predicts_from_the_closest_shorter_effort():
    from src.api.routers.analytics import predict_from_efforts
    efforts = [
        {"distance_m": 1609.344, "seconds": 360, "label": "1 mile"},
        {"distance_m": 10000.0, "seconds": 2700, "label": "10K"},
    ]
    seconds, source = predict_from_efforts(efforts, HALF_MARATHON_M)
    assert source["label"] == "10K"                             # the mile is under a quarter of the distance
    assert seconds == round(2700 * (HALF_MARATHON_M / 10000.0) ** 1.06)
