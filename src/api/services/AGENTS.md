# services

Logic the routes in `../main.py` call, kept out of the route file.

- `metrics.py`: training status from the DuckDB warehouse: daily load to CTL/ATL/TSB/ACWR with a 14-day fitness projection, VO2 max estimates, pace zones around a threshold speed (`pace_zones_from_threshold_speed` is the pure part), performance standings.
- `llm.py`: one `call_llm` over the configured provider (groq, deepseek, gemini, or a local LM Studio), the shared coaching prompt clauses, response cleaning, and `cached_call` for the hourly dashboard insight.
- `coach.py`: `get_ai_insight`, the short coaching note on the dashboard and activity pages, built from the training status and performance standings.
- `sync.py`: background jobs with progress. `registry.start` runs `src.pipeline.sync_athlete` (kind `sync`) or `src.pipeline.recompute_metrics` (kind `recompute`, after a heart rate profile change) in a thread; `SyncJob.snapshot()` is what `GET /api/sync-strava/{user_id}` returns and the browser polls. `start_or_queue` runs one more sync after the current one when a webhook arrives mid-sync.
- `email.py`: `send_email` over SMTP from the `SMTP_*` variables; with no `SMTP_HOST` it prints the message to the server log instead.
- `digest.py`: the Monday morning email. `compose_digest` is the pure text builder (last week against the week before, plan compliance, fitness and form, new best efforts, this week's sessions, upcoming races); `build_digest` gathers those values from the warehouse and Postgres for one user; `send_due_digests` sends to opted-in users whose digest is due and stamps `digest_sent_at`; `start_scheduler` runs that check every half hour from a daemon thread started at app startup.

`../deps.py` owns the DuckDB connection and shared defaults so these modules never import `main.py`.
