import os
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Date, Float, ForeignKey, Text, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/raceplanner")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Warehouse rows are keyed by Strava athlete id. A user who only imports
# files gets a key above every real Strava id; the Strava callback moves
# their rows to the real id when they connect later.
SYNTHETIC_ATHLETE_BASE = 1_000_000_000_000


def synthetic_athlete_id(user_id):
    return SYNTHETIC_ATHLETE_BASE + user_id


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password = Column(String)
    name = Column(String)
    date_of_birth = Column(Date)
    gender = Column(String)
    height = Column(Float)
    weight = Column(Float)
    
    # Race details
    race_length = Column(String, nullable=True)
    race_date = Column(Date, nullable=True)
    race_goal_time = Column(String, nullable=True)
    
    # Strava auth
    strava_access_token = Column(String, nullable=True)
    strava_refresh_token = Column(String, nullable=True)
    strava_athlete_id = Column(Integer, nullable=True)
    strava_last_synced_at = Column(DateTime, nullable=True)

    @property
    def athlete_id(self):
        """The warehouse key: the Strava athlete id, or the synthetic id."""
        return self.strava_athlete_id or synthetic_athlete_id(self.id)

    # Heart rate profile. Null means "use the default resting rate" and "use
    # the highest heart rate seen in the warehouse".
    resting_hr = Column(Integer, nullable=True)
    max_hr = Column(Integer, nullable=True)

    # Monday morning summary email (see services/digest.py).
    weekly_digest = Column(Boolean, nullable=False, default=False, server_default="false")
    digest_sent_at = Column(DateTime, nullable=True)

    # COROS Training Hub link (see services/coros_sync.py). Only the session
    # token is kept, never the password.
    coros_user_id = Column(String, nullable=True)
    coros_access_token = Column(String, nullable=True)
    coros_region = Column(String(8), nullable=True)
    coros_linked_at = Column(DateTime, nullable=True)
    coros_synced_at = Column(DateTime, nullable=True)
    coros_error = Column(String, nullable=True)

    races = relationship("Race", back_populates="user", cascade="all, delete-orphan")

class Race(Base):
    __tablename__ = "races"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    name = Column(String, nullable=True) # E.g., 'Boston Marathon'
    race_length = Column(String)
    race_date = Column(Date)
    goal_time = Column(String, nullable=True)
    course_gpx = Column(Text, nullable=True)  # the course file behind the pacing plan

    user = relationship("User", back_populates="races")


class TrainingPlan(Base):
    """One active training plan per user at a time."""
    __tablename__ = "training_plans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    name = Column(String, nullable=True)  # e.g. "Boston 2026 Plan"
    race_id = Column(Integer, ForeignKey("races.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entries = relationship("TrainingPlanEntry", back_populates="plan", cascade="all, delete-orphan")
    user = relationship("User")


class TrainingPlanEntry(Base):
    """A single day/workout in a training plan."""
    __tablename__ = "training_plan_entries"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("training_plans.id"), index=True)
    date = Column(Date, nullable=False)
    workout_type = Column(String, nullable=True)    # Easy Run, Tempo, Rest, etc.
    training_focus = Column(String, nullable=True)  # e.g. "Aerobic Base"
    approximate_distance = Column(String, nullable=True)  # e.g. "5 miles"
    description = Column(Text, nullable=True)
    is_completed = Column(Boolean, default=False)
    strava_activity_id = Column(BigInteger, nullable=True)  # linked if completed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("TrainingPlan", back_populates="entries")


# Schema is managed by Alembic (see /alembic and alembic.ini at the repo
# root) — run `alembic upgrade head` to create/update tables. This used to
# call Base.metadata.create_all(bind=engine) here at import time, which only
# ever creates missing tables and never alters existing ones; the first
# schema change after a table already existed in a deployed DB would fail at
# runtime with no migration path. See
# agent_prompting/improvement_plan.md Section 2/6.
