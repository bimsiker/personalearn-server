"""Learning helpers that need no database: preference input, lesson lookup, navigation,
and the course response never leaking quiz answers."""

import pytest
from beanie import PydanticObjectId
from pydantic import ValidationError

from app.core.errors import AppError
from app.models.user_curriculum import (
    CourseModule,
    GenerationStatus,
    Learner,
    MicroLesson,
    Preferences,
    Quiz,
    UserCurriculum,
)
from app.schemas.learning import PreferencesIn, UserCurriculumOut
from app.services.learning import find_lesson, neighbours, require_ready

QUIZ = Quiz(question="Q?", options=["a", "b", "c", "d"], correct_index=2, explanation="Because")


def lesson(n: int) -> MicroLesson:
    return MicroLesson(
        lesson_number=n, lesson_title=f"L{n}", duration="3m", script="s", quizzes=[QUIZ]
    )


def course(status=GenerationStatus.READY, lessons_per_module=(2, 1)) -> UserCurriculum:
    # model_construct: build a Document without a database connection
    return UserCurriculum.model_construct(
        id=PydanticObjectId(),
        owner_id=PydanticObjectId(),
        learner=Learner(type="user", id=PydanticObjectId()),
        curriculum_id=PydanticObjectId(),
        course_title="Course",
        preferences=Preferences(
            learning_style="visual", learning_pace="slow", media_preference="text"
        ),
        modules=[
            CourseModule(
                module_number=m,
                module_title=f"M{m}",
                lessons=[lesson(n) for n in range(1, count + 1)],
            )
            for m, count in enumerate(lessons_per_module, start=1)
        ],
        generation_status=status,
    )


# ============================================================ preferences input


def test_preferences_accept_camel_case_and_blank_extras():
    prefs = PreferencesIn.model_validate(
        {
            "learningStyle": "visual",
            "learningPace": "fast",
            "mediaPreference": "video",
            "avatarId": "  ",
            "additionalPreferences": "  examples please ",
        }
    )
    assert prefs.avatar_id is None and prefs.additional_preferences == "examples please"
    assert prefs.to_model().wants_video


@pytest.mark.parametrize(
    "change",
    [
        {"learningStyle": "telepathic"},
        {"learningPace": "instant"},
        {"mediaPreference": "hologram"},
        {"additionalPreferences": "x" * 501},
        {"avatarId": "a" * 101},
    ],
)
def test_preferences_rejected(change):
    body = {"learningStyle": "visual", "learningPace": "fast", "mediaPreference": "text"}
    with pytest.raises(ValidationError):
        PreferencesIn.model_validate({**body, **change})


@pytest.mark.parametrize(
    "media,wants", [("video", True), ("mixed", True), ("text", False), ("audio", False)]
)
def test_text_and_audio_skip_video(media, wants):
    prefs = Preferences(learning_style="audio", learning_pace="slow", media_preference=media)
    assert prefs.wants_video is wants


# ============================================================ lessons


def test_find_lesson():
    module, found = find_lesson(course(), 1, 2)
    assert module.module_title == "M1" and found.lesson_title == "L2"


@pytest.mark.parametrize("m,n", [(0, 1), (1, 3), (3, 1), (2, 2)])
def test_find_missing_lesson_is_404(m, n):
    with pytest.raises(AppError) as info:
        find_lesson(course(), m, n)
    assert info.value.status_code == 404


def test_neighbours_cross_module_boundaries():
    c = course()
    assert neighbours(c, 1, 1) == (None, (1, 2))
    assert neighbours(c, 1, 2) == ((1, 1), (2, 1))
    assert neighbours(c, 2, 1) == ((1, 2), None)


@pytest.mark.parametrize(
    "status,fragment",
    [
        (GenerationStatus.QUEUED, "still being built"),
        (GenerationStatus.GENERATING, "still being built"),
        (GenerationStatus.FAILED, "retry"),
    ],
)
def test_require_ready(status, fragment):
    with pytest.raises(AppError) as info:
        require_ready(course(status))
    assert info.value.status_code == 409 and info.value.code == "COURSE_NOT_READY"
    assert fragment in info.value.message
    require_ready(course(GenerationStatus.READY))  # no error


# ============================================================ response shape


def test_course_response_matches_frontend_and_hides_answers():
    body = UserCurriculumOut.from_doc(course()).model_dump(by_alias=True, mode="json")
    assert {"_id", "userId", "curriculumId", "courseTitle", "modules", "generationStatus"} <= set(
        body
    )
    first = body["modules"][0]["lessons"][0]
    assert first["moduleTitle"] == "M1" and first["quizCount"] == 1
    quiz = first["quizzes"][0]
    assert quiz["question"] == "Q?" and quiz["options"] == ["a", "b", "c", "d"]
    assert quiz["revealed"] is False and quiz["attempts"] == 0
    assert quiz["correctIndex"] is None and quiz["explanation"] is None
    assert "Because" not in str(body)  # the stored explanation never leaks


def test_revealed_quiz_shows_answer():
    c = course()
    c.modules[0].lessons[0].quizzes[0] = QUIZ.model_copy(update={"revealed": True, "passed": True})
    body = UserCurriculumOut.from_doc(c).model_dump(by_alias=True, mode="json")
    quiz = body["modules"][0]["lessons"][0]["quizzes"][0]
    assert quiz["correctIndex"] == 2 and quiz["explanation"] == "Because"
