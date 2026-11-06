"""A stand-in for the race-courses dataset that src/api/services/course_library.py reads: an index with
two half marathon courses, a marathon course, a race with no course file and a 10 miler with a 10K whose
names are both inside the race's name, and the courses' GPX files. The app is pointed at it through
RACE_COURSES_URL."""
import math

from fastapi import FastAPI, HTTPException, Response

METERS_PER_DEGREE = 6371008.8 * math.pi / 180   # src/pacing.py's earth radius


def course(race, event, name, event_name, city, distance_m, status="ok", detail="mymaps map"):
    published = status in ("ok", "warn")
    return {
        "race": race, "event": event, "name": name, "event_name": event_name, "city": city, "state": "PA",
        "month": 5, "distance_m": distance_m, "status": status, "detail": detail,
        "file": f"courses/pa/{race}/{event}.gpx" if published else None,
        "length_m": round(distance_m, -2) if published else None, "ascent_m": 40.0 if published else None,
    }


INDEX = [
    course("three-rivers-half", "half-marathon", "Three Rivers Half", "Half Marathon", "Pittsburgh", 21097.5),
    course("allegheny-half", "half-marathon", "Allegheny Half", "Half Marathon", "Allegheny", 21097.5,
           status="warn", detail="joined from 5 of 11 pieces of the map; check that it is the right course"),
    course("three-rivers-marathon", "marathon", "Three Rivers Marathon", "Marathon", "Pittsburgh", 42195.0),
    course("monongahela-half", "half-marathon", "Monongahela Half", "Half Marathon", "Monongahela", 21097.5,
           status="unsupported", detail="only pdf"),
    course("steel-city-10-miler", "10-miler", "Steel City 10 Miler & 10K", "10 Miler", "Pittsburgh", 16093.44),
    course("steel-city-10-miler", "10k", "Steel City 10 Miler & 10K", "10K", "Pittsburgh", 10000.0),
]


def course_gpx(name, length_m):
    """A course north of length_m with one 40 m hill in the middle, a point every 100 m."""
    steps = round(length_m / 100)
    points = "".join(
        f'<trkpt lat="{40.4 + step * 100 / METERS_PER_DEGREE:.6f}" lon="-79.990000">'
        f"<ele>{200 + 40 * math.sin(math.pi * step / steps):.1f}</ele></trkpt>"
        for step in range(steps + 1)
    )
    return ('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="e2e" xmlns="http://www.topografix.com/GPX/1/1">'
            f"<trk><name>{name}</name><trkseg>{points}</trkseg></trk></gpx>")


def make_app():
    app = FastAPI()
    files = {entry["file"]: course_gpx(entry["name"], entry["length_m"]) for entry in INDEX if entry["file"]}

    @app.get("/index.json")
    def index():
        return INDEX

    @app.get("/{path:path}")
    def course_file(path: str):
        if path not in files:
            raise HTTPException(status_code=404)
        return Response(files[path], media_type="application/gpx+xml")

    return app
