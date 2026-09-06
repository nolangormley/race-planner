"""Pydantic request/response models for the API.

Extracted from main.py, where these were previously scattered across the
file interleaved with unrelated route handlers (see
agent_prompting/improvement_plan.md Section 6).
"""
from typing import Optional, List
from pydantic import BaseModel


class WorkoutCreate(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = "Manual"


class SettingsUpdate(BaseModel):
    race_length: str
    race_date: str
    race_goal_time: str


class RaceCreate(BaseModel):
    name: Optional[str] = None
    race_length: str
    race_date: str
    goal_time: Optional[str] = None


class ProfileUpdate(BaseModel):
    name: str
    email: str
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    height: Optional[float] = None  # stored in meters
    weight: Optional[float] = None  # stored in kg


class PlanEntryCreate(BaseModel):
    date: str
    workout_type: Optional[str] = None
    training_focus: Optional[str] = None
    approximate_distance: Optional[str] = None
    description: Optional[str] = None
    is_completed: Optional[bool] = False
    strava_activity_id: Optional[int] = None


class PlanEntryUpdate(BaseModel):
    workout_type: Optional[str] = None
    training_focus: Optional[str] = None
    approximate_distance: Optional[str] = None
    description: Optional[str] = None
    is_completed: Optional[bool] = None
    strava_activity_id: Optional[int] = None


class BulkEntriesCreate(BaseModel):
    entries: List[PlanEntryCreate]
    name: Optional[str] = None
    race_id: Optional[int] = None


class ChatMessageCreate(BaseModel):
    message: str
