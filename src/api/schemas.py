"""Pydantic request/response models for the API.

Extracted from main.py, where these were previously scattered across the
file interleaved with unrelated route handlers (see
agent_prompting/improvement_plan.md Section 6).
"""
from typing import Optional, List
from pydantic import BaseModel, Field


class WorkoutCreate(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = "Manual"


class SettingsUpdate(BaseModel):
    # race_length/race_goal_time flow into get_ai_insight's system prompt
    # (via User.race_length/race_goal_time) with no length limit at all
    # previously — length-capped per
    # agent_prompting/improvement_plan.md Section 3 ("length-cap and
    # validate free-text fields before they reach a prompt").
    race_length: str = Field(max_length=50)
    race_date: str
    race_goal_time: str = Field(max_length=20)


class RaceCreate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    race_length: str = Field(max_length=50)
    race_date: str
    goal_time: Optional[str] = Field(default=None, max_length=20)


class ProfileUpdate(BaseModel):
    name: str = Field(max_length=100)
    email: str = Field(max_length=254)
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    height: Optional[float] = None  # stored in meters
    weight: Optional[float] = None  # stored in kg


class PlanEntryCreate(BaseModel):
    date: str
    workout_type: Optional[str] = Field(default=None, max_length=200)
    training_focus: Optional[str] = Field(default=None, max_length=200)
    approximate_distance: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    is_completed: Optional[bool] = False
    strava_activity_id: Optional[int] = None


class PlanEntryUpdate(BaseModel):
    workout_type: Optional[str] = Field(default=None, max_length=200)
    training_focus: Optional[str] = Field(default=None, max_length=200)
    approximate_distance: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    is_completed: Optional[bool] = None
    strava_activity_id: Optional[int] = None


class BulkEntriesCreate(BaseModel):
    entries: List[PlanEntryCreate]
    name: Optional[str] = None
    race_id: Optional[int] = None
