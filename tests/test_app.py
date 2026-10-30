from fastapi.routing import APIRoute

from src.api.main import app


def route_table():
    return {(method, route.path) for route in app.routes if isinstance(route, APIRoute) for method in route.methods}


def test_every_router_is_mounted():
    routes = route_table()
    expected = {
        ("GET", "/dashboard"),
        ("GET", "/training-calendar"),
        ("GET", "/warehouse"),
        ("GET", "/workout_streams/{activity_id}"),
        ("GET", "/profile"),
        ("POST", "/profile/{user_id}"),
        ("GET", "/api/activities"),
        ("DELETE", "/api/activities/{activity_id}"),
        ("GET", "/api/activities/{activity_id}/streams"),
        ("POST", "/api/sync-strava/{user_id}"),
        ("GET", "/api/sync-strava/{user_id}"),
        ("GET", "/api/workouts"),
        ("GET", "/status/{user_id}"),
        ("GET", "/analytics/{user_id}"),
        ("GET", "/pace_zones/{user_id}"),
        ("GET", "/vo2max/{user_id}"),
        ("GET", "/api/races/{user_id}"),
        ("GET", "/api/training-plan/{user_id}"),
        ("PUT", "/api/training-plan/entry/{entry_id}"),
        ("POST", "/login"),
        ("GET", "/strava-callback"),
    }
    missing = expected - routes
    assert not missing, f"routes missing from the app: {sorted(missing)}"


def test_removed_endpoints_stay_removed():
    paths = {path for _, path in route_table()}
    for gone in ("/schedule/{user_id}", "/api/training-plan/{user_id}/chat", "/api/mileage-summary/{user_id}",
                 "/users/", "/recommend/{user_id}", "/settings/{user_id}"):
        assert gone not in paths, gone
