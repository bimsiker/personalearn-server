"""Personalization and course responses. Mirror the frontend's types in
src/types/curriculum.ts (PersonalizationPreferences, UserCurriculum, CourseModule,
MicroLesson, Quiz).

Quizzes are sent WITHOUT the correct answer or explanation: answers are checked on the
server (POST /curriculum/{ucId}/attempt, Phase 5), which then reveals the explanation.
"""

from datetime import datetime

from pydantic import ConfigDict, Field, field_validator

from app.models.user_curriculum import (
    CourseModule,
    GenerationStatus,
    LearningPace,
    LearningStyle,
    MediaPreference,
    MicroLesson,
    Preferences,
    UserCurriculum,
    VideoGenerationStatus,
    VideoStatus,
)
from app.schemas.common import CamelModel


class PreferencesIn(CamelModel):
    """Body of POST /curriculum/{id}/enroll: the answers from the personalization form."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "learningStyle": "visual",
                "learningPace": "moderate",
                "mediaPreference": "text",
                "additionalPreferences": "I like real-world examples.",
            }
        }
    )

    learning_style: LearningStyle
    learning_pace: LearningPace
    media_preference: MediaPreference
    avatar_id: str | None = Field(default=None, max_length=100)
    voice_id: str | None = Field(default=None, max_length=100)
    additional_preferences: str | None = Field(default=None, max_length=500)

    @field_validator("avatar_id", "voice_id", "additional_preferences")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None

    def to_model(self) -> Preferences:
        return Preferences(**self.model_dump())


class PreferencesOut(CamelModel):
    learning_style: LearningStyle
    learning_pace: LearningPace
    media_preference: MediaPreference
    avatar_id: str | None = None
    voice_id: str | None = None
    additional_preferences: str | None = None


class AvatarConfigOut(CamelModel):
    avatar_id: str | None = None
    voice_id: str | None = None
    is_custom_avatar: bool = False


class PersonalizedMetadataOut(CamelModel):
    summary: str
    learning_objectives: list[str]
    key_takeaways: list[str]
    source_type: str
    estimated_total_duration: str


class QuizOut(CamelModel):
    question: str
    options: list[str] = Field(description="Always 4 options; answer by index (0-3)")


class MicroLessonOut(CamelModel):
    module_title: str
    module_number: int
    lesson_number: int
    lesson_title: str
    duration: str
    script: str
    quizzes: list[QuizOut]
    quiz_count: int
    is_completed: bool
    quiz_score: int | None = None
    attempts: int
    video_url: str | None = None
    video_id: str | None = None
    video_status: VideoStatus | None = None
    video_thumbnail: str | None = None
    video_duration: float | None = None

    @classmethod
    def build(cls, module: CourseModule, lesson: MicroLesson) -> "MicroLessonOut":
        return cls(
            module_title=module.module_title,
            module_number=module.module_number,
            lesson_number=lesson.lesson_number,
            lesson_title=lesson.lesson_title,
            duration=lesson.duration,
            script=lesson.script,
            quizzes=[QuizOut(question=q.question, options=q.options) for q in lesson.quizzes],
            quiz_count=len(lesson.quizzes),
            is_completed=lesson.is_completed,
            quiz_score=lesson.quiz_score,
            attempts=lesson.attempts,
            video_url=lesson.video_url,
            video_id=lesson.video_id,
            video_status=lesson.video_status,
            video_thumbnail=lesson.video_thumbnail,
            video_duration=lesson.video_duration,
        )


class CourseModuleOut(CamelModel):
    module_title: str
    module_number: int
    summary: str
    recommended_duration: str
    lessons: list[MicroLessonOut]
    progress_percentage: int
    is_completed: bool

    @classmethod
    def build(cls, module: CourseModule) -> "CourseModuleOut":
        return cls(
            module_title=module.module_title,
            module_number=module.module_number,
            summary=module.summary,
            recommended_duration=module.recommended_duration,
            lessons=[MicroLessonOut.build(module, lesson) for lesson in module.lessons],
            progress_percentage=module.progress_percentage,
            is_completed=module.is_completed,
        )


class UserCurriculumOut(CamelModel):
    """A personalized course. Poll it until `generationStatus` is "ready" or "failed"."""

    id: str = Field(alias="_id")
    user_id: str
    child_id: str | None = Field(default=None, description="Set for child-mode courses")
    curriculum_id: str
    course_title: str
    preferences: PreferencesOut
    avatar_config: AvatarConfigOut
    metadata: PersonalizedMetadataOut
    modules: list[CourseModuleOut]
    total_modules: int
    overall_progress: int
    is_completed: bool
    enrolled_at: datetime
    generation_status: GenerationStatus = Field(
        description="queued -> generating -> ready | failed"
    )
    generation_error: str | None = Field(
        default=None, description="Why generation failed; enroll again to retry"
    )
    video_generation_status: VideoGenerationStatus
    video_generation_progress: int

    @classmethod
    def from_doc(cls, doc: UserCurriculum) -> "UserCurriculumOut":
        return cls(
            id=str(doc.id),
            user_id=str(doc.owner_id),
            child_id=str(doc.learner.id) if doc.learner.type == "child" else None,
            curriculum_id=str(doc.curriculum_id),
            course_title=doc.course_title,
            preferences=PreferencesOut(**doc.preferences.model_dump()),
            avatar_config=AvatarConfigOut(**doc.avatar_config.model_dump()),
            metadata=PersonalizedMetadataOut(**doc.metadata.model_dump()),
            modules=[CourseModuleOut.build(module) for module in doc.modules],
            total_modules=doc.total_modules,
            overall_progress=doc.overall_progress,
            is_completed=doc.is_completed,
            enrolled_at=doc.enrolled_at,
            generation_status=doc.generation_status,
            generation_error=doc.generation_error,
            video_generation_status=doc.video_generation_status,
            video_generation_progress=doc.video_generation_progress,
        )


class LessonRef(CamelModel):
    module_number: int
    lesson_number: int


class LessonResponse(CamelModel):
    """One lesson, plus where "Previous" / "Next" buttons should go."""

    user_curriculum_id: str
    curriculum_id: str
    course_title: str
    lesson: MicroLessonOut
    previous: LessonRef | None
    next: LessonRef | None
