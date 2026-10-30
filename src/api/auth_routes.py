from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
import html
import os
import requests
from dotenv import load_dotenv
import urllib.parse
from datetime import datetime

import bcrypt

from .database import SessionLocal, User, Race

load_dotenv()

router = APIRouter()
from .web import templates

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
# In production, this should be the actual host
HOST_URL = os.getenv("HOST_URL", "http://localhost:8000")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---- Password hashing ----
# Passwords used to be stored and compared in plaintext. hash_password() is
# used for every new/changed password from now on. verify_password() still
# recognizes old plaintext rows so existing accounts keep working, and the
# caller transparently upgrades them to a real hash on next successful login
# (see login_post below) — there's no way to recover a plaintext password
# from a hash, so existing rows can only be migrated at the point someone
# next logs in with the correct password.

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith("$2"):  # bcrypt hash (all bcrypt variants start with $2)
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        except ValueError:
            return False
    # Legacy plaintext row predating password hashing. Compare directly; the
    # caller is responsible for rehashing on a successful match.
    return stored == password


# ---- Session-based auth ----
# The session cookie is managed by Starlette's SessionMiddleware (configured
# in main.py), which signs its contents with SECRET_KEY. Unlike the old raw
# `user_id` cookie, a client can no longer forge a session by just setting an
# arbitrary integer — tampering with the cookie invalidates the signature and
# Starlette drops the session entirely.

def get_current_user_from_session(request: Request, db: Session):
    """Returns the logged-in user for this request's session, or None.
    Use this in page routes that should redirect to /login on failure."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency for JSON API routes: returns the logged-in user or
    raises 401. Prefer get_current_user_from_session directly in page routes
    that should redirect to /login instead of returning a 401 JSON body."""
    user = get_current_user_from_session(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == username).first()
    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password."})

    if not user.password.startswith("$2"):
        # Transparently upgrade a legacy plaintext password now that we know
        # the correct value.
        user.password = hash_password(password)
        db.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")


@router.get("/register", response_class=HTMLResponse)
def register_get(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.post("/register")
def register_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    date_of_birth: str = Form(...),
    gender: str = Form(...),
    height: float = Form(...),
    weight: float = Form(...),
    db: Session = Depends(get_db)
):
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already registered."})

    dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
    new_user = User(
        name=name, email=email, password=hash_password(password),
        date_of_birth=dob, gender=gender, height=height, weight=weight
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    request.session.clear()
    request.session["user_id"] = new_user.id
    return RedirectResponse(url="/setup-race", status_code=status.HTTP_302_FOUND)


@router.get("/setup-race", response_class=HTMLResponse)
def setup_race_get(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("setup.html", {"request": request, "user": user})


@router.post("/setup-race")
def setup_race_post(
    request: Request,
    race_length: str = Form(...),
    race_date: str = Form(...),
    race_goal_time: str = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/login")

    try:
        r_date = datetime.strptime(race_date, "%Y-%m-%d").date()
    except ValueError:
        return templates.TemplateResponse("setup.html", {"request": request, "error": "Invalid date format."})

    user.race_length = race_length
    user.race_date = r_date
    user.race_goal_time = race_goal_time
    # The dashboard and training plan read races from the races table, so
    # the first-run race goes there too.
    db.add(Race(user_id=user.id, race_length=race_length, race_date=r_date, goal_time=race_goal_time or None))
    db.commit()

    return RedirectResponse(url="/strava-auth", status_code=status.HTTP_302_FOUND)


@router.get("/strava-auth", response_class=HTMLResponse)
def strava_auth(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/login")

    redirect_uri = f"{HOST_URL}/strava-callback"
    params = {
        "client_id": STRAVA_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "approval_prompt": "force",
        "scope": "read,activity:read_all,profile:read_all"
    }
    strava_auth_url = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode(params)

    return templates.TemplateResponse("strava_auth.html", {"request": request, "user": user, "strava_auth_url": strava_auth_url})


@router.get("/strava-callback")
def strava_callback(request: Request, code: str = None, error: str = None, db: Session = Depends(get_db)):
    user = get_current_user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/login")

    if error:
        # error is an attacker-controllable query param — escape before
        # interpolating into raw HTML (was previously a reflected-XSS sink).
        return HTMLResponse(f"Strava authentication returned an error: {html.escape(error)}. <a href='/strava-auth'>Try again</a>")

    if not code:
        return HTMLResponse("No code provided. <a href='/strava-auth'>Try again</a>")

    # Exchange code for access token
    token_url = "https://www.strava.com/oauth/token"
    payload = {
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code"
    }

    try:
        resp = requests.post(token_url, data=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        user.strava_access_token = data.get("access_token")
        user.strava_refresh_token = data.get("refresh_token")
        if "athlete" in data:
            user.strava_athlete_id = data["athlete"].get("id")

        db.commit()
    except Exception as e:
        return HTMLResponse(f"Failed to authenticate with Strava: {html.escape(str(e))}. <a href='/strava-auth'>Try again</a>")

    # Strava complete! Redirect to dashboard.
    # In a real app we would kick off a background task here to ingest their data.
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
