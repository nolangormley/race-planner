"""Browser tests against the real app: a fresh Postgres schema, a temporary
DuckDB warehouse, a fake Strava, the app in a subprocess, and Chromium.

Needs E2E_DATABASE_URL (a Postgres database this suite may wipe) and the
Playwright Chromium build (`playwright install chromium`).
"""
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg2
import pytest
import requests
import uvicorn
from playwright.sync_api import sync_playwright

from e2e.fake_strava import ATHLETE_ID, make_app
from e2e.fake_coros import make_app as fake_coros_app

REPO = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("E2E_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/raceplanner_e2e")
USER = {"email": "e2e@example.com", "password": "e2e-password-123", "name": "E2E Runner"}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url, seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=2).status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up in {seconds}s")


@pytest.fixture(scope="session")
def fake_strava():
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(make_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    wait_for(f"{base}/api/v3/athlete", 30)
    yield base
    server.should_exit = True


@pytest.fixture(scope="session")
def database():
    """Drops and recreates the public schema, then migrates it."""
    connection = psycopg2.connect(DATABASE_URL)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    connection.close()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=REPO, check=True,
                   env={**os.environ, "DATABASE_URL": DATABASE_URL})
    return DATABASE_URL


@pytest.fixture(scope="session")
def fake_coros():
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(fake_coros_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    wait_for(f"{base}/dashboard/query", 30)
    yield base
    server.should_exit = True


@pytest.fixture(scope="session")
def app_server(tmp_path_factory, fake_strava, fake_coros, database):
    port = free_port()
    workdir = tmp_path_factory.mktemp("app")
    env = {
        **os.environ,
        "DATABASE_URL": database,
        "DB_PATH": str(workdir / "warehouse.duckdb"),
        "STRAVA_API_BASE": fake_strava,
        "COROS_API_BASE": fake_coros,
        "STRAVA_CLIENT_ID": "e2e", "STRAVA_CLIENT_SECRET": "e2e",
        "SECRET_KEY": "e2e-secret", "SECURE_COOKIES": "false",
        "LLM_PROVIDER": "local",
        "HOST_URL": f"http://127.0.0.1:{port}", "ALLOWED_ORIGINS": f"http://127.0.0.1:{port}",
    }
    # cwd is the temp dir so the repo's .env/.env.dev are not picked up.
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api.main:app", "--app-dir", str(REPO), "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=workdir, env=env,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_for(f"{base}/login")
        yield base
    finally:
        process.terminate()
        process.wait(timeout=15)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


# favicon: none is served. 404: how the data endpoints say "nothing yet"; the
# pages render an empty state. The net:: errors are fetches cut off by a
# navigation, not server failures.
IGNORED_CONSOLE = ("favicon", "status of 404", "net::ERR_SOCKET_NOT_CONNECTED", "net::ERR_ABORTED")


class Problems(list):
    """Console errors and page errors collected during a test. A 404 is how
    the data endpoints say "nothing yet" and the pages render an empty
    state for it, so those are not failures; a 500 or a script error is."""

    def attach(self, page):
        page.on("pageerror", lambda error: self.append(f"pageerror: {error}"))
        page.on("console", lambda message: self.append(f"console: {message.text}")
                if message.type == "error" and not any(skip in message.text for skip in IGNORED_CONSOLE) else None)


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    problems = Problems()
    problems.attach(page)
    page.problems = problems
    yield page
    context.close()
    assert not problems, f"browser errors: {problems}"


@pytest.fixture(scope="session")
def registered_user(app_server, database):
    """Registers the test user through the real form and links a Strava
    athlete the way the OAuth callback would."""
    session = requests.Session()
    response = session.post(f"{app_server}/register", data={
        "name": USER["name"], "email": USER["email"], "password": USER["password"],
        "date_of_birth": "1990-05-04", "gender": "Male", "height": "1.78", "weight": "70",
    }, allow_redirects=False)
    assert response.status_code == 302, response.text
    connection = psycopg2.connect(database)
    with connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE users SET strava_athlete_id = %s, strava_refresh_token = %s WHERE email = %s",
            (ATHLETE_ID, "fake-refresh-token", USER["email"]),
        )
    connection.close()
    return USER


def login(page, base, user=USER):
    page.goto(f"{base}/login")
    page.fill("input[name=username]", user["email"])
    page.fill("input[name=password]", user["password"])
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard")
