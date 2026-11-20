"""Scoring and progress rules (services/progress.py): positive and negative paths."""

from datetime import UTC, datetime

import pytest
from beanie import PydanticObjectId

from app.models.user_curriculum import (
    CourseModule,
    GenerationStatus,
    Learner,
    MicroLesson,
    Preferences,
    Quiz,
    UserCurriculum,
)
from app.services import progress

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
REVEAL = 3


def quiz(correct: int = 1) -> Quiz:
    return Quiz(question="Q", options=["a", "b", "c", "d"], correct_index=correct, explanation="E")


def lesson(n: int, quizzes: int = 2) -> MicroLesson:
    return MicroLesson(
        lesson_number=n,
        lesson_title=f"L{n}",
        duration="3m",
        script="s",
        quizzes=[quiz() for _ in range(quizzes)],
    )


def course(*lessons_per_module: int, quizzes: int = 2) -> UserCurriculum:
    return UserCurriculum.model_construct(
        id=PydanticObjectId(),
        owner_id=PydanticObjectId(),
        learner=Learner(type="user", id=PydanticObjectId()),
        curriculum_id=PydanticObjectId(),
        course_title="C",
        preferences=Preferences(
            learning_style="visual", learning_pace="slow", media_preference="text"
        ),
        modules=[
            CourseModule(
                module_number=m,
                module_title=f"M{m}",
                lessons=[lesson(n, quizzes) for n in range(1, count + 1)],
            )
            for m, count in enumerate(lessons_per_module, start=1)
        ],
        overall_progress=0,
        is_completed=False,
        completed_at=None,
        last_activity_at=None,
        generation_status=GenerationStatus.READY,
    )


def answer(c: UserCurriculum, m: int, n: int, q: int, selected: int):
    module = c.modules[m - 1]
    target = module.lessons[n - 1]
    return progress.apply_attempt(
        c, module, target, target.quizzes[q], selected, reveal_after=REVEAL, now=NOW
    )


# ============================================================ attempts


def test_correct_answer_scores_and_reveals():
    c = course(1, quizzes=1)
    outcome = answer(c, 1, 1, 0, 1)
    q = c.modules[0].lessons[0].quizzes[0]
    assert outcome.is_correct and outcome.attempt_number == 1 and outcome.revealed
    assert q.passed and q.first_try_correct is True and q.last_selected_index == 1


def test_wrong_answer_hidden_until_third_try():
    c = course(1, quizzes=1)
    assert answer(c, 1, 1, 0, 0).revealed is False
    assert answer(c, 1, 1, 0, 2).revealed is False
    third = answer(c, 1, 1, 0, 3)
    assert third.revealed is True and third.attempt_number == 3 and not third.is_correct
    assert c.modules[0].lessons[0].quizzes[0].passed is False


def test_reveal_is_never_undone():
    c = course(1, quizzes=1)
    for selected in (0, 2, 3):
        answer(c, 1, 1, 0, selected)
    assert progress.is_revealed(c.modules[0].lessons[0].quizzes[0], reveal_after=99)


def test_score_counts_first_try_only_and_waits_for_all_questions():
    c = course(1, quizzes=2)
    target = c.modules[0].lessons[0]
    answer(c, 1, 1, 0, 0)  # wrong first
    assert target.quiz_score is None  # question 2 not tried yet
    answer(c, 1, 1, 0, 1)  # now right; still a first-try miss
    answer(c, 1, 1, 1, 1)  # right first time
    assert target.quiz_score == 50 and target.attempts == 3


def test_lesson_completes_when_every_question_passed():
    c = course(2, quizzes=2)
    first = answer(c, 1, 1, 0, 1)
    assert not first.lesson_just_completed and c.overall_progress == 0
    second = answer(c, 1, 1, 1, 1)
    assert second.lesson_just_completed
    assert c.modules[0].lessons[0].completed_at == NOW
    assert c.overall_progress == 50 and c.modules[0].progress_percentage == 50
    again = answer(c, 1, 1, 1, 1)  # practising a finished lesson
    assert not again.lesson_just_completed and c.overall_progress == 50


def test_course_completes_once():
    c = course(1, 1, quizzes=1)
    assert not answer(c, 1, 1, 0, 1).course_just_completed
    assert answer(c, 2, 1, 0, 1).course_just_completed
    assert c.is_completed and c.completed_at == NOW and c.overall_progress == 100
    assert all(m.is_completed for m in c.modules)
    assert not answer(c, 2, 1, 0, 1).course_just_completed


def test_wrong_answers_never_complete_a_lesson():
    c = course(1, quizzes=1)
    for selected in (0, 2, 3, 0):
        assert not answer(c, 1, 1, 0, selected).lesson_just_completed
    assert c.overall_progress == 0


# ============================================================ completion and progress


def test_mark_complete_is_idempotent():
    c = course(1, 1, quizzes=1)
    target = c.modules[0].lessons[0]
    assert progress.mark_complete(c, target, NOW) == (True, False)
    assert progress.mark_complete(c, target, NOW) == (False, False)
    assert c.overall_progress == 50 and c.last_activity_at == NOW


@pytest.mark.parametrize(
    "done,total,expected", [(0, 0, 0), (0, 3, 0), (1, 3, 33), (2, 3, 67), (3, 3, 100)]
)
def test_percent(done, total, expected):
    assert progress.percent(done, total) == expected


def test_empty_course_is_zero_percent_not_complete():
    c = course()
    assert progress.recompute(c, NOW) is False
    assert c.overall_progress == 0 and not c.is_completed
    assert progress.next_lesson(c) is None


def test_module_without_lessons_is_not_complete():
    c = course(0, 1, quizzes=1)
    progress.recompute(c, NOW)
    assert c.modules[0].is_completed is False and c.modules[0].progress_percentage == 0


def test_next_lesson_skips_completed():
    c = course(2, 1, quizzes=1)
    assert progress.next_lesson(c) == (1, 1)
    progress.mark_complete(c, c.modules[0].lessons[0], NOW)
    progress.mark_complete(c, c.modules[0].lessons[1], NOW)
    assert progress.next_lesson(c) == (2, 1)
    progress.mark_complete(c, c.modules[1].lessons[0], NOW)
    assert progress.next_lesson(c) is None


def test_lesson_without_quizzes_has_no_score():
    assert progress.lesson_score(lesson(1, quizzes=0)) is None
