# services

Logic the routes in `../main.py` call, kept out of the route file.

- `metrics.py`: training status from the DuckDB warehouse: daily load to CTL/ATL/TSB/ACWR with a 14-day fitness projection, VO2 max estimates, pace zones around a threshold speed (`pace_zones_from_threshold_speed` is the pure part), performance standings.
- `llm.py`: one `call_llm` over the configured provider (groq, deepseek, gemini, or a local LM Studio), the shared coaching prompt clauses, response cleaning, and `cached_call` for the hourly dashboard insight.
- `coach.py`: `get_ai_insight`, the short coaching note on the dashboard and activity pages, built from the training status and performance standings.
- `sync.py`: background Strava syncs. `registry.start` runs `src.pipeline.sync_athlete` in a thread with a progress callback; `SyncJob.snapshot()` is what `GET /api/sync-strava/{user_id}` returns and the browser polls.
- `email.py`: `send_email` over SMTP from the `SMTP_*` variables; with no `SMTP_HOST` it prints the message to the server log instead.

`../deps.py` owns the DuckDB connection and shared defaults so these modules never import `main.py`.
