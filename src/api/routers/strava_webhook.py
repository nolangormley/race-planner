"""Strava push subscription. Once scripts/strava_webhook.py has registered
the callback, Strava calls here when an activity is created, updated or
deleted, and the warehouse follows without anyone pressing Sync.

Strava does not sign events, only the subscription handshake carries the
verify token. An event can therefore only do two things: start a sync for
an athlete we already know (which just re-reads their Strava data) or
remove one activity from the warehouse (which the next sync puts back if it
still exists on Strava)."""
import logging
import os

from fastapi import APIRouter, HTTPException, Request

from src.api.database import SessionLocal, User
from src.api.deps import get_db_connection
from src.api.routers.activities import delete_activity_rows
from src.api.routers.sync import start_sync_for

logger = logging.getLogger("race_planner.webhook")
router = APIRouter()


def subscription_challenge(mode, token, challenge, expected_token):
    """The body to echo for Strava's one-time validation GET, or None when
    the request is not a correctly signed subscribe."""
    if mode != "subscribe" or not expected_token or token != expected_token or not challenge:
        return None
    return {"hub.challenge": challenge}


def handle_event(event, find_user, start_sync, delete_activity, deauthorize):
    """Dispatches one webhook event through the given callbacks and returns
    a short description of what happened, for the log.

    find_user(owner_id) -> user or None; start_sync(user); delete_activity(user,
    activity_id); deauthorize(user)."""
    object_type = event.get("object_type")
    aspect = event.get("aspect_type")
    user = find_user(event.get("owner_id"))
    if user is None:
        return "ignored: unknown athlete"
    if object_type == "activity":
        if aspect in ("create", "update"):
            start_sync(user)
            return f"sync queued for user {user.id}"
        if aspect == "delete":
            delete_activity(user, event.get("object_id"))
            return f"deleted activity {event.get('object_id')} for user {user.id}"
    if object_type == "athlete" and (event.get("updates") or {}).get("authorized") == "false":
        deauthorize(user)
        return f"user {user.id} revoked access"
    return "ignored"


def find_user_by_athlete(owner_id):
    if owner_id is None:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.strava_athlete_id == int(owner_id)).first()
        if user is None or not user.strava_refresh_token:
            return None
        db.expunge(user)
        return user
    finally:
        db.close()


def delete_for_user(user, activity_id):
    con = get_db_connection()
    try:
        owned = con.execute(
            "SELECT 1 FROM dim_activity WHERE activity_id = ? AND athlete_id = ?", [int(activity_id), user.strava_athlete_id]
        ).fetchone()
        if owned:
            delete_activity_rows(con, int(activity_id))
    finally:
        con.close()


def deauthorize(user):
    db = SessionLocal()
    try:
        stored = db.query(User).filter(User.id == user.id).first()
        stored.strava_refresh_token = None
        stored.strava_access_token = None
        db.commit()
    finally:
        db.close()


@router.get("/api/strava/webhook")
def strava_webhook_validation(request: Request):
    params = request.query_params
    body = subscription_challenge(
        params.get("hub.mode"), params.get("hub.verify_token"), params.get("hub.challenge"),
        os.getenv("STRAVA_WEBHOOK_VERIFY_TOKEN", ""),
    )
    if body is None:
        raise HTTPException(status_code=403, detail="Verify token does not match")
    return body


@router.post("/api/strava/webhook")
async def strava_webhook_event(request: Request):
    # Strava expects a 200 within two seconds; the sync itself runs in a thread.
    try:
        event = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Expected a JSON event")
    outcome = handle_event(
        event, find_user_by_athlete,
        lambda user: start_sync_for(user, queue_if_running=True),
        delete_for_user, deauthorize,
    )
    logger.info("Strava webhook %s %s %s: %s", event.get("object_type"), event.get("aspect_type"), event.get("object_id"), outcome)
    return {"ok": True}
