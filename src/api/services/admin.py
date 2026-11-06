"""The admin page's numbers: who signed up each week and what they did
next, and the latest feedback notes.

activation turns one row per user into the weekly counts and the totals so
it can be tested without a database; user_facts gathers those rows from
Postgres and the warehouse, and recent_feedback lists the newest notes.
"""
from datetime import timedelta

from sqlalchemy.orm import joinedload

from src.api.database import Feedback, Race, User

WEEKS = 8
FEEDBACK_LIMIT = 50
# A user came back when they were seen this long after signing up.
SECOND_WEEK = timedelta(days=7)


def week_start(day):
    """The Monday of the week that holds day."""
    return day - timedelta(days=day.weekday())


def came_back(row):
    return bool(row["created_at"] and row["last_seen_at"] and row["last_seen_at"] - row["created_at"] >= SECOND_WEEK)


def counts(rows):
    return {
        "signed_up": len(rows),
        "connected": sum(1 for row in rows if row["connected"]),
        "added_race": sum(1 for row in rows if row["has_race"]),
        "course_file": sum(1 for row in rows if row["has_course"]),
        "returned": sum(1 for row in rows if came_back(row)),
    }


def activation(rows, now, weeks=WEEKS):
    """The sign-up cohorts of the last weeks (Monday to Sunday, oldest
    first, the current week last) and the all-time totals.

    rows has one dict per user: created_at and last_seen_at (None when not
    known), and connected, has_race and has_course. A user without
    created_at is in no week; the totals count everyone."""
    this_week = week_start(now.date())
    cohorts = []
    for ii in reversed(range(weeks)):
        start = this_week - timedelta(days=7 * ii)
        end = start + timedelta(days=7)
        members = [row for row in rows if row["created_at"] and start <= row["created_at"].date() < end]
        cohorts.append({"start": start, **counts(members)})
    totals = counts(rows)
    totals["before_tracking"] = sum(1 for row in rows if row["created_at"] is None)
    return {"weeks": cohorts, "totals": totals}


def user_facts(db, con):
    """One row per user for activation. Connected data is a Strava link, a
    COROS link, or an activity in the warehouse under the user's athlete id."""
    with_activities = {athlete_id for (athlete_id,) in con.execute("SELECT DISTINCT athlete_id FROM dim_activity").fetchall()}
    with_race = {user_id for (user_id,) in db.query(Race.user_id).distinct()}
    with_course = {user_id for (user_id,) in db.query(Race.user_id).filter(Race.course_gpx.isnot(None)).distinct()}
    return [{
        "created_at": user.created_at,
        "last_seen_at": user.last_seen_at,
        "connected": bool(user.strava_athlete_id or user.coros_linked_at) or user.athlete_id in with_activities,
        "has_race": user.id in with_race,
        "has_course": user.id in with_course,
    } for user in db.query(User).order_by(User.id).all()]


def recent_feedback(db, limit=FEEDBACK_LIMIT):
    """The newest notes, with who wrote them. created_at gets the server's
    offset so the browser shows it in local time."""
    notes = (db.query(Feedback).options(joinedload(Feedback.user))
             .order_by(Feedback.created_at.desc(), Feedback.id.desc()).limit(limit).all())
    return [{
        "created_at": note.created_at.astimezone().isoformat(),
        "user_name": note.user.name,
        "user_email": note.user.email,
        "page": note.page,
        "message": note.message,
        "contact_ok": note.contact_ok,
    } for note in notes]
