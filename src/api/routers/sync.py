"""Start a background Strava sync and read its progress."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime
import logging

from src.pipeline import sync_athlete
from src.api.auth_routes import get_current_user
from src.api.database import SessionLocal, User
from src.api.services.sync import registry as sync_registry, SyncAlreadyRunning

logger = logging.getLogger("race_planner.api")
router = APIRouter()


def record_sync_result(user_id, new_refresh_token):
    """Runs in the sync worker thread once sync_athlete returns."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if new_refresh_token:
            user.strava_refresh_token = new_refresh_token
        user.strava_last_synced_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def sync_state(user_id, last_synced_at):
    job = sync_registry.get(user_id)
    return {
        "job": job.snapshot() if job else None,
        "last_synced_at": last_synced_at.isoformat() + "Z" if last_synced_at else None,
    }


@router.post("/api/sync-strava/{user_id}", status_code=202)
def start_strava_sync(user_id: int, current_user: User = Depends(get_current_user)):
    """Starts a background Strava sync for the logged-in user. The sync runs
    in a thread (see src.api.services.sync) and reports its progress through
    GET on the same path, which the UI polls to drive its progress bar.

    DuckDB allows one read-write connection per file, but every connection
    opened in this process shares one database instance, so dashboard reads
    keep working while the sync writes.
    """
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to sync Strava data for this user")
    if not current_user.strava_refresh_token:
        raise HTTPException(status_code=400, detail="Strava isn't connected for this account yet — visit /strava-auth first.")

    refresh_token = current_user.strava_refresh_token
    try:
        sync_registry.start(
            user_id,
            run_sync=lambda progress: sync_athlete(refresh_token, progress=progress),
            on_success=lambda new_token: record_sync_result(user_id, new_token),
        )
    except SyncAlreadyRunning:
        raise HTTPException(status_code=409, detail="A sync is already running for this account.")
    return sync_state(user_id, current_user.strava_last_synced_at)


@router.get("/api/sync-strava/{user_id}")
def strava_sync_status(user_id: int, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this user's sync status")
    return sync_state(user_id, current_user.strava_last_synced_at)
