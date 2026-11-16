"""A learner's personalized course, generated from one curriculum (collection user_curricula).

One per (curriculum, learner). The learner is a user account, or, from Milestone 3, a
child profile learning through their parent's session (`owner_id` is always the account
that controls the course, so access checks only ever compare `owner_id`).
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.core.clock import utcnow

LearningStyle = Literal["visual", "audio", "reading", "kinesthetic"]
LearningPace = Literal["slow", "moderate", "fast"]
MediaPreference = Literal["video", "audio", "text", "mixed"]
VideoStatus = Literal["pending", "processing", "completed", "failed"]
VideoGenerationStatus = Literal["not_started", "in_progress", "completed", "failed"]


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class Learner(BaseModel):
    type: Literal["user", "child"] = "user"
    id: PydanticObjectId


class Preferences(BaseModel):
    learning_style: LearningStyle
    learning_pace: LearningPace
    media_preference: MediaPreference
    avatar_id: str | None = None
    voice_id: str | None = None
    additional_preferences: str | None = None

    @property
    def wants_video(self) -> bool:
        return self.media_preference in ("video", "mixed")


class AvatarConfig(BaseModel):
    avatar_id: str | None = None
    voice_id: str | None = None
    is_custom_avatar: bool = False


class PersonalizedMetadata(BaseModel):
    summary: str = ""
    learning_objectives: list[str] = Field(default_factory=list)
    key_takeaways: list[str] = Field(default_factory=list)
    source_type: str = ""
    estimated_total_duration: str = ""


class Quiz(BaseModel):
    question: str
    options: list[str]  # always exactly 4
    correct_index: int  # 0-3; never sent to the client before an attempt
    explanation: str


class MicroLesson(BaseModel):
    lesson_number: int  # 1-based within its module
    lesson_title: str
    duration: str
    script: str
    quizzes: list[Quiz] = Field(default_factory=list)
    is_completed: bool = False
    completed_at: datetime | None = None
    quiz_score: int | None = None
    attempts: int = 0
    video_url: str | None = None
    video_id: str | None = None
    video_status: VideoStatus | None = None
    video_thumbnail: str | None = None
    video_duration: float | None = None


class CourseModule(BaseModel):
    module_number: int  # 1-based
    module_title: str
    summary: str = ""
    recommended_duration: str = ""
    lessons: list[MicroLesson] = Field(default_factory=list)
    progress_percentage: int = 0
    is_completed: bool = False


class UserCurriculum(Document):
    owner_id: PydanticObjectId
    learner: Learner
    curriculum_id: PydanticObjectId
    course_title: str = ""
    preferences: Preferences
    avatar_config: AvatarConfig = Field(default_factory=AvatarConfig)
    metadata: PersonalizedMetadata = Field(default_factory=PersonalizedMetadata)
    modules: list[CourseModule] = Field(default_factory=list)
    total_modules: int = 0
    overall_progress: int = 0
    is_completed: bool = False

    generation_status: GenerationStatus = GenerationStatus.QUEUED
    generation_error: str | None = None  # safe, user-facing reason when FAILED
    generation_started_at: datetime | None = None
    generation_finished_at: datetime | None = None
    video_generation_status: VideoGenerationStatus = "not_started"
    video_generation_progress: int = 0

    enrolled_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "user_curricula"
        indexes = [
            IndexModel(
                [
                    ("curriculum_id", ASCENDING),
                    ("learner.type", ASCENDING),
                    ("learner.id", ASCENDING),
                ],
                unique=True,
                name="one_course_per_learner",
            ),
            IndexModel([("owner_id", ASCENDING), ("enrolled_at", DESCENDING)], name="owner_newest"),
            IndexModel([("generation_status", ASCENDING)], name="generation_status"),
        ]
