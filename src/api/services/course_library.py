"""The race-courses dataset (https://github.com/nolangormley/race-courses): GPX courses of large US road
races, read from the organizers' maps and rebuilt every week.

The index is read at most once a day and kept in memory for every user. A course file is fetched only
when the index lists it, so a request cannot make the server fetch another URL.
"""
import os
import time

import requests

# RACE_COURSES_URL points the app at a stand-in dataset in tests.
BASE_URL = os.getenv("RACE_COURSES_URL", "https://raw.githubusercontent.com/nolangormley/race-courses/main/")
INDEX_TTL_S = 24 * 3600
TIMEOUT_S = 15
MAX_FILE_BYTES = 5 * 1024 * 1024
PUBLISHED = ("ok", "warn", "stale")

clock = time.monotonic
cache = {"courses": None, "read_at": 0.0}


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


def published(response):
    """The index entries that have a course file."""
    try:
        index = response.json()
    except ValueError as error:
        raise LibraryUnavailable("The course library index is not JSON.") from error
    if not isinstance(index, list):
        raise LibraryUnavailable("The course library index is not a list of courses.")
    return [entry for entry in index if isinstance(entry, dict) and entry.get("file") and entry.get("status") in PUBLISHED]


def courses():
    """The published courses. The index is read again when the copy in memory is a day old; when that
    read fails, the old copy stays in use."""
    now = clock()
    if cache["courses"] is None or now - cache["read_at"] >= INDEX_TTL_S:
        try:
            cache["courses"] = published(fetch("index.json"))
            cache["read_at"] = now
        except LibraryUnavailable:
            if cache["courses"] is None:
                raise
    return cache["courses"]


def course_file(race, event):
    """The GPX text of one published course, or None when the index does not list it."""
    entry = next((course for course in courses() if (course["race"], course["event"]) == (race, event)), None)
    if entry is None:
        return None
    response = fetch(entry["file"])
    return response.content.decode("utf-8", errors="replace")
