"""Race Planner with made-up data and no accounts.

Serves a fake Strava and a fake COROS (the stand-ins the browser tests use,
reused here on purpose), migrates DATABASE_URL, creates the demo user with a
race and a plan, then starts the app and, once it answers, syncs about six
months of runs and links two months of COROS nights through the app's own API.

    DATABASE_URL=postgresql://user:pass@localhost/raceplanner python scripts/demo.py

or, with only Docker installed:

    docker compose -f docker-compose.demo.yml up --build

Log in as demo@example.com / demo1234. The demo keeps its own warehouse,
data/demo/warehouse.duckdb, recreated on every start, and writes no rows in
the database but the demo user's.
"""
import argparse
import os
import secrets
import socket
import subprocess
import sys
import threading
import time

import requests
import uvicorn

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
# The fakes are test code (e2e/); the demo serves them on purpose.
from e2e import fake_coros, fake_strava  # noqa: E402

RUNS = 60
SPAN_DAYS = 175          # inside the 180 days a first sync reads
NIGHTS = 60
WAREHOUSE = os.path.join(REPO, "data", "demo", "warehouse.duckdb")
JOB_TIMEOUT_S = 600


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until_up(url, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=2).status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not answer within {seconds} s")


def serve_in_thread(app, probe_path):
    """Serves a fake on a free local port and returns its base URL."""
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    wait_until_up(base + probe_path, 30)
    return base


def wait_for_job(session, base, user_id):
    """Waits until the user's background sync or recompute has finished."""
    deadline = time.time() + JOB_TIMEOUT_S
    while time.time() < deadline:
        job = session.get(f"{base}/api/sync-strava/{user_id}").json()["job"]
        if job is None or job["status"] == "done":
            return
        if job["status"] == "error":
            raise RuntimeError(f"the {job['kind']} failed: {job['error']}")
        time.sleep(1)
    raise RuntimeError(f"the background job did not finish within {JOB_TIMEOUT_S} s")


def fill(server, base, public_url, user_id, strava_base, failed):
    """Logs in as the demo user, syncs the fake Strava history, links the fake
    COROS account and waits for the recompute that follows, then says where to
    look. Stops the app when a step fails."""
    from scripts.demo_seed import EMAIL, PASSWORD
    try:
        wait_until_up(f"{base}/login")
        # The app loads .env.dev over the environment; make sure that did not
        # point it at a real warehouse or the real Strava before syncing.
        from src import pipeline
        from src.api import deps
        if os.path.abspath(deps.DB_PATH) != WAREHOUSE or pipeline.STRAVA_API_BASE != strava_base:
            raise RuntimeError(f"DB_PATH or STRAVA_API_BASE is set in a .env.dev file; move it aside to run the demo")
        session = requests.Session()
        login = session.post(f"{base}/login", data={"username": EMAIL, "password": PASSWORD}, allow_redirects=False)
        if login.status_code != 302:
            raise RuntimeError("could not log in as the demo user")
        print(f"Syncing {RUNS} made-up runs from the fake Strava...", flush=True)
        session.post(f"{base}/api/sync-strava/{user_id}").raise_for_status()
        wait_for_job(session, base, user_id)
        if os.getenv("COROS_ENABLED", "true").strip().lower() != "false":
            print(f"Linking the fake COROS account ({NIGHTS} nights)...", flush=True)
            session.post(f"{base}/api/coros/{user_id}/link", json={"email": fake_coros.EMAIL, "password": fake_coros.PASSWORD, "region": "us"}).raise_for_status()
            wait_for_job(session, base, user_id)
    except Exception as error:
        print(f"\nDemo setup failed: {error}", file=sys.stderr, flush=True)
        failed.set()
        server.should_exit = True
        return
    print(f"\nRace Planner demo is ready at {public_url}\nLog in as {EMAIL} / {PASSWORD}. Ctrl-C stops it.\n", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Race Planner with made-up data and no accounts.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to serve on (0.0.0.0 inside Docker)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--public-url", help="the address to print and to build links with (default http://localhost:PORT)")
    args = parser.parse_args()
    if not os.getenv("DATABASE_URL"):
        sys.exit("Set DATABASE_URL to a Postgres database the demo may migrate (it writes only the demo user's rows),\n"
                 "or run it with Docker: docker compose -f docker-compose.demo.yml up --build")
    public_url = args.public_url or f"http://localhost:{args.port}"

    runs = fake_strava.synthetic_runs(RUNS, span_days=SPAN_DAYS)
    strava_base = serve_in_thread(fake_strava.make_app(runs), "/api/v3/athlete")
    coros_base = serve_in_thread(fake_coros.make_app(NIGHTS), "/dashboard/query")
    os.environ.update({
        "STRAVA_API_BASE": strava_base, "COROS_API_BASE": coros_base, "DB_PATH": WAREHOUSE,
        "STRAVA_CLIENT_ID": "demo", "STRAVA_CLIENT_SECRET": "demo",
        "HOST_URL": public_url, "ALLOWED_ORIGINS": public_url, "SECURE_COOKIES": "false",
    })
    os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
    for leftover in (WAREHOUSE, WAREHOUSE + ".wal"):
        if os.path.exists(leftover):
            os.remove(leftover)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=REPO, check=True)

    # The src modules read their settings when imported, so they come in only now.
    from scripts.demo_seed import seed_demo
    from src.api.database import SessionLocal
    db = SessionLocal()
    try:
        user_id = seed_demo(db, runs, fake_strava.ATHLETE_ID).id
    finally:
        db.close()

    server = uvicorn.Server(uvicorn.Config("src.api.main:app", host=args.host, port=args.port, log_level="warning"))
    failed = threading.Event()
    threading.Thread(target=fill, args=(server, f"http://127.0.0.1:{args.port}", public_url, user_id, strava_base, failed), daemon=True).start()
    server.run()
    if failed.is_set():
        sys.exit(1)


if __name__ == "__main__":
    main()
