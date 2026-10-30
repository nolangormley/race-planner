# services

Logic the routes in `../main.py` call, kept out of the route file.

- `metrics.py`: training status from the DuckDB warehouse: daily load to CTL/ATL/TSB/ACWR with a 14-day fitness projection, VO2 max estimates, pace zones around a threshold speed (`pace_zones_from_threshold_speed` is the pure part), performance standings.
- `llm.py`: one `call_llm` over the configured provider (groq, deepseek, gemini, or a local LM Studio), the shared coaching prompt clauses, response cleaning, and `cached_call` for the hourly dashboard insight.
- `coach.py`: `get_ai_insight`, the short coaching note on the dashboard and activity pages, built from the training status and performance standings.
- `sync.py`: background jobs with progress. `registry.start` runs `src.pipeline.sync_athlete` (kind `sync`) or `src.pipeline.recompute_metrics` (kind `recompute`, after a heart rate profile change) in a thread; `SyncJob.snapshot()` is what `GET /api/sync-strava/{user_id}` returns and the browser polls. `start_or_queue` runs one more sync after the current one when a webhook arrives mid-sync.
- `email.py`: `send_email` over SMTP from the `SMTP_*` variables; with no `SMTP_HOST` it prints the message to the server log instead.
- `digest.py`: the Monday morning email. `compose_digest` is the pure text builder (last week against the week before, plan compliance, fitness and form, new best efforts, this week's sessions, upcoming races); `build_digest` gathers those values from the warehouse and Postgres for one user; `send_due_digests` is the scheduler pass that sends to opted-in users whose digest is due and stamps `digest_sent_at`.
- `coros_sync.py`: the COROS link. `sync_coros` pulls the nights since the last sync (a year on the first) into `coros_daily`, stamps the user, records failures on `coros_error`, and when the athlete has no typed resting heart rate and the COROS median moved by 2 bpm or more starts a recompute like a profile change; `unlink_coros` forgets the session and the nights; `sync_due_coros` is the scheduler pass (once a day after 05:00, skipped while the session is marked expired).
- `scheduler.py`: `start_scheduler(jobs)` runs the passes above every half hour from a daemon thread started in `main.py`'s lifespan; the first pass is one interval after start.
- `metrics.py` also owns `coros_resting_hr` (14-night median) and `resolve_resting_hr(con, user)`: the profile value, else the COROS median, else `DEFAULT_REST_HR`. Every route that feeds the heart-rate-reserve model goes through it.

`../deps.py` owns the DuckDB connection and shared defaults so these modules never import `main.py`.
