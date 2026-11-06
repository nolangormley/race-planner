# api

The FastAPI app. Postgres (SQLAlchemy, `database.py`) holds users, races and training plans; the DuckDB warehouse (`deps.py`, filled by `../pipeline.py`) holds Strava activities, streams and computed effectiveness.

- `main.py`: environment, app, session and CORS middleware, static files, router mounting, the scheduler. No routes of its own. It keeps one warehouse connection open for its whole life (`warehouse_anchor`) and checkpoints through it at shutdown, so a stopped app leaves no `.wal` next to the warehouse file. `COROS_ENABLED=false` leaves out the COROS router and nightly pull and sets the `coros_enabled` template global the pages use. `is_admin` from `auth_routes.py` is a template global too, for the top bar's Admin link.
- `web.py`: the shared Jinja2 template environment.
- `auth_routes.py`: sign-up, login, first race, Strava OAuth, and the `get_current_user*` dependencies every router uses; they stamp `users.last_seen_at` through `touch_last_seen`, at most once a day. `is_admin` reads `ADMIN_EMAILS` (comma-separated, any case; unset means nobody), and `get_admin` is the dependency for the admin API, a 404 for anyone else.
- `database.py`, `schemas.py`: ORM models and request bodies. `User.athlete_id` is the warehouse key: the Strava athlete id, or `1_000_000_000_000 + user id` for a user who only imports files. Read the warehouse with it, never with `strava_athlete_id`; the Strava callback moves the placeholder's rows when the account is linked. `users.created_at` (set at sign-up, NULL for older accounts) and `last_seen_at` feed the admin page; `Feedback` is a note from the Feedback button, with the page it was written on.
- `routers/`: the endpoints, one file per concern.
- `services/`: logic the routers call (metrics, background sync, imports, the digest, COROS).
- `static/`, `templates/`: the front end.

Schema changes go through Alembic (`../../alembic`); the systemd unit runs `alembic upgrade head` before starting the app.
