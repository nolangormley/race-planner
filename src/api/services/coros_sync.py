"""Pulls nightly wellness rows from a linked COROS account into the
warehouse (coros_daily) and keeps the user's link state. The resting
heart rate those rows give is the default for the heart-rate-reserve
model when the athlete has not typed one on their profile, so a sync
that moves it recomputes the stored metrics the way a profile change does.
"""
import logging
from datetime import datetime, timedelta

from src.coros import CorosClient, CorosAuthError, CorosError
from src.api.database import SessionLocal, User
from src.api.deps import get_db_connection
from src.api.services.metrics import coros_resting_hr, resolve_resting_hr
from src.api.services.sync import queue_recompute

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


def sync_coros(db, user, client, con, now=None, recompute=None):
    """Fetches the nights since the last sync (a year on the first one),
    stores them and stamps the user. A failure is recorded on the user and
    re-raised. When the 14-night median resting heart rate moves by
    RESTING_HR_STEP or more it becomes the user's coros_resting_hr, and if the
    profile has no typed resting heart rate, recompute() is called so the
    stored metrics follow. Returns a summary for the UI."""
    now = now or datetime.now()
    today = now.date()
    if user.coros_synced_at:
        start = user.coros_synced_at.date() - timedelta(days=OVERLAP_DAYS)
    else:
        start = today - timedelta(days=FIRST_SYNC_DAYS)
    try:
        rows = client.daily(start, today)
    except CorosError as error:
        user.coros_error = f"{EXPIRED_PREFIX} {error}" if isinstance(error, CorosAuthError) else str(error)
        db.commit()
        raise
    upsert_daily(con, user.athlete_id, rows)
    previous = user.coros_resting_hr
    median = coros_resting_hr(con, user.athlete_id)
    if median and (previous is None or abs(median - previous) >= RESTING_HR_STEP):
        user.coros_resting_hr = median
    user.coros_synced_at = now
    user.coros_error = None
    db.commit()

    recompute_started = user.resting_hr is None and user.coros_resting_hr != previous and recompute is not None
    if recompute_started:
        recompute()
    return {"rows": len(rows), "from": start.isoformat(), "to": today.isoformat(), "resting_hr": resolve_resting_hr(user), "recompute_started": recompute_started}


def unlink_coros(db, user, con, recompute=None):
    """Forgets the session and the stored nights. Calls recompute() when the
    metrics used the COROS resting heart rate, which falls back to the default."""
    previous = user.coros_resting_hr
    con.execute("DELETE FROM coros_daily WHERE athlete_id = ?", [user.athlete_id])
    user.coros_access_token = None
    user.coros_user_id = None
    user.coros_region = None
    user.coros_linked_at = None
    user.coros_synced_at = None
    user.coros_error = None
    user.coros_resting_hr = None
    db.commit()
    if user.resting_hr is None and previous is not None and recompute is not None:
        recompute()


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
                sync_coros(db, user, client_for(user), con, now, recompute=lambda user_id=user.id: queue_recompute(user_id))
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
