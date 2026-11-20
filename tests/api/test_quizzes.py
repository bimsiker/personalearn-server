"""Quizzes and progress (Phase 5): attempts, reveal rule, completion, progress, tutor.

Covers success, 401/404/409/422/503, concurrent answers and cleanup on delete.
"""

import asyncio

import pytest

from app.models import QuizAttempt, UserCurriculum
from tests.api.test_learning import GatedAI, _enroll, _material, _ready_course
from tests.conftest import bearer

pytestmark = pytest.mark.db


async def _answers(course_id: str, module: int = 1, lesson: int = 1) -> list[int]:
    """The stored correct option for each question in a lesson (tests only)."""
    doc = await UserCurriculum.get(course_id)
    return [q.correct_index for q in doc.modules[module - 1].lessons[lesson - 1].quizzes]


def _wrong(correct: int) -> int:
    return (correct + 1) % 4


async def _attempt(api, token, ucid, selected, *, module=1, lesson=1, quiz=0):
    return await api.client.post(
        f"/curriculum/{ucid}/attempt",
        headers=bearer(token),
        json={
            "moduleNumber": module,
            "lessonNumber": lesson,
            "quizIndex": quiz,
            "selectedIndex": selected,
        },
    )


async def _explain(api, token, ucid, *, quiz=0, selected=None):
    body = {"moduleNumber": 1, "lessonNumber": 1, "quizIndex": quiz}
    if selected is not None:
        body["selectedIndex"] = selected
    return await api.client.post(f"/curriculum/{ucid}/explain", headers=bearer(token), json=body)


async def _complete(api, token, ucid, module, lesson):
    return await api.client.post(
        f"/user-curricula/{ucid}/lessons/{module}/{lesson}/complete", headers=bearer(token)
    )


async def _progress(api, token, ucid):
    return await api.client.get(f"/progress/{ucid}", headers=bearer(token))


# ============================================================ attempts


async def test_correct_answer_reveals_and_is_recorded(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    correct = (await _answers(ucid))[0]

    res = await _attempt(api, token, ucid, correct)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["isCorrect"] is True and body["attemptNumber"] == 1
    assert body["revealed"] is True and body["correctIndex"] == correct and body["explanation"]
    assert body["triesBeforeReveal"] is None
    assert body["lesson"]["quizzesPassed"] == 1 and body["lesson"]["attempts"] == 1

    rows = await QuizAttempt.find_all().to_list()
    assert len(rows) == 1 and rows[0].is_correct and rows[0].selected_index == correct


async def test_wrong_answers_hidden_until_third_try(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    wrong = _wrong((await _answers(ucid))[0])

    first = (await _attempt(api, token, ucid, wrong)).json()
    assert first["isCorrect"] is False and first["revealed"] is False
    assert first["correctIndex"] is None and first["explanation"] is None
    assert first["triesBeforeReveal"] == 2
    assert (await _attempt(api, token, ucid, wrong)).json()["triesBeforeReveal"] == 1
    third = (await _attempt(api, token, ucid, wrong)).json()
    assert third["revealed"] is True and third["correctIndex"] is not None
    assert third["attemptNumber"] == 3


async def test_lesson_completes_and_course_shows_state(api):
    token, cid, course = await _ready_course(api)
    ucid = course["_id"]
    answers = await _answers(ucid)
    wrong_first = (await _attempt(api, token, ucid, _wrong(answers[0]))).json()
    assert wrong_first["lessonJustCompleted"] is False

    last = None
    for index, correct in enumerate(answers):
        last = (await _attempt(api, token, ucid, correct, quiz=index)).json()
    assert last["lessonJustCompleted"] is True and last["lesson"]["isCompleted"] is True
    assert last["overallProgress"] > 0

    # Reloading the course (e.g. after a refresh) shows the same state.
    reloaded = (
        await api.client.get(f"/curriculum/{cid}/user-curriculum", headers=bearer(token))
    ).json()
    first_lesson = reloaded["modules"][0]["lessons"][0]
    assert first_lesson["isCompleted"] is True and reloaded["overallProgress"] > 0
    # question 0 was missed on the first try; the others were right first time
    assert first_lesson["quizScore"] == round(100 * (len(answers) - 1) / len(answers))
    q0 = first_lesson["quizzes"][0]
    assert q0["passed"] and q0["revealed"] and q0["correctIndex"] == answers[0]
    assert q0["attempts"] == 2 and q0["lastSelectedIndex"] == answers[0]


async def test_quiz_score_counts_first_try(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    answers = await _answers(ucid)
    for index, correct in enumerate(answers):
        res = await _attempt(api, token, ucid, correct, quiz=index)
    assert res.json()["lesson"]["quizScore"] == 100


async def test_concurrent_answers_are_all_counted(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    wrong = _wrong((await _answers(ucid))[0])
    results = await asyncio.gather(*[_attempt(api, token, ucid, wrong) for _ in range(4)])
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert sorted(r.json()["attemptNumber"] for r in results) == [1, 2, 3, 4]
    doc = await UserCurriculum.get(ucid)
    assert doc.modules[0].lessons[0].quizzes[0].attempts == 4
    assert await QuizAttempt.find_all().count() == 4


# ============================================================ completion and progress


async def test_complete_every_lesson_finishes_course(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    positions = [
        (m["moduleNumber"], x["lessonNumber"]) for m in course["modules"] for x in m["lessons"]
    ]
    for index, (m, n) in enumerate(positions):
        res = await _complete(api, token, ucid, m, n)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["lessonJustCompleted"] is True
        is_last = index == len(positions) - 1
        assert body["courseJustCompleted"] is is_last and body["courseCompleted"] is is_last
        assert body["next"] == (
            None
            if is_last
            else {"moduleNumber": positions[index + 1][0], "lessonNumber": positions[index + 1][1]}
        )
    assert body["overallProgress"] == 100

    again = (await _complete(api, token, ucid, *positions[0])).json()
    assert again["lessonJustCompleted"] is False and again["courseJustCompleted"] is False

    progress = (await _progress(api, token, ucid)).json()
    assert progress["isCompleted"] is True and progress["completedAt"]
    assert progress["completedLessons"] == progress["totalLessons"] == len(positions)
    assert progress["next"] is None


async def test_progress_summary(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    fresh = (await _progress(api, token, ucid)).json()
    assert fresh["overallProgress"] == 0 and fresh["averageQuizScore"] is None
    assert fresh["next"] == {"moduleNumber": 1, "lessonNumber": 1}
    assert fresh["lastActivityAt"] is None and fresh["totalAttempts"] == 0

    for index, correct in enumerate(await _answers(ucid)):
        await _attempt(api, token, ucid, correct, quiz=index)
    body = (await _progress(api, token, ucid)).json()
    assert body["averageQuizScore"] == 100 and body["completedLessons"] == 1
    assert body["next"] == {"moduleNumber": 1, "lessonNumber": 2}
    assert body["lastActivityAt"] and body["totalAttempts"] >= 1
    first = body["modules"][0]
    assert first["completedLessons"] == 1 and first["lessons"][0]["isCompleted"] is True
    assert "preferences" not in body  # progress never includes private preferences


# ============================================================ tutor


async def test_tutor_requires_an_answer_first(api):
    token, _, course = await _ready_course(api)
    res = await _explain(api, token, course["_id"])
    assert res.status_code == 409 and res.json()["code"] == "ANSWER_FIRST"


async def test_tutor_hints_then_reveals(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    correct = (await _answers(ucid))[0]
    wrong = _wrong(correct)
    await _attempt(api, token, ucid, wrong)
    options = (await UserCurriculum.get(ucid)).modules[0].lessons[0].quizzes[0].options

    hint = (await _explain(api, token, ucid)).json()  # defaults to the last answer
    assert hint["answerRevealed"] is False
    assert options[wrong] in hint["explanation"] and options[correct] not in hint["explanation"]

    await _attempt(api, token, ucid, correct)
    full = (await _explain(api, token, ucid, selected=wrong)).json()
    assert full["answerRevealed"] is True and options[correct] in full["explanation"]


async def test_tutor_failure_is_503(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    await _attempt(api, token, ucid, 0)

    class BrokenTutor:
        name = "broken"

        async def tutor(self, request):
            raise RuntimeError("provider down")

    api.app.state.ai = BrokenTutor()
    res = await _explain(api, token, ucid)
    assert res.status_code == 503 and res.json()["code"] == "TUTOR_UNAVAILABLE"
    assert "provider down" not in res.text


# ============================================================ errors


async def test_requires_login(api):
    _, _, course = await _ready_course(api)
    ucid = course["_id"]
    body = {"moduleNumber": 1, "lessonNumber": 1, "quizIndex": 0, "selectedIndex": 0}
    assert (await api.client.post(f"/curriculum/{ucid}/attempt", json=body)).status_code == 401
    assert (await api.client.get(f"/progress/{ucid}")).status_code == 401
    res = await api.client.post(f"/user-curricula/{ucid}/lessons/1/1/complete")
    assert res.status_code == 401


async def test_other_learners_course_is_404(api):
    _, _, course = await _ready_course(api)
    ucid = course["_id"]
    _, other = await api.verified_user()
    token = other["accessToken"]
    assert (await _attempt(api, token, ucid, 0)).status_code == 404
    assert (await _progress(api, token, ucid)).status_code == 404
    assert (await _complete(api, token, ucid, 1, 1)).status_code == 404
    assert (await _explain(api, token, ucid)).status_code == 404


@pytest.mark.parametrize(
    "position",
    [{"module": 9}, {"lesson": 9}, {"quiz": 9}],
)
async def test_missing_lesson_or_question_is_404(api, position):
    token, _, course = await _ready_course(api)
    res = await _attempt(api, token, course["_id"], 0, **position)
    assert res.status_code == 404 and res.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "change",
    [
        {"selectedIndex": 4},
        {"selectedIndex": -1},
        {"quizIndex": -1},
        {"moduleNumber": 0},
        {"selectedIndex": None},
    ],
)
async def test_invalid_attempt_is_422(api, change):
    token, _, course = await _ready_course(api)
    body = {"moduleNumber": 1, "lessonNumber": 1, "quizIndex": 0, "selectedIndex": 0, **change}
    res = await api.client.post(
        f"/curriculum/{course['_id']}/attempt", headers=bearer(token), json=body
    )
    assert res.status_code == 422


async def test_course_not_ready_is_409(api):
    gated = GatedAI(api.app.state.ai)
    api.app.state.ai = gated
    token, cid = await _material(api)
    ucid = (await _enroll(api, token, cid)).json()["_id"]
    assert (await _attempt(api, token, ucid, 0)).status_code == 409
    assert (await _progress(api, token, ucid)).status_code == 409
    assert (await _complete(api, token, ucid, 1, 1)).status_code == 409
    gated.gate.set()
    await api.app.state.jobs.wait_idle()


# ============================================================ cleanup


async def test_deleting_material_deletes_attempts(api):
    token, cid, course = await _ready_course(api)
    await _attempt(api, token, course["_id"], 0)
    assert await QuizAttempt.find_all().count() == 1
    assert (await api.client.delete(f"/curriculum/{cid}", headers=bearer(token))).status_code == 204
    assert await QuizAttempt.find_all().count() == 0
