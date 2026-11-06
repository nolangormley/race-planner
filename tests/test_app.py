from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    # Importing the app creates the warehouse schema at DB_PATH, so point it
    # at a temp file rather than a real warehouse another process may hold.
    import src.api.deps as deps
    deps.DB_PATH = str(tmp_path_factory.mktemp("warehouse") / "warehouse.duckdb")
    from src.api.main import app
    return app


def route_table(app):
    return {(method, route.path) for route in app.routes if isinstance(route, APIRoute) for method in route.methods}


def test_every_router_is_mounted(app):
    routes = route_table(app)
    expected = {
        ("GET", "/dashboard"),
        ("GET", "/training-calendar"),
        ("GET", "/warehouse"),
        ("GET", "/workout_streams/{activity_id}"),
        ("GET", "/profile"),
        ("GET", "/privacy"),
        ("POST", "/profile/{user_id}"),
        ("DELETE", "/api/account"),
        ("GET", "/api/hr-profile/{user_id}"),
        ("GET", "/api/readiness/{user_id}"),
        ("GET", "/api/energy/{user_id}"),
        ("GET", "/api/activities"),
        ("DELETE", "/api/activities/{activity_id}"),
        ("GET", "/api/activities/{activity_id}/intervals"),
        ("GET", "/api/activities/{activity_id}/streams"),
        ("POST", "/api/activities/import"),
        ("POST", "/api/sync-strava/{user_id}"),
        ("GET", "/api/sync-strava/{user_id}"),
        ("GET", "/api/workouts"),
        ("GET", "/status/{user_id}"),
        ("GET", "/analytics/{user_id}"),
        ("GET", "/pace_zones/{user_id}"),
        ("GET", "/vo2max/{user_id}"),
        ("GET", "/api/predictions/{user_id}"),
        ("GET", "/api/efficiency/{user_id}"),
        ("GET", "/api/projection/{user_id}"),
        ("GET", "/api/races/{user_id}"),
        ("GET", "/races/{race_id}/pacing"),
        ("GET", "/api/races/{race_id}/pacing"),
        ("POST", "/api/races/{race_id}/course"),
        ("DELETE", "/api/races/{race_id}/course"),
        ("GET", "/api/races/{race_id}/course-library"),
        ("POST", "/api/races/{race_id}/course/library"),
        ("GET", "/api/race-catalog"),
        ("GET", "/api/courses"),
        ("GET", "/api/courses/{race}/{event}/pacing"),
        ("GET", "/courses"),
        ("GET", "/courses/{race}/{event}"),
        ("GET", "/sitemap.xml"),
        ("GET", "/robots.txt"),
        ("GET", "/api/training-plan/{user_id}"),
        ("PUT", "/api/training-plan/entry/{entry_id}"),
        ("GET", "/api/strava/webhook/{token}"),
        ("POST", "/api/strava/webhook/{token}"),
        ("POST", "/login"),
        ("GET", "/strava-callback"),
    }
    missing = expected - routes
    assert not missing, f"routes missing from the app: {sorted(missing)}"


def test_energy_answers_only_for_the_signed_in_user(app):
    from src.api.auth_routes import get_current_user

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, strava_athlete_id=None, coros_access_token=None)
    try:
        client = TestClient(app)
        assert client.get("/api/energy/2").status_code == 403
        assert client.get("/api/energy/1?days=3").json() == {"available": False}
    finally:
        app.dependency_overrides.clear()


def test_intervals_tell_a_run_not_analysed_yet_from_one_with_nothing_found(app):
    import src.api.deps as deps
    from src.api.auth_routes import get_current_user
    from src.pipeline import StravaDB

    warehouse = StravaDB(deps.DB_PATH)
    warehouse.con.execute("INSERT INTO dim_athlete (athlete_id, sex) VALUES (7, 'F')")
    for activity_id in (71, 72):
        warehouse.con.execute("INSERT INTO dim_activity (activity_id, athlete_id, type) VALUES (?, 7, 'Run')", [activity_id])
        warehouse.con.execute("INSERT INTO stream_velocity (activity_id, time_offset, value) VALUES (?, 0, 3.0)", [activity_id])
    warehouse.con.execute("INSERT INTO activity_structure (activity_id, kind, rep_count) VALUES (72, NULL, 0)")
    warehouse.con.close()

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7, athlete_id=7)
    try:
        client = TestClient(app)
        nothing = {"kind": None, "reps": [], "recoveries": []}
        assert client.get("/api/activities/71/intervals").json() == {**nothing, "checked": False, "pending": True}
        assert client.get("/api/activities/72/intervals").json() == {**nothing, "checked": True, "pending": False}
        assert client.get("/api/activities/73/intervals").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_removed_endpoints_stay_removed(app):
    paths = {path for _, path in route_table(app)}
    for gone in ("/schedule/{user_id}", "/api/training-plan/{user_id}/chat", "/api/mileage-summary/{user_id}",
                 "/users/", "/recommend/{user_id}", "/settings/{user_id}", "/api/activities/{activity_id}/ai_summary"):
        assert gone not in paths, gone
