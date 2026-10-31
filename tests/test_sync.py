import threading

import pytest

from src.api.services.sync import SyncJob, SyncRegistry, SyncAlreadyRunning
from src.pipeline import StravaAuthError


# ---- SyncJob: progress -> percent + message ----

def test_percent_follows_phase_spans():
    job = SyncJob(user_id=1)
    assert job.percent() == 0

    job.update("listing", page=2, found=80)
    assert job.percent() == 3
    assert job.snapshot()["detail"] == "page 2, 80 activities found"

    job.update("streams", done=15, total=30, name="Morning Run")
    assert job.percent() == 34  # halfway through the 10-58 span
    assert job.snapshot()["detail"] == "Morning Run"

    job.update("metrics", done=0, total=0)
    assert job.percent() == 70

    job.finish(new_activities=30)
    snap = job.snapshot()
    assert snap["percent"] == 100
    assert snap["status"] == "done"
    assert snap["new_activities"] == 30
    assert snap["finished_at"] is not None


def test_percent_never_exceeds_phase_end_when_done_overshoots_total():
    job = SyncJob(user_id=1)
    job.update("streams", done=12, total=10)
    assert job.percent() == 58


def test_fail_records_error_kind():
    job = SyncJob(user_id=1)
    job.fail("token revoked", kind="auth")
    snap = job.snapshot()
    assert snap["status"] == "error"
    assert snap["label"] == "Sync failed"
    assert snap["error_kind"] == "auth"
    assert snap["percent"] == 0


# ---- SyncRegistry: one job per user, runs in a thread ----

def test_registry_runs_sync_and_persists_token():
    registry = SyncRegistry()
    finished = threading.Event()
    persisted = []

    def run_sync(progress):
        progress("streams", done=1, total=1, name="x")
        return "new-token", 1

    def on_success(token):
        persisted.append(token)
        finished.set()

    job = registry.start(7, run_sync, on_success)
    assert finished.wait(timeout=5)
    # finish() runs right after on_success; give the worker a moment to get there.
    for _ in range(50):
        if job.status == "done":
            break
        threading.Event().wait(0.01)

    assert persisted == ["new-token"]
    assert job.snapshot()["status"] == "done"
    assert job.snapshot()["new_activities"] == 1
    assert registry.get(7) is job


def test_registry_rejects_second_job_while_running():
    registry = SyncRegistry()
    release = threading.Event()

    def run_sync(progress):
        release.wait(timeout=5)
        return None, 0

    registry.start(7, run_sync, lambda token: None)
    with pytest.raises(SyncAlreadyRunning):
        registry.start(7, run_sync, lambda token: None)
    # A different user is not blocked.
    registry.start(8, run_sync, lambda token: None)
    release.set()


def test_registry_marks_auth_failures_so_the_ui_can_offer_reconnect():
    registry = SyncRegistry()

    def run_sync(progress):
        raise StravaAuthError("refresh token revoked")

    job = registry.start(7, run_sync, lambda token: None)
    for _ in range(100):
        if job.status == "error":
            break
        threading.Event().wait(0.01)

    snap = job.snapshot()
    assert snap["status"] == "error"
    assert snap["error_kind"] == "auth"
    assert "revoked" in snap["error"]


def test_registry_queues_one_more_run_when_a_job_is_already_running():
    registry = SyncRegistry()
    release = threading.Event()
    runs = []

    def run_sync(progress):
        runs.append(1)
        release.wait(timeout=5)
        return None, 0

    first = registry.start_or_queue(7, run_sync, lambda token: None)
    queued = registry.start_or_queue(7, run_sync, lambda token: None)
    assert queued is first                      # no second job yet
    release.set()
    for _ in range(200):
        if len(runs) == 2 and registry.get(7).status in ("done", "error") and registry.get(7) is not first:
            break
        threading.Event().wait(0.01)
    assert len(runs) == 2                       # the queued run happened after the first finished
    assert registry.get(7) is not first


def test_a_queued_sync_covers_a_later_recompute_but_not_the_other_way_round():
    registry = SyncRegistry()
    release = threading.Event()
    kinds = []

    def blocking(progress):
        release.wait(timeout=5)
        return None, 0

    def recorder(kind):
        def run(progress):
            kinds.append(kind)
            return None, 0
        return run

    registry.start(7, blocking, lambda token: None)
    registry.start_or_queue(7, recorder("recompute"), lambda token: None, kind="recompute")
    registry.start_or_queue(7, recorder("sync"), lambda token: None, kind="sync")         # replaces the recompute
    registry.start_or_queue(7, recorder("recompute"), lambda token: None, kind="recompute")   # the sync covers it
    assert registry.queued[7][2] == "sync"
    release.set()
    for _ in range(200):
        if kinds:
            break
        threading.Event().wait(0.01)
    assert kinds == ["sync"]



def test_a_poll_never_sees_the_first_job_done_while_a_queued_one_waits():
    registry = SyncRegistry()
    release_first, release_second, second_started = threading.Event(), threading.Event(), threading.Event()

    def first(progress):
        release_first.wait(timeout=5)
        return None, 0

    def second(progress):
        second_started.set()
        release_second.wait(timeout=5)
        return None, 0

    registry.start(7, first, lambda token: None)
    registry.start_or_queue(7, second, lambda token: None, kind="recompute")
    seen, stop = set(), threading.Event()

    def poll():
        while not stop.is_set():
            snapshot = registry.snapshot(7)
            seen.add((snapshot["kind"], snapshot["status"]))

    poller = threading.Thread(target=poll)
    poller.start()
    release_first.set()
    assert second_started.wait(timeout=5)
    # The recompute stays running until release_second, so give the poller
    # time to be scheduled and see it before stopping.
    for _ in range(500):
        if ("recompute", "running") in seen:
            break
        threading.Event().wait(0.01)
    stop.set()
    poller.join()
    release_second.set()
    assert ("sync", "done") not in seen          # the page keeps polling into the recompute
    assert ("recompute", "running") in seen
