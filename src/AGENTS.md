# src

The data side of Race Planner: ingestion into the DuckDB warehouse, the training math, and the plan generator. The web app lives in `api/`.

- `pipeline.py`: Strava client (`StravaManager`), the warehouse schema and writes (`StravaDB`), and the sync passes: `ingest_data` (activities, streams, laps), `run_analyze_effectiveness` (per-activity TRIMP, decoupling, zones), `run_best_efforts`, and `sync_athlete`, which runs them in order and reports progress.
- `metrics.py`: pure functions with no I/O: Banister load model (CTL/ATL/TSB), heart-rate reserve zones, the VO2 max estimator, Daniels VDOT and race equivalents, best efforts with Riegel predictions, and the planned-load projection used by the calendar.
- `plan_builder.py`: deterministic training plan generator. `build_plan` returns one entry per day from a start date to race day, with weekly volume ramped from the athlete's current distance toward a race-specific peak, cutback weeks and a taper; `summarize` describes a plan for the confirmation toast. `api/routers/plan.py` writes its output into the calendar.
- `scrape_coros_workouts.py`: standalone Playwright scraper that reads structured workouts from the COROS web app into the warehouse. Not used by the web app.

Unit tests for all of this are in `../tests/`.
