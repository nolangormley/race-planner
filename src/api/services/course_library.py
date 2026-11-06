"""The race-courses dataset (https://github.com/nolangormley/race-courses): large US road races, and GPX
courses for the ones whose organizers publish a readable map, rebuilt every week.

The index is read at most once a day and kept in memory for every user. A course file is fetched only
when the index lists it, so a request cannot make the server fetch another URL.
"""
import logging
import os
import time

import requests

from src.pacing import parse_gpx_course

logger = logging.getLogger("race_planner.api")

# RACE_COURSES_URL points the app at a stand-in dataset in tests.
BASE_URL = os.getenv("RACE_COURSES_URL", "https://raw.githubusercontent.com/nolangormley/race-courses/main/")
INDEX_TTL_S = 24 * 3600
TIMEOUT_S = 15
MAX_FILE_BYTES = 5 * 1024 * 1024
PUBLISHED = ("ok", "warn", "stale")

clock = time.monotonic
cache = {"entries": None, "read_at": 0.0}
# GPX text by (race, event) with the clock reading when it was fetched; the public course pages read
# the same files again and again.
files = {}


class LibraryUnavailable(Exception):
    """The dataset could not be read."""


def fetch(path):
    try:
        response = requests.get(BASE_URL + path, timeout=TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as error:
        raise LibraryUnavailable(f"The course library could not be read ({error}).") from error
    if len(response.content) > MAX_FILE_BYTES:
        raise LibraryUnavailable(f"The course library sent a file over 5 MB ({path}).")
    return response


def read_index(response):
    """The index entries: one per event of every race in the dataset, with or without a course."""
    try:
        index = response.json()
    except ValueError as error:
        raise LibraryUnavailable("The course library index is not JSON.") from error
    if not isinstance(index, list):
        raise LibraryUnavailable("The course library index is not a list of races.")
    return [entry for entry in index if isinstance(entry, dict) and entry.get("race") and entry.get("event")]


def entries():
    """Every event in the dataset. The index is read again when the copy in memory is a day old; when that
    read fails, the old copy stays in use."""
    now = clock()
    if cache["entries"] is None or now - cache["read_at"] >= INDEX_TTL_S:
        try:
            cache["entries"] = read_index(fetch("index.json"))
            cache["read_at"] = now
        except LibraryUnavailable:
            if cache["entries"] is None:
                raise
    return cache["entries"]


def has_course(entry):
    return bool(entry.get("file")) and entry.get("status") in PUBLISHED


def courses():
    """The events that have a course file."""
    return [entry for entry in entries() if has_course(entry)]


def course_entry(race, event):
    """The index entry of one published course, or None when the index does not list it."""
    return next((course for course in courses() if (course["race"], course["event"]) == (race, event)), None)


def course_file(race, event):
    """The GPX text of one published course, kept in memory for a day, or None when the index does not
    list it."""
    entry = course_entry(race, event)
    if entry is None:
        return None
    now = clock()
    text, read_at = files.get((race, event), (None, 0.0))
    if text is None or now - read_at >= INDEX_TTL_S:
        text = fetch(entry["file"]).content.decode("utf-8", errors="replace")
        files[(race, event)] = (text, now)
    return text


def attach(race, race_slug, event_slug):
    """Store the dataset's course for one event on a race. Returns the parsed course, or None when the
    dataset has no course for the event or cannot be read; the race then stays without a course."""
    try:
        text = course_file(race_slug, event_slug)
        course = parse_gpx_course(text) if text else None
    except (LibraryUnavailable, ValueError) as error:
        logger.warning("No library course for %s/%s: %s", race_slug, event_slug, error)
        return None
    if course:
        race.course_gpx = text
    return course
