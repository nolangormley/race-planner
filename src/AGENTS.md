# src

The data side of Race Planner: ingestion into the DuckDB warehouse, the training math, and the plan generator. The web app lives in `api/`.

- `pipeline.py`: Strava client (`StravaManager`), the warehouse schema and writes (`StravaDB`), and the sync passes: `ingest_data` (activities, streams, laps), `run_analyze_effectiveness` (per-activity TRIMP, decoupling, zones), `run_best_efforts`, and `sync_athlete`, which runs them in order and reports progress.
- `metrics.py`: pure functions with no I/O: Banister load model (CTL/ATL/TSB), heart-rate reserve zones, the VO2 max estimator, Daniels VDOT and race equivalents, best efforts with Riegel predictions, and the planned-load projection used by the calendar.
- `plan_builder.py`: deterministic training plan generator. `build_plan` returns one entry per day from a start date to race day, with weekly volume ramped from the athlete's current distance toward a race-specific peak, cutback weeks and a taper; `summarize` describes a plan for the confirmation toast. `api/routers/plan.py` writes its output into the calendar.
- `pacing.py`: race-day pacing, pure. `plan_splits` turns a distance, goal and split length into splits with grade, target pace, split and cumulative time (even or negative strategy; with a course profile the pace carries `grade_factor`, Minetti's running cost relative to flat, downhill halved) and rescales so the clock ends on the goal exactly. `parse_gpx_course` reads a GPX into a per-100 m profile; `course_profile` stretches it to the race distance and warns when the length is off by more than 3%. `parse_clock` is the shared goal-time parser.
- `coros.py`: client for the private COROS Training Hub API: `login` (MD5 password, returns the session token and user id), `CorosClient.daily` (nightly resting heart rate, HRV and baseline, time in bed, training load, in 90-day chunks) and `verify`. `COROS_API_BASE` points it at a stand-in server in tests. The API is unofficial and can change; errors are `CorosError` / `CorosAuthError`.
- `scrape_coros_workouts.py`: standalone Playwright scraper that reads structured workouts from the COROS web app into the warehouse. Not used by the web app.

Unit tests for all of this are in `../tests/`.
