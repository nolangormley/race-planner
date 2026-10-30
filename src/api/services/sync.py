"""Background Strava syncs that the browser can watch.

POST /api/sync-strava/{user_id} starts one job per user in a thread and returns
at once. GET on the same path reads the job's snapshot, which the sync progress
bar polls until the job is done. src.pipeline.sync_athlete reports its phases
through the progress callback each job hands it; this module turns those calls
into one percentage and one line of text.
"""
import logging
import threading
from datetime import datetime, timezone

from src.pipeline import StravaAuthError, recompute_metrics, sync_athlete
from src.api.database import SessionLocal, User
from src.api.services.metrics import resolve_resting_hr

logger = logging.getLogger("race_planner.sync")

# Each phase owns a slice of the 0-100 bar. The streams phase gets the most
# because it makes one Strava request per new activity.
PHASE_SPAN = {
    "auth": (0, 3),
    "listing": (3, 10),
    "streams": (10, 58),
    "laps": (58, 66),
    "structure": (66, 70),
    "metrics": (70, 95),
    "load": (95, 100),
}
# A recompute (heart rate profile changed) never talks to Strava.
RECOMPUTE_SPAN = {
    "metrics": (0, 95),
    "load": (95, 100),
}

PHASE_LABEL = {
    "auth": "Refreshing Strava access",
    "listing": "Fetching your activity list",
    "streams": "Downloading activity streams",
    "laps": "Fetching lap splits",
    "structure": "Finding intervals and tempo blocks",
    "metrics": "Computing training metrics",
    "load": "Calculating training load",
    "done": "Sync complete",
    "error": "Sync failed",
}


class SyncAlreadyRunning(Exception):
    pass


class SyncJob:
    def __init__(self, user_id, kind="sync"):
        self.user_id = user_id
        self.kind = kind
        self.status = "running"
        self.phase = "auth" if kind == "sync" else "metrics"
        self.done = 0
        self.total = 0
        self.detail = ""
        self.new_activities = None
        self.error = None
        self.error_kind = None
        self.started_at = datetime.now(timezone.utc)
        self.finished_at = None
        self.lock = threading.Lock()

    def update(self, phase, done=0, total=0, name="", page=0, found=0):
        """The progress callback that sync_athlete calls."""
        with self.lock:
            self.phase = phase
            self.done = done
            self.total = total
            if phase == "listing":
                self.detail = f"page {page}, {found} activities found"
            else:
                self.detail = name

    def finish(self, new_activities):
        with self.lock:
            self.status = "done"
            self.phase = "done"
            self.new_activities = new_activities
            self.detail = ""
            self.finished_at = datetime.now(timezone.utc)

    def fail(self, error, kind="error"):
        with self.lock:
            self.status = "error"
            self.phase = "error"
            self.error = error
            self.error_kind = kind
            self.finished_at = datetime.now(timezone.utc)

    def percent(self):
        if self.status == "done":
            return 100
        spans = RECOMPUTE_SPAN if self.kind == "recompute" else PHASE_SPAN
        start, end = spans.get(self.phase, (0, 0))
        if self.total:
            return round(start + (end - start) * min(self.done, self.total) / self.total)
        return start

    def snapshot(self):
        with self.lock:
            label = PHASE_LABEL[self.phase]
            if self.phase == "done" and self.kind == "recompute":
                label = "Metrics updated"
            return {
                "status": self.status,
                "kind": self.kind,
                "phase": self.phase,
                "label": label,
                "detail": self.detail,
                "done": self.done,
                "total": self.total,
                "percent": self.percent(),
                "new_activities": self.new_activities,
                "error": self.error,
                "error_kind": self.error_kind,
                "started_at": self.started_at.isoformat(),
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            }


class SyncRegistry:
    """One live job per user. A finished job stays until the next one starts,
    so a page that loads right after a sync can still show its result."""

    def __init__(self):
        self.jobs = {}
        self.queued = {}
        self.lock = threading.Lock()

    def get(self, user_id):
        return self.jobs.get(user_id)

    def snapshot(self, user_id):
        """The current job's snapshot, or None. Read under the lock run()
        holds while it ends a job and starts the queued one, so a poll never
        sees the first job done before the queued one exists: the page would
        stop polling and miss the queued job."""
        with self.lock:
            job = self.jobs.get(user_id)
            return job.snapshot() if job else None

    def start_or_queue(self, user_id, run_sync, on_success, kind="sync"):
        """Like start, but a job already running queues one more run for when
        it finishes instead of raising. A Strava webhook arriving mid-sync
        must not be lost: the running sync may have listed activities before
        the new one existed. The check and the queueing share one critical
        section with the end of a job (see run), so a run is never queued
        behind a job that has already finished."""
        with self.lock:
            current = self.jobs.get(user_id)
            if current and current.status == "running":
                queued = self.queued.get(user_id)
                # A queued sync reads the heart rate profile when it starts and
                # recomputes whatever changed, so it already covers a recompute.
                if not (queued and queued[2] == "sync" and kind == "recompute"):
                    self.queued[user_id] = (run_sync, on_success, kind)
                return current
            job = self.register(user_id, kind)
        self.launch(job, run_sync, on_success)
        return job

    def start(self, user_id, run_sync, on_success, kind="sync"):
        """run_sync(progress) does the sync and returns (new_refresh_token, count).
        on_success(new_refresh_token) runs in the worker thread when run_sync
        returns, before the job is marked done. kind is 'sync' (Strava) or
        'recompute' (metrics only)."""
        with self.lock:
            current = self.jobs.get(user_id)
            if current and current.status == "running":
                raise SyncAlreadyRunning()
            job = self.register(user_id, kind)
        self.launch(job, run_sync, on_success)
        return job

    def register(self, user_id, kind):
        """A new job as the user's current one. The caller holds self.lock."""
        job = SyncJob(user_id, kind=kind)
        self.jobs[user_id] = job
        return job

    def launch(self, job, run_sync, on_success):
        worker = threading.Thread(
            target=self.run, args=(job, run_sync, on_success), daemon=True, name=f"strava-sync-{job.user_id}"
        )
        worker.start()

    def run(self, job, run_sync, on_success):
        count, error, error_kind = 0, None, "error"
        try:
            new_refresh_token, count = run_sync(job.update)
            on_success(new_refresh_token)
        except StravaAuthError as exc:
            error, error_kind = str(exc), "auth"
        except Exception as exc:
            logger.exception("Strava sync failed for user %s", job.user_id)
            error = str(exc)
        with self.lock:
            if error is None:
                job.finish(count)
            else:
                job.fail(error, kind=error_kind)
            queued = self.queued.pop(job.user_id, None)
            next_job = self.register(job.user_id, queued[2]) if queued else None
        if next_job:
            self.launch(next_job, queued[0], queued[1])


registry = SyncRegistry()


def stored_profile(user_id):
    """(refresh token, warehouse athlete id, resting HR, max HR) as stored now."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        return user.strava_refresh_token, user.athlete_id, resolve_resting_hr(user), user.max_hr
    finally:
        db.close()


def sync_job(user_id):
    """run_sync for a Strava sync. It reads the token and the heart rate
    profile when it starts, so a queued sync uses the values of that moment."""
    def run(progress):
        refresh_token, athlete_id, resting_hr, max_hr = stored_profile(user_id)
        return sync_athlete(refresh_token, athlete_id, progress=progress, resting_hr=resting_hr, max_hr=max_hr)
    return run


def recompute_job(user_id):
    """run_sync for a metrics recompute; it reads the profile when it starts."""
    def run(progress):
        _, athlete_id, resting_hr, max_hr = stored_profile(user_id)
        return recompute_metrics(athlete_id, progress=progress, resting_hr=resting_hr, max_hr=max_hr)
    return run


def queue_recompute(user_id):
    """Recomputes the user's metrics now, or once the running job ends, so a
    heart rate change saved during a sync is not lost."""
    return registry.start_or_queue(user_id, recompute_job(user_id), lambda new_token: None, kind="recompute")
