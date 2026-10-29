# Race Planner & Activity Warehouse

A professional-grade training analytics platform and AI coaching engine built on your Strava data. This tool transforms raw activity streams into actionable physiological insights and customized training programs.

## 🚀 Key Features

*   **Training Load Analytics**: Visualize your Fitness (CTL), Fatigue (ATL), and Form (TSB) using the Banister impulse-response model.
*   **AI-Powered Race Coaching**: Select a target race from your calendar, and our AI (via Groq/Llama 3) builds a periodized training block tailored to your current fitness levels.
*   **Deep Activity Analysis**:
    *   **Efficiency Factor (EF)**: Tracking aerobic efficiency over time.
    *   **Aerobic Decoupling (Pa:Hr)**: Measure how well your heart rate holds steady during endurance runs.
    *   **Pace Zone Mapping**: Automatic calculation of physiological zones (Recovery to Anaerobic) based on your recent 90-day performance (see Methodology below for the exact algorithm).
*   **Performance Metrics Warehouse**: All data is stored locally in a high-performance **DuckDB** warehouse for lightning-fast queries and long-term history tracking.
*   **Modern Web UI**: A glass-morphic, responsive dashboard with interactive charts (Chart.js) and real-time AI executive summaries.

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
To sync your latest Strava activities and recalculate effectiveness metrics:
```bash
python src/pipeline.py
```

## 🏗 Project Structure

*   **/src/api**: FastAPI backend and Jinja2 templates (Dashboard, Warehouse, Activity Analysis).
*   **/src/pipeline.py**: The ingestion and analysis engine that calculates TRIMP, EF, and TSB — including `run_analyze_effectiveness()`, which computes per-activity physiological metrics from activity streams.
*   **/alembic**: Postgres schema migrations (`alembic upgrade head` to apply). The DuckDB warehouse's own schema is managed separately, inside `src/pipeline.py`'s `StravaDB.init_schema()`.
*   **/data**: Contains the `strava_warehouse.duckdb` file.

## 📊 Methodology
*   **Training Load**: Uses the Banister TRIMP model where `Fitness (CTL)` is a 42-day exponentially weighted moving average (EWMA) and `Fatigue (ATL)` is a 7-day EWMA.
*   **Pace Zones**: Calculated (`calculate_pace_zones()` in `src/api/main.py`) from a 90-day rolling window: the threshold speed is the 80th percentile of speed samples in the 85–92% max-HR band (or, with fewer than 30 such samples, the 90th percentile of all speed samples as a fallback), then Coros-style zone bands are derived as percentages of that threshold pace.
