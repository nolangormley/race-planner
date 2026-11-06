"""Public course pages: every course of the race-courses dataset with its elevation profile and a pacing plan
for any goal, without an account, and the sitemap that lists them for search engines."""
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from src.pacing import STRATEGIES, parse_gpx_course
from src.api.auth_routes import HOST_URL, get_current_user_from_session
from src.api.database import SessionLocal
from src.api.routers.pacing import UNITS_M, checked_goal, plan_payload
from src.api.services import course_library as library
from src.api.web import templates

router = APIRouter()

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
LIST_FIELDS = ("race", "event", "name", "event_name", "city", "state", "month", "distance_m", "length_m", "ascent_m",
               "status")
SOURCES = {"manual": "Traced from the organizer's map onto OpenStreetMap streets", "osm": "An OpenStreetMap route of the course"}


def title(course):
    """The race name, with the event when the race's name does not already say it."""
    if course["event_name"].lower() in course["name"].lower():
        return course["name"]
    return f"{course['name']} – {course['event_name']}"


def session_user(request):
    db = SessionLocal()
    try:
        return get_current_user_from_session(request, db)
    finally:
        db.close()


def page_course(course):
    """What the course page and the course list show of one course."""
    source = course.get("source") or {}
    return {
        **course, "title": title(course), "month_name": MONTHS[course["month"] - 1] if course.get("month") else "",
        "source_name": SOURCES.get(source.get("kind"), "The organizer's course map"),
    }


@router.get("/api/courses")
def course_list():
    """The dataset's courses, biggest race first, in the shape of the race catalog."""
    try:
        courses = sorted(library.courses(), key=lambda course: -(course.get("finishers") or 0))
    except library.LibraryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    return {"races": [{**{field: course.get(field) for field in LIST_FIELDS}, "has_course": True} for course in courses]}


@router.get("/api/courses/{race}/{event}/pacing")
def course_pacing(race: str, event: str, goal: str, strategy: str = "even", unit: str = "mi"):
    """The split plan for a goal over one course of the dataset."""
    if strategy not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"strategy must be one of {', '.join(STRATEGIES)}")
    if unit not in UNITS_M:
        raise HTTPException(status_code=400, detail="unit must be mi or km")
    try:
        course = library.course_entry(race, event)
        text = library.course_file(race, event) if course else None
    except library.LibraryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    if text is None:
        raise HTTPException(status_code=404, detail="Course not found")
    try:
        gpx = parse_gpx_course(text)
    except ValueError as error:
        raise HTTPException(status_code=502, detail=f"The course file could not be read ({error}).")
    distance = course["distance_m"]
    goal_seconds = checked_goal(goal, course["event_name"], distance, unit)
    return {**plan_payload(distance, goal_seconds, strategy, unit, gpx),
            "race": {"name": title(course), "race_length": course["event_name"]}}


@router.get("/courses")
def courses_page(request: Request):
    try:
        courses = sorted(library.courses(), key=lambda course: (course["state"], course["name"], course["event_name"]))
    except library.LibraryUnavailable:
        courses = None
    return templates.TemplateResponse("courses.html", {
        "request": request, "user": session_user(request),
        "courses": [page_course(course) for course in courses] if courses is not None else None,
    })


@router.get("/courses/{race}/{event}")
def course_page(request: Request, race: str, event: str):
    try:
        course = library.course_entry(race, event)
    except library.LibraryUnavailable:
        return templates.TemplateResponse("course_missing.html", {
            "request": request, "user": session_user(request), "reason": "unavailable",
        }, status_code=503)
    if course is None:
        return templates.TemplateResponse("course_missing.html", {
            "request": request, "user": session_user(request), "reason": "missing",
        }, status_code=404)
    return templates.TemplateResponse("course.html", {
        "request": request, "user": session_user(request), "course": page_course(course),
    })


@router.get("/sitemap.xml")
def sitemap():
    try:
        courses = library.courses()
    except library.LibraryUnavailable:
        courses = []
    paths = ["/", "/courses"] + [f"/courses/{course['race']}/{course['event']}" for course in courses]
    urls = "".join(f"<url><loc>{escape(HOST_URL + path)}</loc></url>" for path in paths)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n',
                    media_type="application/xml")


@router.get("/robots.txt")
def robots():
    return PlainTextResponse(f"User-agent: *\nDisallow: /api/\nSitemap: {HOST_URL}/sitemap.xml\n")
