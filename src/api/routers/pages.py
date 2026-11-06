"""Server-rendered pages. Each checks the session and renders a template; the page's data comes from the API routers."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import logging

from src.api.auth_routes import get_current_user_from_session
from src.api.database import SessionLocal, Race
from src.api.routers.courses import page_course
from src.api.services import course_library as library
from src.api.web import templates
from src.importers import IMPORT_ID_BASE

logger = logging.getLogger("race_planner.api")
router = APIRouter()

FEATURED = 6


@router.get("/")
def read_root(request: Request):
    """The dashboard for a signed-in runner; for everyone else, the home page with the biggest races of the
    course library (none when it cannot be read)."""
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=302)
    try:
        courses = library.courses()
    except library.LibraryUnavailable:
        courses = []
    featured = sorted(courses, key=lambda course: -(course.get("finishers") or 0))[:FEATURED]
    return templates.TemplateResponse("home.html", {
        "request": request, "user": None, "featured": [page_course(course) for course in featured],
        "course_count": len(courses),
    })

@router.get("/dashboard")
def dashboard(request: Request):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

@router.get("/workout_streams/{activity_id}")
def view_workout_streams(request: Request, activity_id: int):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("streams.html", {
        "request": request, "activity_id": activity_id, "user": user, "imported": activity_id >= IMPORT_ID_BASE,
    })

@router.get("/warehouse")
def view_warehouse(request: Request):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@router.get("/profile")
def profile_page(request: Request):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})

@router.get("/training-calendar")
def training_calendar_page(request: Request):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("training_calendar.html", {"request": request, "user": user})


@router.get("/races/{race_id}/pacing")
def pacing_page(request: Request, race_id: int):
    db = SessionLocal()
    user = get_current_user_from_session(request, db)
    race = db.query(Race).filter(Race.id == race_id, Race.user_id == user.id).first() if user else None
    db.close()
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not race:
        return RedirectResponse(url="/profile", status_code=302)
    return templates.TemplateResponse("pacing.html", {"request": request, "user": user, "race": race})
