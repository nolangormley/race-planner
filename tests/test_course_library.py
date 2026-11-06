"""The race-courses dataset reader, with requests.get answered from a dict of files."""
import json
from types import SimpleNamespace

import pytest
import requests

from src.api.services import course_library as library

HALF = {
    "race": "pittsburgh-marathon", "event": "half-marathon", "name": "Pittsburgh Marathon",
    "event_name": "Half Marathon", "city": "Pittsburgh", "state": "PA", "distance_m": 21097.5,
    "status": "warn", "file": "courses/pa/pittsburgh-marathon/half-marathon.gpx",
}
KEPT = {**HALF, "race": "akron-marathon", "status": "stale", "file": "courses/oh/akron-marathon/half-marathon.gpx"}
PDF_ONLY = {**HALF, "race": "flying-pig-marathon", "status": "unsupported", "file": None}
REJECTED = {**HALF, "race": "cowtown-marathon", "status": "reject"}
COURSE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
  <trkpt lat="40.4400" lon="-79.9500"><ele>100</ele></trkpt>
  <trkpt lat="40.4490" lon="-79.9500"><ele>120</ele></trkpt>
  <trkpt lat="40.4580" lon="-79.9500"><ele>140</ele></trkpt>
  <trkpt lat="40.4670" lon="-79.9500"><ele>130</ele></trkpt>
</trkseg></trk></gpx>"""


class Reply:
    def __init__(self, status_code, content):
        self.status_code, self.content = status_code, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self):
        return json.loads(self.content)


@pytest.fixture
def dataset(monkeypatch):
    """files maps a path in the dataset to its bytes, or to an exception that the read raises.
    reads lists the paths read; now is the clock."""
    state = SimpleNamespace(files={}, reads=[], now=1000.0)

    def get(url, timeout):
        path = url.removeprefix(library.BASE_URL)
        state.reads.append(path)
        content = state.files.get(path)
        if isinstance(content, Exception):
            raise content
        return Reply(404, b"404: Not Found") if content is None else Reply(200, content)

    monkeypatch.setattr(library.requests, "get", get)
    monkeypatch.setattr(library, "clock", lambda: state.now)
    monkeypatch.setattr(library, "cache", {"entries": None, "read_at": 0.0})
    return state


def test_the_index_is_read_once_a_day_and_courses_are_the_events_with_a_file(dataset):
    dataset.files["index.json"] = json.dumps([HALF, KEPT, PDF_ONLY, REJECTED, ["not an event"]]).encode()
    assert [entry["race"] for entry in library.entries()] == [
        "pittsburgh-marathon", "akron-marathon", "flying-pig-marathon", "cowtown-marathon",
    ]
    assert [course["race"] for course in library.courses()] == ["pittsburgh-marathon", "akron-marathon"]
    dataset.now += library.INDEX_TTL_S - 1
    library.courses()
    assert dataset.reads == ["index.json"]
    dataset.now += 1
    library.courses()
    assert dataset.reads == ["index.json", "index.json"]


def test_a_failed_read_keeps_the_copy_in_memory(dataset):
    dataset.files["index.json"] = requests.ConnectionError("offline")
    with pytest.raises(library.LibraryUnavailable, match="could not be read"):
        library.courses()
    dataset.files["index.json"] = json.dumps([HALF]).encode()
    assert len(library.courses()) == 1
    dataset.now += library.INDEX_TTL_S
    dataset.files["index.json"] = None                          # a 404
    assert len(library.courses()) == 1
    assert dataset.reads == ["index.json"] * 3


@pytest.mark.parametrize("content, message", [(b"<html>rate limited</html>", "not JSON"), (b'{"courses": []}', "not a list")])
def test_an_index_that_is_not_a_list_of_courses_is_refused(dataset, content, message):
    dataset.files["index.json"] = content
    with pytest.raises(library.LibraryUnavailable, match=message):
        library.courses()


def test_only_files_that_the_index_lists_are_fetched(dataset):
    dataset.files["index.json"] = json.dumps([HALF, PDF_ONLY]).encode()
    dataset.files[HALF["file"]] = b"<gpx>Pittsburgh \xe2\x80\x93 half</gpx>"
    assert library.course_file("pittsburgh-marathon", "half-marathon") == "<gpx>Pittsburgh – half</gpx>"
    assert library.course_file("flying-pig-marathon", "half-marathon") is None     # listed, but no course file
    assert library.course_file("https://example.com/", "anything") is None
    assert dataset.reads == ["index.json", HALF["file"]]


def test_a_file_over_5_mb_is_refused(dataset):
    dataset.files["index.json"] = json.dumps([HALF]).encode()
    dataset.files[HALF["file"]] = b"x" * (library.MAX_FILE_BYTES + 1)
    with pytest.raises(library.LibraryUnavailable, match="over 5 MB"):
        library.course_file("pittsburgh-marathon", "half-marathon")


def test_attach_stores_the_course_or_leaves_the_race_without_one(dataset):
    dataset.files["index.json"] = json.dumps([HALF, PDF_ONLY]).encode()
    dataset.files[HALF["file"]] = COURSE.encode()
    race = SimpleNamespace(course_gpx=None)
    course = library.attach(race, "pittsburgh-marathon", "half-marathon")
    assert race.course_gpx == COURSE and 2900 < course["length_m"] < 3100
    for files, event in (({}, ("flying-pig-marathon", "half-marathon")),            # no course file
                         ({HALF["file"]: b"<html>moved</html>"}, ("pittsburgh-marathon", "half-marathon")),
                         ({HALF["file"]: requests.ConnectionError("offline")}, ("pittsburgh-marathon", "half-marathon"))):
        dataset.files.update(files)
        race = SimpleNamespace(course_gpx=None)
        assert library.attach(race, *event) is None and race.course_gpx is None
