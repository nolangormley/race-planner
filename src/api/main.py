"""App setup: environment, middleware, static files and router mounting.
The endpoints live in routers/ (see routers/AGENTS.md)."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
import os
import secrets
import sys
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)

# Makes `src.` imports work however the app is started (uvicorn --app-dir,
# plain `python -m`, or the tests).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from src.api.auth_routes import router as auth_router  # noqa: E402
from src.api.web import BASE_DIR, templates  # noqa: E402

load_dotenv()
# Also try loading .env.dev, overriding variables from .env if both exist
if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env.dev')):
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env.dev'), override=True)
elif os.path.exists('.env.dev'):
    load_dotenv('.env.dev', override=True)

# COROS goes through a private, unofficial API (see src/coros.py), so an
# install can switch it off: COROS_ENABLED=false removes the profile card,
# the dashboard recovery card, the routes and the nightly pull.
COROS_ENABLED = os.getenv("COROS_ENABLED", "true").strip().lower() != "false"
templates.env.globals["coros_enabled"] = COROS_ENABLED


@asynccontextmanager
async def lifespan(_app):
    # Half-hourly passes: Monday digests (services/digest.py) and the nightly
    # COROS pull (services/coros_sync.py).
    from src.api.services.scheduler import start_scheduler
    from src.api.services.digest import send_due_digests
    from src.api.services.coros_sync import sync_due_coros
    start_scheduler([send_due_digests, sync_due_coros] if COROS_ENABLED else [send_due_digests])
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(auth_router)

# SECRET_KEY signs the session cookie (see SessionMiddleware below). Without
# it — or with a hardcoded/guessable value — anyone could forge a session.
# Falling back to a random key keeps local dev working without extra setup,
# but it means sessions won't survive a restart and *must* be overridden with
# a real, persistent value in any shared/production environment.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print(
        "WARNING: SECRET_KEY is not set in the environment. Using a random, "
        "process-local key — all sessions will be invalidated on restart. "
        "Set SECRET_KEY in your .env for any shared or production deployment."
    )

# Password reset links are signed with the same key (see auth_routes.py).
app.state.secret_key = SECRET_KEY

# https_only should be True in any deployment served over HTTPS (e.g. Render).
# It defaults to False so local http://localhost dev keeps working; set
# SECURE_COOKIES=true in production environments.
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() == "true"

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="session",
    same_site="lax",
    https_only=SECURE_COOKIES,
    max_age=60 * 60 * 24 * 14,  # 14 days
)

# CORS: this app's own frontend is server-rendered and only ever calls the
# API via same-origin relative fetch() calls, so there's no legitimate need
# for a wildcard. allow_origins=["*"] combined with allow_credentials=True is
# also invalid per the CORS spec, which made browsers/Starlette effectively
# reflect any Origin — i.e. any third-party site could make credentialed
# requests against this API. ALLOWED_ORIGINS lets a real deployment add its
# own domain without editing code.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.middleware("http")
async def revalidate_static_files(request: Request, call_next):
    # The front end is plain ES modules with no build step or hashed file
    # names, so a deploy must not leave browsers on a cached old version.
    # no-cache keeps the files cached but revalidates them (ETag) on each load.
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


from src.api.routers import pages  # noqa: E402
from src.api.routers import activities  # noqa: E402
from src.api.routers import sync  # noqa: E402
from src.api.routers import workouts  # noqa: E402
from src.api.routers import analytics  # noqa: E402
from src.api.routers import races  # noqa: E402
from src.api.routers import profile  # noqa: E402
from src.api.routers import plan  # noqa: E402
from src.api.routers import strava_webhook  # noqa: E402
from src.api.routers import digest  # noqa: E402
from src.api.routers import coros  # noqa: E402
from src.api.routers import imports  # noqa: E402
from src.api.routers import pacing  # noqa: E402

app.include_router(pages.router)
app.include_router(activities.router)
app.include_router(sync.router)
app.include_router(workouts.router)
app.include_router(analytics.router)
app.include_router(races.router)
app.include_router(profile.router)
app.include_router(plan.router)
app.include_router(strava_webhook.router)
app.include_router(digest.router)
if COROS_ENABLED:
    app.include_router(coros.router)
app.include_router(imports.router)
app.include_router(pacing.router)

# A new install has no warehouse until the first sync. Creating the schema
# here means every page works from the first login instead of failing on
# missing tables. The connection stays open for the life of the process:
# DuckDB drops the database instance when its last connection closes, and
# request threads opening and closing their own connections can race that
# teardown for the file lock, which showed up as a rare 500 on the dashboard.
from src.pipeline import StravaDB  # noqa: E402
from src.api.deps import DB_PATH  # noqa: E402
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
warehouse_anchor = StravaDB(DB_PATH)
