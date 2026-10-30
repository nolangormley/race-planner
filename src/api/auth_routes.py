from fastapi import APIRouter, BackgroundTasks, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
import hashlib
import hmac
import html
import logging
import os
import requests
from dotenv import load_dotenv
import urllib.parse
from datetime import datetime

import bcrypt
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .database import SessionLocal, User, Race
from .schemas import PasswordChange
from .services.email import send_email

load_dotenv()

logger = logging.getLogger("race_planner.api")
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
def login_get(request: Request, reset: bool = False):
    notice = "Your password has been reset. Sign in with the new one." if reset else None
    return templates.TemplateResponse("login.html", {"request": request, "notice": notice})


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


# ---- Password reset ----
# A reset link carries a signed, timestamped token naming the user and an
# HMAC of their current password hash. Setting a new password changes that
# HMAC, so a link stops working once it has been used (or the password was
# changed some other way). Tokens are signed, not encrypted, which is why the
# password goes in as an HMAC under SECRET_KEY rather than a plain hash:
# legacy accounts may still have a plaintext password stored.

MIN_PASSWORD_LENGTH = 8
RESET_TOKEN_MAX_AGE = 60 * 60  # 1 hour


def new_password_error(password: str):
    """Why a new password can't be used, or None if it can."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    # bcrypt only reads the first 72 bytes and bcrypt>=5 refuses longer input.
    if len(password.encode("utf-8")) > 72:
        return "Use at most 72 characters."
    return None


def _reset_serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="password-reset")


def _password_fingerprint(secret_key: str, stored_password: str) -> str:
    return hmac.new(secret_key.encode("utf-8"), (stored_password or "").encode("utf-8"), hashlib.sha256).hexdigest()


def make_reset_token(user: User, secret_key: str) -> str:
    return _reset_serializer(secret_key).dumps({"uid": user.id, "pw": _password_fingerprint(secret_key, user.password)})


def user_for_reset_token(token: str, secret_key: str, db: Session):
    """Returns the user a reset token belongs to, or None if the token is
    forged, expired, or already used."""
    try:
        data = _reset_serializer(secret_key).loads(token, max_age=RESET_TOKEN_MAX_AGE)
    except BadSignature:  # also covers SignatureExpired
        return None
    user = db.query(User).filter(User.id == data.get("uid")).first()
    if not user or not hmac.compare_digest(data.get("pw", ""), _password_fingerprint(secret_key, user.password)):
        return None
    return user


def send_reset_email(to: str, link: str) -> None:
    try:
        send_email(
            to,
            "Reset your Race Planner password",
            "Someone asked to reset the password for your Race Planner account.\n\n"
            f"Open this link within an hour to choose a new password:\n{link}\n\n"
            "If it wasn't you, ignore this email; your password stays the same.",
        )
    except Exception as e:
        print(f"Failed to send password reset email to {to}: {e}")


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_get(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request})


@router.post("/forgot-password")
def forgot_password_post(request: Request, background_tasks: BackgroundTasks, email: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email.strip()).first()
    if user:
        token = make_reset_token(user, request.app.state.secret_key)
        link = f"{HOST_URL}/reset-password?" + urllib.parse.urlencode({"token": token})
        # Sent after the response so the reply takes the same time whether or
        # not the email has an account.
        background_tasks.add_task(send_reset_email, user.email, link)
    # Same answer either way, so this form can't be used to find out who has
    # an account.
    return templates.TemplateResponse("forgot_password.html", {"request": request, "sent": True})


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_get(request: Request, token: str = "", db: Session = Depends(get_db)):
    user = user_for_reset_token(token, request.app.state.secret_key, db)
    if not user:
        return templates.TemplateResponse("reset_password.html", {"request": request, "invalid": True})
    return templates.TemplateResponse("reset_password.html", {"request": request, "token": token})


@router.post("/reset-password")
def reset_password_post(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = user_for_reset_token(token, request.app.state.secret_key, db)
    if not user:
        return templates.TemplateResponse("reset_password.html", {"request": request, "invalid": True})
    error = new_password_error(password)
    if not error and password != confirm_password:
        error = "The passwords don't match."
    if error:
        return templates.TemplateResponse("reset_password.html", {"request": request, "token": token, "error": error})

    user.password = hash_password(password)
    db.commit()
    request.session.clear()
    return RedirectResponse(url="/login?reset=1", status_code=status.HTTP_302_FOUND)


@router.post("/api/password")
def change_password(change: PasswordChange, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not verify_password(change.current_password, user.password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    error = new_password_error(change.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    user.password = hash_password(change.new_password)
    db.commit()
    return {"message": "Password changed"}


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
        athlete = data.get("athlete") or {}
        if athlete.get("id"):
            if user.strava_athlete_id != athlete["id"]:
                # Activities imported from files before connecting live under
                # a synthetic athlete key; they follow the user to the real id.
                # Imported here: deps reads DB_PATH on import, which must
                # happen after main.py has loaded the .env files.
                from .deps import DB_PATH
                from .services.imports import move_to_strava_athlete
                try:
                    move_to_strava_athlete(user.id, athlete, DB_PATH)
                except Exception:
                    logger.exception("Could not move imported activities to Strava athlete %s", athlete["id"])
            user.strava_athlete_id = athlete["id"]

        db.commit()
    except Exception as e:
        return HTMLResponse(f"Failed to authenticate with Strava: {html.escape(str(e))}. <a href='/strava-auth'>Try again</a>")

    # Strava complete! Redirect to dashboard.
    # In a real app we would kick off a background task here to ingest their data.
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
