import pytest
from fastapi.routing import APIRoute


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
        ("POST", "/profile/{user_id}"),
        ("GET", "/api/hr-profile/{user_id}"),
        ("GET", "/api/activities"),
        ("DELETE", "/api/activities/{activity_id}"),
        ("GET", "/api/activities/{activity_id}/intervals"),
        ("GET", "/api/activities/{activity_id}/streams"),
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
        ("GET", "/api/training-plan/{user_id}"),
        ("PUT", "/api/training-plan/entry/{entry_id}"),
        ("GET", "/api/strava/webhook"),
        ("POST", "/api/strava/webhook"),
        ("POST", "/login"),
        ("GET", "/strava-callback"),
    }
    missing = expected - routes
    assert not missing, f"routes missing from the app: {sorted(missing)}"


def test_removed_endpoints_stay_removed(app):
    paths = {path for _, path in route_table(app)}
    for gone in ("/schedule/{user_id}", "/api/training-plan/{user_id}/chat", "/api/mileage-summary/{user_id}",
                 "/users/", "/recommend/{user_id}", "/settings/{user_id}"):
        assert gone not in paths, gone
