# Race Planner & Activity Warehouse

A professional-grade training analytics platform and AI coaching engine built on your Strava data. This tool transforms raw activity streams into actionable physiological insights and customized training programs.

## 🚀 Key Features

*   **Training Load Analytics**: Visualize your Fitness (CTL), Fatigue (ATL), and Form (TSB) using the Banister impulse-response model.
*   **Training Plan Calendar**: Plan your weeks on a month calendar, attach the Strava activity that completed each session, and see planned against actual distance.
*   **Deep Activity Analysis**:
    *   **Efficiency Factor (EF)**: Tracking aerobic efficiency over time.
    *   **Aerobic Decoupling (Pa:Hr)**: Measure how well your heart rate holds steady during endurance runs.
    *   **Pace Zone Mapping**: Automatic calculation of physiological zones (Recovery to Anaerobic) based on your recent 90-day performance (see Methodology below for the exact algorithm).
*   **Performance Metrics Warehouse**: All data is stored locally in a high-performance **DuckDB** warehouse for lightning-fast queries and long-term history tracking.
*   **Web UI**: Server-rendered pages with a small dependency-free SVG chart module (see `src/api/static/js/charts/AGENTS.md`), dark and light themes, and a live progress bar for Strava syncs.

## 🛠 Setup

1.  **Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    Create a `.env` file in the root directory (see `.env.example` for the full list of variables, including `SECRET_KEY`/`DATABASE_URL`):
    ```env
    STRAVA_CLIENT_ID=your_id
    STRAVA_CLIENT_SECRET=your_secret
    GROQ_API_KEY=your_key
    ```

3.  **Database schema**:
    The Postgres schema (users, races, training plans) is managed by Alembic, not created automatically at startup. Run:
    ```bash
    alembic upgrade head
    ```
    `docker-compose up`/the Dockerfiles run this automatically on container start; for local `uvicorn` development, run it once yourself first.

4.  **Authentication**:
    Run the ingestion pipeline to authenticate with Strava and populate your warehouse:
    ```bash
    python src/pipeline.py
    ```

## 📈 Usage

### Running the Web Dashboard
The primary way to interact with the platform is via the web application.

**Option A: Local Uvicorn** (Recommended for development)
```bash
python -m uvicorn src.api.main:app --reload
```

**Option B: Docker Compose**
```bash
docker-compose up --build
```
Access the dashboard at `http://localhost:8000`.

> There are two Dockerfiles, and only one of them is what `docker-compose up`
> actually uses. `src/api/Dockerfile` is what `docker-compose.yml` builds for
> local dev (it runs `uvicorn --reload`). The root `Dockerfile` is a separate,
> deploy-oriented image (used by Render — it reads the `$PORT` env var and
> runs without `--reload`) and isn't referenced by docker-compose at all.
> Edit whichever one matches what you're actually trying to change.

### Data Ingestion
The **Sync Strava** button in the app is the normal way to pull activities.
To run the pipeline from the command line instead:
```bash
python src/pipeline.py
```
The running app holds the warehouse file open, so stop it first (`sudo systemctl stop race-planner` on the server); DuckDB allows one writing process at a time.

### Building a training plan
On the training plan page, **Build plan** fills the calendar from tomorrow to one of your upcoming races.
The generator (`src/plan_builder.py`) is deterministic: weekly distance starts from your last four weeks of running,
climbs no more than 10% a week toward a peak that suits the race distance, drops back every fourth week and tapers before the race.
Each week has a long run on the day you choose, up to two quality sessions (tempo and intervals), easy runs on the other training days and rest otherwise.
Every entry is an ordinary calendar entry afterwards, so edit or delete whatever does not fit.
Building again replaces planned days after today unless you untick that option, in which case only empty days are filled.


## Strava webhooks (automatic sync)

With a push subscription, Strava calls `/api/strava/webhook` whenever an activity is created, updated or deleted and the warehouse syncs itself; the Sync button becomes a fallback.

1. Set `STRAVA_WEBHOOK_VERIFY_TOKEN` to any secret string in the app's `.env` and restart the app.
2. With the same variables in your shell or `.env`, register the callback once per Strava application:

    ```bash
    python scripts/strava_webhook.py create https://your-host/api/strava/webhook
    python scripts/strava_webhook.py view
    ```

Strava validates the URL during `create`, so the app must already be reachable there. Events are not signed; an event can only start a sync for a known athlete or remove one activity, which the next sync restores if it still exists on Strava.


## Weekly digest email
Tick **Weekly digest** on the profile page to get a Monday morning email: last week's runs against the week before, how the plan went, fitness and form, new best efforts, this week's sessions and upcoming races.
Sending needs SMTP in `.env`, the same variables the password reset uses: `SMTP_HOST`, `SMTP_PORT` (587 for STARTTLS, 465 for implicit TLS), `SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM`.
Set `APP_URL` for the link in the footer.
Without `SMTP_HOST` the digest is written to the server log, and **Preview** on the profile page shows it in the browser.
A thread in the app checks every half hour and sends after 06:00 server time on Mondays; **Send now** sends it straight away.

## COROS (unofficial)
The COROS card on the profile page links a COROS Training Hub account and pulls the nightly readings the watch uploads: resting heart rate, overnight HRV with its baseline and training load. (Sleep is only in the phone app's API, which the app does not use because logging in there signs the phone app out; `scripts/coros_probe.py` prints the raw fields if you want to look for more.)
They show up in the Recovery card on the dashboard, and the 14-night median resting heart rate becomes the default for the heart-rate-reserve model whenever you have not typed a resting heart rate on your profile (a change of 2 bpm or more recomputes your metrics, as a profile change does).
Linking sends your email and password to COROS once, the way their web app does; only the session token is stored.
The app syncs once a day after 05:00 server time and the card has **Sync now**.
This uses the private API behind the Training Hub web app, not a public one: COROS can change it at any time, it rate-limits, and logging in to the Training Hub elsewhere can invalidate the stored session, in which case the card asks you to reconnect.

## Repairing the warehouse

Stream tables that were migrated with a rename in an earlier version keep a foreign-key reference to their old name inside DuckDB, and every delete on them fails at commit (`Table with name stream_x_new does not exist`). That breaks the activity Delete button and webhook deletes. Rebuild the file once, with the app stopped:

```bash
sudo systemctl stop race-planner
sudo -u raceplanner /opt/race-planner/.venv/bin/python scripts/rebuild_warehouse.py data/strava_warehouse.duckdb
# check the printed row counts, then swap:
sudo -u raceplanner mv data/strava_warehouse.duckdb data/strava_warehouse.duckdb.bak
sudo -u raceplanner mv data/strava_warehouse.rebuilt.duckdb data/strava_warehouse.duckdb
sudo systemctl start race-planner
```

The rebuild copies every table into a fresh schema (about 4 s for 400 activities) and leaves the original untouched until you swap it.

## 🏗 Project Structure

*   **/src/api**: FastAPI backend and Jinja2 templates (Dashboard, Warehouse, Activity Analysis).
*   **/src/pipeline.py**: The ingestion and analysis engine that calculates TRIMP, EF, and TSB — including `run_analyze_effectiveness()`, which computes per-activity physiological metrics from activity streams.
*   **/src/metrics.py**: Pure math shared by the pipeline and the API: load model, VO2 max estimator, Daniels VDOT, best efforts, planned-load projection.
*   **/src/plan_builder.py**: The deterministic training plan generator behind **Build plan**.
*   **/alembic**: Postgres schema migrations (`alembic upgrade head` to apply). The DuckDB warehouse's own schema is managed separately, inside `src/pipeline.py`'s `StravaDB.init_schema()`.
*   **/data**: Contains the `strava_warehouse.duckdb` file.

## 📊 Methodology
*   **Training Load**: Uses the Banister TRIMP model where `Fitness (CTL)` is a 42-day exponentially weighted moving average (EWMA) and `Fatigue (ATL)` is a 7-day EWMA.
*   **Pace Zones**: Calculated (`calculate_pace_zones()` in `src/api/main.py`) from a 90-day rolling window: the threshold speed is the 80th percentile of speed samples in the 85–92% max-HR band (or, with fewer than 30 such samples, the 90th percentile of all speed samples as a fallback), then Coros-style zone bands are derived as percentages of that threshold pace.
