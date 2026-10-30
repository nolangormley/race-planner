# tests

Unit tests (`pytest`, no network, no database server). DuckDB tests use `StravaDB(":memory:")`; Postgres-backed code is tested through SQLite or with fakes.

- `test_metrics.py`: load model (CTL/ATL/TSB/ACWR), VO2 max estimator pieces (steady-state mask, grade, Daniels VDOT, best efforts, Riegel), pace zones, planned-load projection.
- `test_pipeline.py`: TRIMP, decoupling, stream upserts, the incremental effectiveness pass, heart-rate zones, best efforts, and `ingest_data` with a fake Strava manager.
- `test_plan_builder.py`: the plan generator: every day covered to race day, long runs on the chosen day, ramp/cutback/taper of weekly volume, quality sessions, metric output.
- `test_sync.py`: the background job registry and its progress snapshots.
- `test_app.py`: the route table, so a router that fails to mount or a removed endpoint that returns fails CI.
- `test_digest.py`: when the weekly digest is due, the email text, and a scheduler pass sending once and stamping the user (SQLite in place of Postgres).
- `test_webhook.py`, `test_password_reset.py`, `test_llm_parsing.py`: the webhook dispatcher, reset tokens, LLM response cleaning.

Browser tests live in `../e2e/`.
