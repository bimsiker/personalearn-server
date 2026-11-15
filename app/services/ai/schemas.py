"""What the AI must return, as Pydantic models. Every provider's raw JSON is validated
against these before it touches the database; Gemini will also receive them as its
structured-output schema in Milestone 2."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user_curriculum import Preferences

OPTION_COUNT = 4


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class ModuleOutline(_Strict):
    module_title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=2000)
    recommended_duration: str = Field(default="", max_length=50)


class CourseOutline(_Strict):
    """Agent 1: the course as a whole."""

    course_title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    learning_objectives: list[str] = Field(default_factory=list, max_length=12)
    key_takeaways: list[str] = Field(default_factory=list, max_length=12)
    source_type: str = Field(default="Curriculum", max_length=60)
    estimated_total_duration: str = Field(default="", max_length=50)
    modules: list[ModuleOutline] = Field(min_length=1)


class QuizDraft(_Strict):
    question: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(min_length=OPTION_COUNT, max_length=OPTION_COUNT)
    correct_index: int = Field(ge=0, lt=OPTION_COUNT)
    explanation: str = Field(min_length=1, max_length=2000)

    @field_validator("options")
    @classmethod
    def _distinct_options(cls, options: list[str]) -> list[str]:
        cleaned = [option.strip() for option in options]
        if any(not option for option in cleaned):
            raise ValueError("options must not be empty")
        if len({option.casefold() for option in cleaned}) != len(cleaned):
            raise ValueError("options must be different from each other")
        return cleaned


class LessonDraft(_Strict):
    lesson_title: str = Field(min_length=1, max_length=200)
    duration: str = Field(default="", max_length=50)
    script: str = Field(min_length=1, max_length=20_000)
    quizzes: list[QuizDraft] = Field(min_length=1)


class ModuleLessons(_Strict):
    """Agent 2: the lessons of one module."""

    lessons: list[LessonDraft] = Field(min_length=1)


@dataclass(frozen=True)
class Limits:
    max_modules: int
    max_lessons_per_module: int
    max_quizzes_per_lesson: int


@dataclass(frozen=True)
class OutlineRequest:
    source_title: str
    source_text: str  # untrusted: from the uploaded file
    preferences: Preferences
    limits: Limits


@dataclass(frozen=True)
class LessonsRequest:
    source_title: str
    source_text: str  # untrusted
    preferences: Preferences
    limits: Limits
    course_title: str
    module_number: int
    module: ModuleOutline
