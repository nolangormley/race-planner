# routers

One `APIRouter` per concern, all mounted by `../main.py`. Each router does the session check through `get_current_user` (API) or `get_current_user_from_session` (pages) from `../auth_routes.py`, talks to Postgres through `SessionLocal` and to the DuckDB warehouse through `get_db_connection` in `../deps.py`.

- `pages.py`: the server-rendered pages (`/dashboard`, `/training-calendar`, `/warehouse`, `/workout_streams/{id}`, `/profile`). Markup only; data comes from the API routers below.
- `activities.py`: synced Strava activities: list, per-user list, detail, streams, effectiveness, AI note, delete (with the fixed table allowlist).
- `sync.py`: start a background Strava sync and read its progress (`src.api.services.sync`).
- `workouts.py`: the workout library CRUD.
- `analytics.py`: training status (with the coach's note), VO2 max, pace zones, dashboard analytics (HR zones, time by type, weekly volume), race predictions from VDOT (`/api/predictions`), aerobic efficiency on easy runs (`/api/efficiency`).
- `races.py`: races CRUD.
- `profile.py`: profile updates and `GET /api/hr-profile/{user_id}` (resting and max heart rate in use, and where they come from). A changed heart rate profile starts a metrics recompute job.
- `plan.py`: training plan entries CRUD.

A new endpoint goes in the router that owns its data; a new concern gets a new file here and one `include_router` line in `main.py`.
