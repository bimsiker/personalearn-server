"""Quiz scoring and course progress. Pure functions (no database), so the rules are easy to
test and every write path (attempt, complete) applies them the same way.

Rules
- A quiz question is **passed** once it has been answered correctly.
- Its answer and explanation are **revealed** once passed, or after
  QUIZ_REVEAL_AFTER_ATTEMPTS wrong tries, so a stuck learner can move on.
- A lesson's **quiz score** is the % of its questions answered correctly on the first try.
  It stays None until every question has been tried at least once.
- A lesson is **completed** when all its questions are passed, or when the learner marks
  it complete. Completion is never undone.
- Module and course **progress** is completed lessons / lessons, rounded to a whole %.
  An empty course is 0% (never a divide-by-zero).
"""

from dataclasses import dataclass
from datetime import datetime

from app.models.user_curriculum import CourseModule, MicroLesson, Quiz, UserCurriculum


@dataclass(frozen=True)
class AttemptOutcome:
    is_correct: bool
    attempt_number: int  # per question, 1-based
    revealed: bool
    lesson_just_completed: bool
    course_just_completed: bool


def percent(done: int, total: int) -> int:
    return round(100 * done / total) if total else 0


def is_revealed(quiz: Quiz, reveal_after: int) -> bool:
    return quiz.revealed or quiz.passed or quiz.attempts >= reveal_after


def lesson_score(lesson: MicroLesson) -> int | None:
    if not lesson.quizzes or any(q.first_try_correct is None for q in lesson.quizzes):
        return None
    return percent(sum(bool(q.first_try_correct) for q in lesson.quizzes), len(lesson.quizzes))


def recompute(course: UserCurriculum, now: datetime) -> bool:
    """Refresh module/course progress from the lessons. Returns True if the course
    became complete in this call."""
    total = done = 0
    for module in course.modules:
        module_done = sum(lesson.is_completed for lesson in module.lessons)
        module.progress_percentage = percent(module_done, len(module.lessons))
        module.is_completed = bool(module.lessons) and module_done == len(module.lessons)
        total += len(module.lessons)
        done += module_done
    course.overall_progress = percent(done, total)
    was_completed = course.is_completed
    course.is_completed = total > 0 and done == total
    if course.is_completed and not was_completed:
        course.completed_at = now
    course.last_activity_at = now
    return course.is_completed and not was_completed


def _complete_lesson(lesson: MicroLesson, now: datetime) -> bool:
    if lesson.is_completed:
        return False
    lesson.is_completed = True
    lesson.completed_at = now
    return True


def apply_attempt(
    course: UserCurriculum,
    module: CourseModule,
    lesson: MicroLesson,
    quiz: Quiz,
    selected_index: int,
    *,
    reveal_after: int,
    now: datetime,
) -> AttemptOutcome:
    """Record one answer on the in-memory course and update every derived field."""
    is_correct = selected_index == quiz.correct_index
    quiz.attempts += 1
    quiz.last_selected_index = selected_index
    if quiz.first_try_correct is None:
        quiz.first_try_correct = is_correct
    if is_correct:
        quiz.passed = True
    quiz.revealed = is_revealed(quiz, reveal_after)

    lesson.attempts += 1
    lesson.quiz_score = lesson_score(lesson)
    lesson_done = all(q.passed for q in lesson.quizzes) and _complete_lesson(lesson, now)
    course_done = recompute(course, now)
    return AttemptOutcome(
        is_correct=is_correct,
        attempt_number=quiz.attempts,
        revealed=quiz.revealed,
        lesson_just_completed=lesson_done,
        course_just_completed=course_done,
    )


def mark_complete(course: UserCurriculum, lesson: MicroLesson, now: datetime) -> tuple[bool, bool]:
    """The learner marks a lesson done. Returns (lesson_just_completed, course_just_completed)."""
    lesson_done = _complete_lesson(lesson, now)
    course_done = recompute(course, now)
    return lesson_done, course_done


def next_lesson(course: UserCurriculum) -> tuple[int, int] | None:
    """Where to resume: the first lesson not yet completed."""
    for module in course.modules:
        for lesson in module.lessons:
            if not lesson.is_completed:
                return module.module_number, lesson.lesson_number
    return None
