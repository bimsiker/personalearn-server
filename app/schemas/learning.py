"""Personalization and course responses. Mirror the frontend's types in
src/types/curriculum.ts (PersonalizationPreferences, UserCurriculum, CourseModule,
MicroLesson, Quiz).

Quizzes are sent WITHOUT the correct answer or explanation until they are revealed
(answered correctly, or after QUIZ_REVEAL_AFTER_ATTEMPTS wrong tries). Answers are
checked on the server: POST /curriculum/{ucId}/attempt.
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
    Quiz,
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
    attempts: int
    passed: bool = Field(description="Answered correctly at least once")
    last_selected_index: int | None = None
    revealed: bool = Field(description="True once passed or after 3 wrong tries")
    correct_index: int | None = Field(default=None, description="Only set once revealed")
    explanation: str | None = Field(default=None, description="Only set once revealed")

    @classmethod
    def build(cls, quiz: Quiz) -> "QuizOut":
        return cls(
            question=quiz.question,
            options=quiz.options,
            attempts=quiz.attempts,
            passed=quiz.passed,
            last_selected_index=quiz.last_selected_index,
            revealed=quiz.revealed,
            correct_index=quiz.correct_index if quiz.revealed else None,
            explanation=quiz.explanation if quiz.revealed else None,
        )


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
            quizzes=[QuizOut.build(quiz) for quiz in lesson.quizzes],
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


# ============================================================ quizzes and progress (Phase 5)


class QuizPosition(CamelModel):
    module_number: int = Field(ge=1, le=100)
    lesson_number: int = Field(ge=1, le=100)
    quiz_index: int = Field(ge=0, le=20, description="0-based position of the question")


class AttemptRequest(QuizPosition):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"moduleNumber": 1, "lessonNumber": 1, "quizIndex": 0, "selectedIndex": 2}
        }
    )

    selected_index: int = Field(ge=0, le=3, description="The chosen option (0-3)")


class ExplainRequest(QuizPosition):
    model_config = ConfigDict(
        json_schema_extra={"example": {"moduleNumber": 1, "lessonNumber": 1, "quizIndex": 0}}
    )

    selected_index: int | None = Field(
        default=None, ge=0, le=3, description="Defaults to your last answer to this question"
    )


class LessonProgressOut(CamelModel):
    module_number: int
    lesson_number: int
    is_completed: bool
    quiz_score: int | None = Field(description="% right on the first try; set once all tried")
    attempts: int
    quizzes_passed: int
    quiz_count: int

    @classmethod
    def build(cls, module: CourseModule, lesson: MicroLesson) -> "LessonProgressOut":
        return cls(
            module_number=module.module_number,
            lesson_number=lesson.lesson_number,
            is_completed=lesson.is_completed,
            quiz_score=lesson.quiz_score,
            attempts=lesson.attempts,
            quizzes_passed=sum(q.passed for q in lesson.quizzes),
            quiz_count=len(lesson.quizzes),
        )


class AttemptResponse(CamelModel):
    is_correct: bool
    attempt_number: int = Field(description="Tries on this question so far, including this one")
    selected_index: int
    revealed: bool
    correct_index: int | None = Field(default=None, description="Only set once revealed")
    explanation: str | None = Field(default=None, description="Only set once revealed")
    tries_before_reveal: int | None = Field(
        default=None, description="Wrong tries left before the answer is shown"
    )
    lesson: LessonProgressOut
    lesson_just_completed: bool
    overall_progress: int
    course_completed: bool
    course_just_completed: bool


class CompleteResponse(CamelModel):
    lesson: LessonProgressOut
    lesson_just_completed: bool = Field(description="False if it was already complete")
    overall_progress: int
    course_completed: bool
    course_just_completed: bool
    next: LessonRef | None = Field(description="The next unfinished lesson, if any")


class ExplainResponse(CamelModel):
    explanation: str
    answer_revealed: bool = Field(
        description="False: the tutor explains the mistake without giving the answer away"
    )


class ModuleProgressOut(CamelModel):
    module_number: int
    module_title: str
    progress_percentage: int
    is_completed: bool
    completed_lessons: int
    total_lessons: int
    lessons: list[LessonProgressOut]


class ProgressOut(CamelModel):
    """GET /progress/{ucId}: everything a progress screen or dashboard needs."""

    user_curriculum_id: str
    curriculum_id: str
    course_title: str
    overall_progress: int
    is_completed: bool
    completed_at: datetime | None
    completed_lessons: int
    total_lessons: int
    average_quiz_score: int | None = Field(description="Mean of the scored lessons")
    total_attempts: int
    last_activity_at: datetime | None
    next: LessonRef | None = Field(description="Where to resume")
    modules: list[ModuleProgressOut]

    @classmethod
    def from_doc(cls, doc: UserCurriculum, next_lesson: tuple[int, int] | None) -> "ProgressOut":
        modules = [
            ModuleProgressOut(
                module_number=m.module_number,
                module_title=m.module_title,
                progress_percentage=m.progress_percentage,
                is_completed=m.is_completed,
                completed_lessons=sum(x.is_completed for x in m.lessons),
                total_lessons=len(m.lessons),
                lessons=[LessonProgressOut.build(m, x) for x in m.lessons],
            )
            for m in doc.modules
        ]
        lessons = [x for m in doc.modules for x in m.lessons]
        scores = [x.quiz_score for x in lessons if x.quiz_score is not None]
        return cls(
            user_curriculum_id=str(doc.id),
            curriculum_id=str(doc.curriculum_id),
            course_title=doc.course_title,
            overall_progress=doc.overall_progress,
            is_completed=doc.is_completed,
            completed_at=doc.completed_at,
            completed_lessons=sum(x.is_completed for x in lessons),
            total_lessons=len(lessons),
            average_quiz_score=round(sum(scores) / len(scores)) if scores else None,
            total_attempts=sum(x.attempts for x in lessons),
            last_activity_at=doc.last_activity_at,
            next=LessonRef(module_number=next_lesson[0], lesson_number=next_lesson[1])
            if next_lesson
            else None,
            modules=modules,
        )
