"""Server-rendered pages. Each checks the session and renders a template; the page's data comes from the API routers."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import logging

from src.api.auth_routes import get_current_user_from_session
from src.api.database import SessionLocal
from src.api.web import templates

logger = logging.getLogger("race_planner.api")
router = APIRouter()


@router.get("/")
def read_root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)

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
    return templates.TemplateResponse("streams.html", {"request": request, "activity_id": activity_id, "user": user})

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
