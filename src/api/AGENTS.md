# api

The FastAPI app. Postgres (SQLAlchemy, `database.py`) holds users, races and training plans; the DuckDB warehouse (`deps.py`, filled by `../pipeline.py`) holds Strava activities, streams and computed effectiveness.

- `main.py`: environment, app, session and CORS middleware, static files, router mounting, the scheduler. No routes of its own. `COROS_ENABLED=false` leaves out the COROS router and nightly pull and sets the `coros_enabled` template global the pages use.
- `web.py`: the shared Jinja2 template environment.
- `auth_routes.py`: sign-up, login, first race, Strava OAuth, and the `get_current_user*` dependencies every router uses.
- `database.py`, `schemas.py`: ORM models and request bodies.
- `routers/`: the endpoints, one file per concern.
- `services/`: logic the routers call (metrics, LLM, coach's note, background sync).
- `static/`, `templates/`: the front end.

Schema changes go through Alembic (`../../alembic`); the systemd unit runs `alembic upgrade head` before starting the app.
