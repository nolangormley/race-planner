# scripts

Tools run by hand, not by the app.

- `demo.py`: the app with made-up data and no accounts. It serves the fake Strava and fake COROS from `../e2e/` in threads, points the environment at them and at `data/demo/warehouse.duckdb`, migrates `DATABASE_URL`, seeds the demo user, then starts the app and syncs and links COROS through the app's own API. Before syncing it checks that a `.env.dev` did not point the app at a real warehouse or the real Strava. `../docker-compose.demo.yml` runs it next to a throwaway Postgres.
- `demo_seed.py`: the demo user's rows (account, a half marathon ten weeks out, a plan from `src.plan_builder`). Separate from `demo.py` because the `src` modules read their settings when imported, so `demo.py` imports it only after setting the environment; `../tests/test_demo.py` runs it against SQLite.
- `strava_webhook.py`: create, view or delete the Strava push subscription.
- `rebuild_warehouse.py`: copy a warehouse into a fresh schema (README, "Repairing the warehouse").
- `coros_probe.py`: print the raw COROS dayDetail fields for an account, to check a field before mapping it.
- `manual_check_*.py`: one-off checks of the LLM providers' answers.
