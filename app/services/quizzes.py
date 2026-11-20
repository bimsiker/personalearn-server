"""Quiz attempts, lesson completion and the tutor (Milestone 1, Phase 5).

Every write follows the same pattern: load the course, apply the rules in
services/progress.py in memory, then save only if nobody else saved in between
(`revision` check). On a clash we reload and re-apply, so two quick answers can never
overwrite each other's results.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pymongo.errors import DuplicateKeyError

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AppError
from app.models.quiz_attempt import QuizAttempt
from app.models.user import User
from app.models.user_curriculum import (
    CourseModule,
    GenerationStatus,
    MicroLesson,
    Quiz,
    UserCurriculum,
)
from app.services import progress
from app.services.ai import AIProvider
from app.services.ai.agents import GenerationError, explain_answer
from app.services.ai.schemas import TutorRequest
from app.services.learning import find_lesson, owned_course, require_ready

logger = logging.getLogger(__name__)

MAX_SAVE_TRIES = 5


@dataclass
class Located:
    course: UserCurriculum
    module: CourseModule
    lesson: MicroLesson


def find_quiz(lesson: MicroLesson, quiz_index: int) -> Quiz:
    if not 0 <= quiz_index < len(lesson.quizzes):
        raise AppError(404, "NOT_FOUND", "We couldn't find that question.")
    return lesson.quizzes[quiz_index]


async def _locate(course_id: str, user: User, module_number: int, lesson_number: int) -> Located:
    course = await owned_course(course_id, user)
    require_ready(course)
    module, lesson = find_lesson(course, module_number, lesson_number)
    return Located(course, module, lesson)


async def _save(course: UserCurriculum) -> bool:
    """Write progress fields only if the stored revision is still the one we loaded."""
    expected = course.revision
    match: dict = {"_id": course.id, "generation_status": GenerationStatus.READY.value}
    if expected == 0:  # courses created before Phase 5 have no revision field yet
        match["$or"] = [{"revision": 0}, {"revision": {"$exists": False}}]
    else:
        match["revision"] = expected
    now = utcnow()
    result = await UserCurriculum.get_pymongo_collection().update_one(
        match,
        {
            "$set": {
                "modules": [module.model_dump() for module in course.modules],
                "overall_progress": course.overall_progress,
                "is_completed": course.is_completed,
                "completed_at": course.completed_at,
                "last_activity_at": course.last_activity_at,
                "updated_at": now,
                "revision": expected + 1,
            }
        },
    )
    if result.modified_count == 1:
        course.revision = expected + 1
        course.updated_at = now
        return True
    return False


async def _update[T](
    course_id: str,
    user: User,
    module_number: int,
    lesson_number: int,
    change: Callable[[Located], T],
) -> tuple[Located, T]:
    """Load → change in memory → conditional save, retrying on a concurrent write."""
    for attempt in range(1, MAX_SAVE_TRIES + 1):
        located = await _locate(course_id, user, module_number, lesson_number)
        result = change(located)
        if await _save(located.course):
            return located, result
        logger.info("Progress write clashed, retrying course=%s try=%d", course_id, attempt)
    raise AppError(409, "CONFLICT", "Your progress changed at the same time. Please try again.")


# ============================================================ attempts


@dataclass
class AttemptResult:
    located: Located
    quiz: Quiz
    outcome: progress.AttemptOutcome


async def attempt(
    course_id: str,
    user: User,
    *,
    module_number: int,
    lesson_number: int,
    quiz_index: int,
    selected_index: int,
    settings: Settings,
) -> AttemptResult:
    def change(located: Located) -> tuple[Quiz, progress.AttemptOutcome]:
        quiz = find_quiz(located.lesson, quiz_index)
        if selected_index >= len(quiz.options):
            raise AppError(422, "VALIDATION_ERROR", "That answer option doesn't exist.")
        outcome = progress.apply_attempt(
            located.course,
            located.module,
            located.lesson,
            quiz,
            selected_index,
            reveal_after=settings.quiz_reveal_after_attempts,
            now=utcnow(),
        )
        return quiz, outcome

    located, (quiz, outcome) = await _update(course_id, user, module_number, lesson_number, change)
    course = located.course
    try:
        await QuizAttempt(
            owner_id=course.owner_id,
            learner=course.learner,
            user_curriculum_id=course.id,
            curriculum_id=course.curriculum_id,
            module_number=module_number,
            lesson_number=lesson_number,
            quiz_index=quiz_index,
            selected_index=selected_index,
            is_correct=outcome.is_correct,
            attempt_number=outcome.attempt_number,
        ).insert()
    except DuplicateKeyError:  # can't happen with the revision check; never lose progress
        logger.warning("Duplicate quiz attempt row course=%s", course.id)

    logger.info(
        "Quiz attempt course=%s lesson=%d.%d quiz=%d correct=%s try=%d progress=%d%%",
        course.id,
        module_number,
        lesson_number,
        quiz_index,
        outcome.is_correct,
        outcome.attempt_number,
        course.overall_progress,
    )
    if outcome.course_just_completed:
        logger.info("Course completed course=%s user=%s", course.id, user.id)
    return AttemptResult(located=located, quiz=quiz, outcome=outcome)


# ============================================================ completion


async def complete_lesson(
    course_id: str, user: User, *, module_number: int, lesson_number: int
) -> tuple[Located, bool, bool]:
    """Mark a lesson done (idempotent). Returns (located, lesson_just_done, course_just_done)."""
    located, (lesson_done, course_done) = await _update(
        course_id,
        user,
        module_number,
        lesson_number,
        lambda loc: progress.mark_complete(loc.course, loc.lesson, utcnow()),
    )
    if lesson_done:
        logger.info(
            "Lesson completed course=%s lesson=%d.%d progress=%d%%",
            located.course.id,
            module_number,
            lesson_number,
            located.course.overall_progress,
        )
    return located, lesson_done, course_done


# ============================================================ tutor


async def explain(
    course_id: str,
    user: User,
    *,
    module_number: int,
    lesson_number: int,
    quiz_index: int,
    selected_index: int | None,
    settings: Settings,
    ai: AIProvider,
) -> tuple[str, bool]:
    """Tutor explanation of an answer. Returns (explanation, answer_revealed)."""
    located = await _locate(course_id, user, module_number, lesson_number)
    quiz = find_quiz(located.lesson, quiz_index)
    if quiz.attempts == 0:
        raise AppError(409, "ANSWER_FIRST", "Answer this question first, then ask for help.")
    chosen = quiz.last_selected_index if selected_index is None else selected_index
    if chosen is None or not 0 <= chosen < len(quiz.options):
        raise AppError(422, "VALIDATION_ERROR", "That answer option doesn't exist.")

    may_reveal = progress.is_revealed(quiz, settings.quiz_reveal_after_attempts)
    try:
        text = await explain_answer(
            ai,
            TutorRequest(
                lesson_title=located.lesson.lesson_title,
                lesson_script=located.lesson.script,
                question=quiz.question,
                options=quiz.options,
                selected_index=chosen,
                correct_index=quiz.correct_index,
                may_reveal=may_reveal,
                preferences=located.course.preferences,
            ),
        )
    except GenerationError as exc:
        raise _tutor_unavailable() from exc
    except Exception as exc:
        logger.exception("Tutor crashed course=%s", located.course.id)
        raise _tutor_unavailable() from exc
    return text, may_reveal


def _tutor_unavailable() -> AppError:
    return AppError(
        503, "TUTOR_UNAVAILABLE", "The tutor isn't available right now. Please try again."
    )


# ============================================================ cleanup


async def delete_attempts_for_curriculum(curriculum_id) -> int:
    result = await QuizAttempt.get_pymongo_collection().delete_many(
        {"curriculum_id": curriculum_id}
    )
    return result.deleted_count
