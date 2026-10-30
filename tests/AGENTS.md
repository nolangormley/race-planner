# tests

Unit tests (`pytest`, no network, no database server). DuckDB tests use `StravaDB(":memory:")`; Postgres-backed code is tested through SQLite or with fakes.

- `test_metrics.py`: load model (CTL/ATL/TSB/ACWR), VO2 max estimator pieces (steady-state mask, grade, Daniels VDOT, best efforts, Riegel), pace zones, planned-load projection.
- `test_pipeline.py`: TRIMP, decoupling, stream upserts, the incremental effectiveness pass, heart-rate zones, best efforts, and `ingest_data` with a fake Strava manager.
- `test_plan_builder.py`: the plan generator: every day covered to race day, long runs on the chosen day, ramp/cutback/taper of weekly volume, quality sessions, metric output.
- `test_importers.py`: FIT and GPX parsing: the `fixtures/sample_run.fit` fixture (summary, local time from the watch's offset, streams, laps), FIT records without a session, sport mapping and names, GPX tracks and time offsets, refused files, the activity id range.
- `test_imports.py`: the import service on an in-memory warehouse (activity, streams and analysis written, a `dim_athlete` row for a file-only user, an imported track session's reps detected), skipping repeats and runs already synced, moving a file-only user's rows (with structure and reps) to their Strava id, and the `User.athlete_id` resolver.
- `test_warehouse_maintenance.py`: every `activity_id`-keyed table is in `src.pipeline.ACTIVITY_CHILD_TABLES`, deleting an activity with children, and the warehouse rebuild script copying every table.
- `test_sync.py`: the background job registry and its progress snapshots.
- `test_intervals.py`: the interval detector on synthetic streams (track reps, steady run, tempo, a short fast block, rolling hills at even effort, auto-pauses, standing rests, missing readings) and the warehouse pass.
- `test_plan_match.py`: marking planned sessions done from synced runs, with SQLite in place of Postgres and an in-memory warehouse.
- `test_app.py`: the route table, so a router that fails to mount or a removed endpoint that returns fails CI.
- `test_demo.py`: the demo's seeding (user, race, plan from the fake history's volume) against SQLite, and that seeding again replaces only the demo rows.
- `test_fakes.py`: the fake Strava and COROS the browser tests and the demo use: the default runs unchanged, the varied history's span, kinds and streams agreeing with summaries, mile laps, paging, nights.
- `test_digest.py`: when the weekly digest is due, the email text, and a scheduler pass sending once and stamping the user (SQLite in place of Postgres).
- `test_pacing.py`: clock parsing, even and negative splits summing to the goal, the grade cost curve, a hilly profile slowing the climbs, GPX parsing of an inline course, the stretched-course warning, rejected files, and the prediction the goal falls back to.
- `test_coros.py`: the COROS client against a fake HTTP session (login hashing, chunked daily fetch, auth and rate-limit errors), and the sync into an in-memory warehouse: nights stored, resting heart rate default and recompute trigger, expired sessions, unlink.
- `test_webhook.py`, `test_password_reset.py`, `test_llm_parsing.py`: the webhook dispatcher, reset tokens, LLM response cleaning.

`conftest.py` has `make_gpx`, a factory for GPX bytes of a straight run with heart rate, at a steady pace or in `(seconds, speed)` blocks. `fixtures/sample_run.fit` is a ten-minute, two-lap run written by `fixtures/make_fit_fixture.py` (a minimal FIT encoder; run it only to change the fixture).

Browser tests live in `../e2e/`.
