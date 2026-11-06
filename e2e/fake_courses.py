"""A stand-in for the race-courses dataset that src/api/services/course_library.py reads: an index with
two half marathon courses, a marathon course and a race with no course file, and the halves' GPX files.
The app is pointed at it through RACE_COURSES_URL."""
import math

from fastapi import FastAPI, HTTPException, Response

METERS_PER_DEGREE = 6371008.8 * math.pi / 180   # src/pacing.py's earth radius
STEPS = 211                                       # 100 m each: 21.1 km


def course(race, event, name, event_name, city, distance_m, status="ok", detail="mymaps map"):
    published = status in ("ok", "warn")
    return {
        "race": race, "event": event, "name": name, "event_name": event_name, "city": city, "state": "PA",
        "month": 5, "distance_m": distance_m, "status": status, "detail": detail,
        "file": f"courses/pa/{race}/{event}.gpx" if published else None,
        "length_m": STEPS * 100.0 if published else None, "ascent_m": 40.0 if published else None,
    }


INDEX = [
    course("three-rivers-half", "half-marathon", "Three Rivers Half", "Half Marathon", "Pittsburgh", 21097.5),
    course("allegheny-half", "half-marathon", "Allegheny Half", "Half Marathon", "Allegheny", 21097.5,
           status="warn", detail="joined from 5 of 11 pieces of the map; check that it is the right course"),
    course("three-rivers-marathon", "marathon", "Three Rivers Marathon", "Marathon", "Pittsburgh", 42195.0),
    course("monongahela-half", "half-marathon", "Monongahela Half", "Half Marathon", "Monongahela", 21097.5,
           status="unsupported", detail="only pdf"),
]


def course_gpx(name):
    """A 21.1 km course north with one 40 m hill in the middle, a point every 100 m."""
    points = "".join(
        f'<trkpt lat="{40.4 + step * 100 / METERS_PER_DEGREE:.6f}" lon="-79.990000">'
        f"<ele>{200 + 40 * math.sin(math.pi * step / STEPS):.1f}</ele></trkpt>"
        for step in range(STEPS + 1)
    )
    return ('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="e2e" xmlns="http://www.topografix.com/GPX/1/1">'
            f"<trk><name>{name}</name><trkseg>{points}</trkseg></trk></gpx>")


def make_app():
    app = FastAPI()
    files = {entry["file"]: course_gpx(entry["name"]) for entry in INDEX if entry["file"]}

    @app.get("/index.json")
    def index():
        return INDEX

    @app.get("/{path:path}")
    def course_file(path: str):
        if path not in files:
            raise HTTPException(status_code=404)
        return Response(files[path], media_type="application/gpx+xml")

    return app
