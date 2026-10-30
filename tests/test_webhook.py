from types import SimpleNamespace

from src.api.routers.strava_webhook import subscription_challenge, handle_event


def test_subscription_handshake_echoes_the_challenge_only_for_the_right_token():
    assert subscription_challenge("subscribe", "secret", "abc123", "secret") == {"hub.challenge": "abc123"}
    assert subscription_challenge("subscribe", "wrong", "abc123", "secret") is None
    assert subscription_challenge("unsubscribe", "secret", "abc123", "secret") is None
    assert subscription_challenge("subscribe", "", "abc123", "") is None  # unset token never validates


def _dispatch(event, known_athletes=(42,)):
    calls = []
    user = SimpleNamespace(id=7, strava_athlete_id=42)
    outcome = handle_event(
        event,
        find_user=lambda owner: user if owner in known_athletes else None,
        start_sync=lambda u: calls.append(("sync", u.id)),
        delete_activity=lambda u, activity_id: calls.append(("delete", activity_id)),
        deauthorize=lambda u: calls.append(("deauthorize", u.id)),
    )
    return outcome, calls


def test_new_or_updated_activity_starts_a_sync():
    outcome, calls = _dispatch({"object_type": "activity", "aspect_type": "create", "object_id": 1, "owner_id": 42})
    assert calls == [("sync", 7)]
    assert "sync" in outcome
    _, calls = _dispatch({"object_type": "activity", "aspect_type": "update", "object_id": 1, "owner_id": 42})
    assert calls == [("sync", 7)]


def test_deleted_activity_is_removed_from_the_warehouse():
    _, calls = _dispatch({"object_type": "activity", "aspect_type": "delete", "object_id": 99, "owner_id": 42})
    assert calls == [("delete", 99)]


def test_unknown_athletes_and_other_events_are_ignored():
    outcome, calls = _dispatch({"object_type": "activity", "aspect_type": "create", "object_id": 1, "owner_id": 1})
    assert calls == [] and outcome.startswith("ignored")
    outcome, calls = _dispatch({"object_type": "athlete", "aspect_type": "update", "owner_id": 42, "updates": {"authorized": "false"}})
    assert calls == [("deauthorize", 7)]
