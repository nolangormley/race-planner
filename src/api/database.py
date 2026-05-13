import os
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, ForeignKey, Text, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/raceplanner")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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

    races = relationship("Race", back_populates="user", cascade="all, delete-orphan")

class Race(Base):
    __tablename__ = "races"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    name = Column(String, nullable=True) # E.g., 'Boston Marathon'
    race_length = Column(String)
    race_date = Column(Date)
    goal_time = Column(String, nullable=True)

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
    chat_messages = relationship("ChatMessage", back_populates="plan", cascade="all, delete-orphan")
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
    strava_activity_id = Column(Integer, nullable=True)  # linked if completed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("TrainingPlan", back_populates="entries")


class ChatMessage(Base):
    """AI conversation history for a training plan."""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("training_plans.id"), index=True)
    role = Column(String, nullable=False)   # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    plan = relationship("TrainingPlan", back_populates="chat_messages")


Base.metadata.create_all(bind=engine)
