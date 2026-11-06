"""Deletes a user's account: every warehouse row under their athlete id,
their Postgres rows, and the app's Strava authorization when one is stored.
"""
import logging

import requests

from src.api.auth_routes import verify_password
from src.api.database import Feedback, Race, TrainingPlan, TrainingPlanEntry, User
from src.pipeline import ACTIVITY_CHILD_TABLES, CLIENT_ID, CLIENT_SECRET, STRAVA_API_BASE

logger = logging.getLogger("race_planner.account")

REVOKE_TIMEOUT_S = 10
# Every warehouse table keyed by athlete_id, in the order their rows can go
# once the activities' child rows are gone. A new athlete-keyed table goes
# here (tests/test_account.py fails until it does).
ATHLETE_TABLES = ("dim_activity", "coros_daily", "dim_athlete")


class WrongPassword(Exception):
    """The password given does not match the account's."""


def revoke_strava(refresh_token):
    """Asks Strava to drop this app's access to the account, so the app
    leaves the user's list of authorized apps. The stored access token has
    usually expired, so the refresh token is exchanged for a fresh one first."""
    response = requests.post(f"{STRAVA_API_BASE}/oauth/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }, timeout=REVOKE_TIMEOUT_S)
    response.raise_for_status()
    requests.post(
        f"{STRAVA_API_BASE}/oauth/deauthorize", data={"access_token": response.json()["access_token"]}, timeout=REVOKE_TIMEOUT_S,
    ).raise_for_status()


def delete_warehouse_rows(con, athlete_id):
    """Removes every warehouse row under an athlete id: the activities' child
    rows first, then the activities, the COROS nights and the athlete. Each
    statement commits on its own because DuckDB checks foreign keys against
    committed rows only, so a parent row cannot go in the transaction that
    removes its children."""
    for table in ACTIVITY_CHILD_TABLES:
        con.execute(
            f"DELETE FROM {table} WHERE activity_id IN (SELECT activity_id FROM dim_activity WHERE athlete_id = ?)", [athlete_id],
        )
    for table in ATHLETE_TABLES:
        con.execute(f"DELETE FROM {table} WHERE athlete_id = ?", [athlete_id])


def delete_account(db, con, user, password, revoke=revoke_strava):
    """Deletes the user and everything stored for them, after checking the
    password the way login does: a wrong one raises WrongPassword and
    deletes nothing.

    The warehouse rows go first, so a failure there leaves the account in
    place to try again; then the training plan entries, plans, races and
    feedback notes, then the user row. A stored Strava token is revoked last through
    revoke(refresh_token), best effort: a failure is logged and the
    deletion stands."""
    if not verify_password(password, user.password):
        raise WrongPassword()
    user_id, refresh_token = user.id, user.strava_refresh_token
    delete_warehouse_rows(con, user.athlete_id)
    plan_ids = [plan.id for plan in db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id)]
    db.query(TrainingPlanEntry).filter(TrainingPlanEntry.plan_id.in_(plan_ids)).delete(synchronize_session=False)
    db.query(TrainingPlan).filter(TrainingPlan.user_id == user_id).delete(synchronize_session=False)
    db.query(Race).filter(Race.user_id == user_id).delete(synchronize_session=False)
    db.query(Feedback).filter(Feedback.user_id == user_id).delete(synchronize_session=False)
    db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    db.commit()
    if refresh_token:
        try:
            revoke(refresh_token)
        except Exception:
            logger.exception("Strava access for deleted user %s could not be revoked", user_id)
