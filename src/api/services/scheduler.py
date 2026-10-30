"""The in-process scheduler: a daemon thread that runs each registered job
every interval. Jobs decide for themselves whether anything is due (see
digest.send_due_digests and coros_sync.sync_due_coros), so a restart never
sends or fetches anything by itself: the first pass is one interval in.
"""
import logging
import threading
import time

logger = logging.getLogger("race_planner.scheduler")

CHECK_INTERVAL_S = 1800


def run_jobs(jobs):
    for job in jobs:
        try:
            result = job()
            if result:
                logger.info("%s: %s", job.__name__, result)
        except Exception:
            logger.exception("Scheduled job %s failed", job.__name__)


def start_scheduler(jobs, interval_s=CHECK_INTERVAL_S):
    def loop():
        while True:
            time.sleep(interval_s)
            run_jobs(jobs)

    thread = threading.Thread(target=loop, name="scheduler", daemon=True)
    thread.start()
    return thread
