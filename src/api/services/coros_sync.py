"""Pulls nightly wellness rows from a linked COROS account into the
warehouse (coros_daily) and keeps the user's link state. The resting
heart rate those rows give is the default for the heart-rate-reserve
model when the athlete has not typed one on their profile, so a sync
that moves it recomputes the stored metrics the way a profile change does.
"""
import logging
from datetime import datetime, timedelta

from src.coros import CorosClient, CorosAuthError, CorosError
from src.pipeline import recompute_metrics
from src.api.database import SessionLocal, User
from src.api.deps import get_db_connection
from src.api.services.metrics import resolve_resting_hr
from src.api.services.sync import registry as sync_registry, SyncAlreadyRunning

logger = logging.getLogger("race_planner.coros")

FIRST_SYNC_DAYS = 365
OVERLAP_DAYS = 3
SYNC_HOUR = 5                       # server local time; the watch uploads overnight
SYNC_EVERY = timedelta(hours=20)
EXPIRED_PREFIX = "Session expired."
RESTING_HR_STEP = 2                 # bpm change that is worth a recompute


def as_int(value):
    return None if value is None else int(round(value))


def upsert_daily(con, athlete_id, rows):
    if not rows:
        return
    con.executemany("""
        INSERT OR REPLACE INTO coros_daily
            (athlete_id, day, rhr, hrv, hrv_baseline, time_in_bed_min, training_load, vo2max, stamina, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
    """, [
        [athlete_id, row["day"], as_int(row["rhr"]), as_int(row["hrv"]), as_int(row["hrv_baseline"]), as_int(row["time_in_bed_min"]),
         as_int(row["training_load"]), row["vo2max"], as_int(row["stamina"])]
        for row in rows
    ])


def client_for(user):
    return CorosClient(user.coros_access_token, user.coros_user_id, user.coros_region or "us")


def recompute_trigger(user):
    """A callable that recomputes the warehouse with a new resting heart
    rate, like a profile change does. Returns whether a job started."""
    user_id, max_hr = user.id, user.max_hr

    def start(resting_hr):
        try:
            sync_registry.start(
                user_id,
                run_sync=lambda progress: recompute_metrics(progress=progress, resting_hr=resting_hr, max_hr=max_hr),
                on_success=lambda new_token: None,
                kind="recompute",
            )
            return True
        except SyncAlreadyRunning:
            return False
    return start


def sync_coros(db, user, client, con, now=None, on_resting_hr_change=None):
    """Fetches the nights since the last sync (a year on the first one),
    stores them and stamps the user. A failure is recorded on the user and
    re-raised. Returns a summary for the UI."""
    now = now or datetime.now()
    today = now.date()
    if user.coros_synced_at:
        start = user.coros_synced_at.date() - timedelta(days=OVERLAP_DAYS)
    else:
        start = today - timedelta(days=FIRST_SYNC_DAYS)
    before = resolve_resting_hr(con, user)
    try:
        rows = client.daily(start, today)
    except CorosError as error:
        user.coros_error = f"{EXPIRED_PREFIX} {error}" if isinstance(error, CorosAuthError) else str(error)
        db.commit()
        raise
    upsert_daily(con, user.athlete_id, rows)
    user.coros_synced_at = now
    user.coros_error = None
    db.commit()

    after = resolve_resting_hr(con, user)
    recompute_started = False
    if user.resting_hr is None and abs(after - before) >= RESTING_HR_STEP and on_resting_hr_change:
        recompute_started = bool(on_resting_hr_change(after))
    return {"rows": len(rows), "from": start.isoformat(), "to": today.isoformat(), "resting_hr": after, "recompute_started": recompute_started}


def unlink_coros(db, user, con, on_resting_hr_change=None):
    """Forgets the session and the stored nights. Recomputes when the resting
    heart rate default falls back to the plain default."""
    before = resolve_resting_hr(con, user)
    con.execute("DELETE FROM coros_daily WHERE athlete_id = ?", [user.athlete_id])
    user.coros_access_token = None
    user.coros_user_id = None
    user.coros_region = None
    user.coros_linked_at = None
    user.coros_synced_at = None
    user.coros_error = None
    db.commit()
    after = resolve_resting_hr(con, user)
    if user.resting_hr is None and after != before and on_resting_hr_change:
        on_resting_hr_change(after)


def coros_sync_due(user, now):
    """Once a day after SYNC_HOUR, for linked users whose session still works."""
    if not user.coros_access_token or now.hour < SYNC_HOUR:
        return False
    if user.coros_error and user.coros_error.startswith(EXPIRED_PREFIX):
        return False
    return user.coros_synced_at is None or now - user.coros_synced_at >= SYNC_EVERY


def sync_due_coros(now=None):
    """One scheduler pass: syncs every linked user whose nightly pull is due.
    Returns how many synced."""
    now = now or datetime.now()
    synced = 0
    db = SessionLocal()
    try:
        for user in db.query(User).filter(User.coros_access_token.isnot(None)).all():
            if not coros_sync_due(user, now):
                continue
            con = get_db_connection()
            try:
                sync_coros(db, user, client_for(user), con, now, on_resting_hr_change=recompute_trigger(user))
                synced += 1
            except CorosError as error:
                logger.warning("COROS sync for %s failed: %s", user.email, error)
            except Exception:
                db.rollback()
                logger.exception("COROS sync for %s failed", user.email)
            finally:
                con.close()
    finally:
        db.close()
    return synced
