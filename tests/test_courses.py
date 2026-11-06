"""The public course pages, their pacing API, the home page and the sitemap, logged out, with the race-courses
library answered from dicts."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.routers import courses as courses_router
from src.api.services import course_library as library

COURSE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
""" + "".join(f'  <trkpt lat="{40.44 + 0.0009 * ii:.4f}" lon="-79.9500"><ele>{100 + (ii % 40)}</ele></trkpt>\n'
              for ii in range(180)) + "</trkseg></trk></gpx>"


def course(race, name, event_name, distance_m, finishers, status="ok"):
    slug = event_name.lower().replace(" ", "-")
    return {"race": race, "event": slug, "name": name, "event_name": event_name, "city": "Pittsburgh", "state": "PA",
            "month": 11, "distance_m": distance_m, "finishers": finishers, "status": status, "length_m": 16050.0,
            "ascent_m": 64.0, "detail": "joined from 5 of 11 pieces of the map" if status == "warn" else "mymaps map",
            "file": f"courses/pa/{race}/{slug}.gpx", "page": f"https://{race}.example/course",
            "source": {"kind": "manual" if race == "pittsburgh-10-miler" else "mymaps", "page": "races.yaml"}}


CATALOG = [
    course("pittsburgh-10-miler", "EQT Pittsburgh 10 Miler & 10K", "10 Miler", 16093, 3325),
    course("bloomsday", "Lilac Bloomsday Run", "12K", 12000, 30000, status="warn"),
    {**course("flying-pig", "Flying Pig Marathon", "Half Marathon", 21097.5, 8000), "status": "unsupported", "file": None},
]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    # Importing the app creates the warehouse schema at DB_PATH, so point it at a temp file.
    import src.api.deps as deps
    deps.DB_PATH = str(tmp_path_factory.mktemp("warehouse") / "warehouse.duckdb")
    from src.api.main import app
    return app


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(library, "entries", lambda: CATALOG)
    monkeypatch.setattr(library, "course_file", lambda race, event: COURSE if library.course_entry(race, event) else None)


def offline(*args):
    raise library.LibraryUnavailable("The course library could not be read (offline).")


def test_course_list_is_the_published_courses_biggest_first(stubbed):
    races = courses_router.course_list()["races"]
    assert [(race["name"], race["has_course"]) for race in races] == [
        ("Lilac Bloomsday Run", True), ("EQT Pittsburgh 10 Miler & 10K", True),
    ]


def test_a_course_gets_a_plan_for_any_goal_and_its_own_distance(stubbed):
    plan = courses_router.course_pacing("pittsburgh-10-miler", "10-miler", goal="1:30", unit="mi")
    assert (plan["goal_seconds"], plan["goal"], plan["race"]["name"]) == (5400, "1:30:00", "EQT Pittsburgh 10 Miler & 10K")
    assert len(plan["splits"]) == 10 and plan["splits"][-1]["cumulative_seconds"] == 5400
    assert plan["course"]["ascent_m"] > 0 and plan["profile"]
    twelve_k = courses_router.course_pacing("bloomsday", "12k", goal="1:00:00", strategy="negative", unit="km")
    assert len(twelve_k["splits"]) == 12 and twelve_k["race"]["name"] == "Lilac Bloomsday Run – 12K"


@pytest.mark.parametrize("arguments, status", [
    ({"race": "flying-pig", "event": "half-marathon", "goal": "2:00:00"}, 404),       # no course file
    ({"race": "nowhere", "event": "10k", "goal": "45:00"}, 404),
    ({"race": "bloomsday", "event": "12k", "goal": "0:20:00"}, 400),                   # no runner holds that
    ({"race": "bloomsday", "event": "12k", "goal": "1:00:00", "strategy": "fast"}, 400),
    ({"race": "bloomsday", "event": "12k", "goal": "1:00:00", "unit": "furlongs"}, 400),
])
def test_course_pacing_refusals(stubbed, arguments, status):
    with pytest.raises(HTTPException) as refused:
        courses_router.course_pacing(**arguments)
    assert refused.value.status_code == status
    if status == 400 and "goal" in arguments and arguments["goal"] == "0:20:00":
        assert "not a plausible 12K time" in refused.value.detail


def test_course_pacing_without_the_library_answers_503(monkeypatch):
    monkeypatch.setattr(library, "entries", offline)
    with pytest.raises(HTTPException) as refused:
        courses_router.course_pacing("bloomsday", "12k", goal="1:00:00")
    assert refused.value.status_code == 503


def test_pages_render_logged_out(app, stubbed):
    client = TestClient(app)
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 200 and "Pace your race on its real course." in home.text
    assert 'href="/courses/bloomsday/12k"' in home.text and "for 2 large US road races" in home.text
    listing = client.get("/courses")
    assert listing.status_code == 200
    assert 'href="/courses/pittsburgh-10-miler/10-miler"' in listing.text and "flying-pig" not in listing.text
    page = client.get("/courses/bloomsday/12k")
    assert page.status_code == 200 and "<h1>Lilac Bloomsday Run – 12K</h1>" in page.text
    assert "Check this course before you trust it: joined from 5 of 11 pieces of the map." in page.text
    assert "Create a free account" in page.text and "© OpenStreetMap contributors" in page.text
    assert client.get("/courses/flying-pig/half-marathon").status_code == 404


def test_pages_say_when_the_library_cannot_be_read(app, monkeypatch):
    monkeypatch.setattr(library, "entries", offline)
    client = TestClient(app)
    assert client.get("/", follow_redirects=False).status_code == 200
    assert "cannot be read right now" in client.get("/courses").text
    assert client.get("/courses/bloomsday/12k").status_code == 503


def test_sitemap_lists_every_course_page(app, stubbed, monkeypatch):
    monkeypatch.setattr(courses_router, "HOST_URL", "https://races.example")
    client = TestClient(app)
    sitemap = client.get("/sitemap.xml").text
    assert "<loc>https://races.example/courses/pittsburgh-10-miler/10-miler</loc>" in sitemap
    assert "<loc>https://races.example/courses</loc>" in sitemap and "flying-pig" not in sitemap
    assert "Sitemap: https://races.example/sitemap.xml" in client.get("/robots.txt").text
