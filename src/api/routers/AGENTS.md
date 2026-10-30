# routers

One `APIRouter` per concern, all mounted by `../main.py`. Each router does the session check through `get_current_user` (API) or `get_current_user_from_session` (pages) from `../auth_routes.py`, talks to Postgres through `SessionLocal` and to the DuckDB warehouse through `get_db_connection` in `../deps.py`.

- `pages.py`: the server-rendered pages (`/dashboard`, `/training-calendar`, `/warehouse`, `/workout_streams/{id}`, `/profile`). Markup only; data comes from the API routers below.
- `activities.py`: synced Strava activities: list, per-user list, detail, streams, laps (the watch's splits), effectiveness, AI note, delete (with the fixed table allowlist).
- `sync.py`: start a background Strava sync and read its progress (`src.api.services.sync`). `start_sync_for(user)` is the one place that builds a user's sync job; the webhook uses it too.
- `strava_webhook.py`: Strava's push subscription. `GET` answers the subscription handshake with `STRAVA_WEBHOOK_VERIFY_TOKEN`; `POST` takes activity create/update (queue a sync), delete (remove from the warehouse) and athlete deauthorization events. `scripts/strava_webhook.py` registers the subscription.
- `workouts.py`: the workout library CRUD.
- `analytics.py`: training status (with the coach's note), VO2 max, pace zones, dashboard analytics (HR zones, time by type, weekly volume), race predictions from best efforts with the VDOT ceiling (`/api/predictions`), aerobic efficiency on easy runs (`/api/efficiency`), personal records (`/api/records`).
- `races.py`: races CRUD.
- `profile.py`: profile updates and `GET /api/hr-profile/{user_id}` (resting and max heart rate in use, and where they come from). A changed heart rate profile starts a metrics recompute job.
- `plan.py`: training plan entries CRUD; `GET /api/projection/{user_id}`: fitness, fatigue and form stepped forward along the planned sessions (load per session from the athlete's TRIMP per kilometre scaled by workout type), with upcoming race dates; `POST /api/training-plan/{user_id}/build`: fills the calendar from tomorrow to a race with entries from `src/plan_builder.py`, starting from the last four weeks of run volume in the warehouse unless the body gives a weekly distance.
- `digest.py`: weekly digest email settings (`GET`/`POST /api/digest/{user_id}`), `GET .../preview` for the text the user would get, and `POST .../send` to send it now.
- `coros.py`: the COROS link: `GET /api/coros/{user_id}` status, `POST .../link` (logs in with email and password, keeps only the token, pulls a year of nights), `POST .../sync`, `DELETE /api/coros/{user_id}`, and `GET /api/recovery/{user_id}` with the nightly HRV, resting heart rate and time in bed for the dashboard.

A new endpoint goes in the router that owns its data; a new concern gets a new file here and one `include_router` line in `main.py`.
