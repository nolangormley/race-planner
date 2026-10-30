# e2e

Browser tests against the real app, run by the `e2e` CI job before every deploy and kept out of `pytest`'s default collection (`testpaths` is `tests/`).

- `conftest.py`: session fixtures. `database` drops and recreates the public schema of `E2E_DATABASE_URL`, then runs Alembic. `fake_strava` serves `fake_strava.py` in a thread. `app_server` starts uvicorn in a subprocess with a temporary DuckDB warehouse, `STRAVA_API_BASE` pointing at the fake, and a temp working directory so the repo's `.env` is not read. `registered_user` signs up through the real form and links the fake athlete the way the OAuth callback would. `page` collects console and page errors and fails the test if any appear.
- `fake_strava.py`: token, athlete, activity list, streams and laps endpoints with three synthetic 40-minute runs.
- `fake_coros.py`: the Training Hub login, dashboard and dayDetail endpoints with two weeks of synthetic nights; the app reaches it through `COROS_API_BASE`.
- `test_flows.py`: sign-up to the Strava prompt, dashboard empty states, a sync from the button with the progress panel and the filled dashboard, every page rendering, calendar entry add/edit/delete, a heart rate profile change triggering a recompute, Build plan filling the calendar to race day, the weekly digest preview and switch, linking COROS and the recovery card, the pacing page building splits for a race with a goal, a user without Strava importing a GPX file on the Activities page (then the repeat skipped, and the run on the dashboard). Tests run in file order; the sync test fills the warehouse the later tests read.

Run locally: `E2E_DATABASE_URL=postgresql://user:pass@host/db pytest e2e -q` with `playwright install chromium` done once.
