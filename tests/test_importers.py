from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.importers import (
    IMPORT_ID_BASE, UnreadableFile, default_name, fit_activity_type, import_activity_id, parse_file, parse_fit_messages,
)

FIT_FIXTURE = Path(__file__).parent / "fixtures" / "sample_run.fit"
START = datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc)


def test_activity_id_is_stable_and_above_every_strava_id():
    activity_id = import_activity_id(b"one file")
    assert activity_id == import_activity_id(b"one file")
    assert activity_id != import_activity_id(b"another file")
    assert IMPORT_ID_BASE <= activity_id < 2 * IMPORT_ID_BASE


def test_fit_fixture_becomes_a_run_with_streams_and_laps():
    parsed = parse_file("sample_run.fit", FIT_FIXTURE.read_bytes())
    summary = parsed["summary"]
    assert summary["id"] == import_activity_id(FIT_FIXTURE.read_bytes())
    assert (summary["type"], summary["name"], summary["device_name"]) == ("Run", "Morning Run", "Imported FIT")
    assert summary["start_date"] == "2026-10-25T11:00:00Z"
    assert summary["start_date_local"] == "2026-10-25T07:00:00Z"      # the watch's UTC-4 offset
    assert summary["distance"] == pytest.approx(1950.5)
    assert (summary["moving_time"], summary["average_heartrate"], summary["max_heartrate"]) == (600, 150, 160)

    streams = parsed["streams"]
    assert streams["time"]["data"][:3] == [0, 1, 2] and len(streams["time"]["data"]) == 601
    assert {"heartrate", "velocity_smooth", "altitude", "cadence", "moving"} <= streams.keys()
    assert streams["velocity_smooth"]["data"][100] == pytest.approx(3.0)
    assert streams["velocity_smooth"]["data"][500] == pytest.approx(3.5)

    laps = parsed["laps"]
    assert [(lap["start_index"], lap["end_index"]) for lap in laps] == [(0, 300), (300, 600)]
    assert [lap["distance"] for lap in laps] == pytest.approx([900.5, 1050.0])


def test_fit_records_without_a_session_still_give_a_summary():
    messages = [("record", {"timestamp": START + timedelta(seconds=second), "distance": 3.0 * second, "heart_rate": 150}) for second in range(120)]
    parsed = parse_fit_messages(messages, "bare.fit")
    summary = parsed["summary"]
    assert summary["distance"] == pytest.approx(357.0)
    assert summary["moving_time"] == 119 and summary["type"] == "Run"
    assert parsed["streams"]["velocity_smooth"]["data"][60] == pytest.approx(3.0)
    assert parsed["laps"] == []


def test_fit_sports_map_to_strava_types_and_names():
    assert fit_activity_type("running", "trail") == "TrailRun"
    assert fit_activity_type("cycling", "indoor_cycling") == "VirtualRide"
    assert fit_activity_type("walking", "generic") == "Walk"
    assert fit_activity_type(None, None) == "Run"
    assert default_name("TrailRun", datetime(2026, 10, 25, 18, 30)) == "Evening Trail Run"
    assert default_name("Ride", datetime(2026, 10, 25, 6, 0)) == "Morning Ride"


def test_gpx_track_becomes_a_run(make_gpx):
    parsed = parse_file("track.gpx", make_gpx(START, points=101, spacing_s=3, speed_mps=3.2, name="Riverfront loop"))
    summary = parsed["summary"]
    assert (summary["name"], summary["type"], summary["device_name"]) == ("Riverfront loop", "Run", "Imported GPX")
    assert summary["start_date"] == "2026-10-25T11:00:00Z"
    assert summary["distance"] == pytest.approx(100 * 3 * 3.2, rel=1e-3)
    assert summary["elapsed_time"] == 300 and summary["average_heartrate"] == pytest.approx(152, abs=0.1)
    assert parsed["streams"]["time"]["data"][:3] == [0, 3, 6]
    assert parsed["streams"]["velocity_smooth"]["data"][50] == pytest.approx(3.2, rel=1e-3)
    assert parsed["streams"]["heartrate"]["data"][:2] == [150, 151]
    assert parsed["laps"] == []


def test_gpx_offsets_are_kept_and_bad_files_are_refused():
    gpx = (b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
           b'<trkpt lat="40.0" lon="-80.0"><time>2026-10-24T08:00:00-04:00</time></trkpt>'
           b'<trkpt lat="40.001" lon="-80.0"><time>2026-10-24T08:00:30-04:00</time></trkpt>'
           b'</trkseg></trk></gpx>')
    summary = parse_file("offset.gpx", gpx)["summary"]
    assert summary["start_date"] == "2026-10-24T12:00:00Z"
    assert summary["start_date_local"] == "2026-10-24T08:00:00Z"
    assert summary["name"] == "Morning Run"

    for filename, data in (
        ("empty.gpx", b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg></trkseg></trk></gpx>'),
        ("broken.gpx", b"<gpx><trk>"),
        ("not-a.fit", b"definitely not a FIT file"),
        ("notes.txt", b"hello"),
    ):
        with pytest.raises(UnreadableFile):
            parse_file(filename, data)
