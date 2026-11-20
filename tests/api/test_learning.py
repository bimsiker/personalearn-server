"""Personalization + generation (Phase 4): enroll -> background job -> poll -> read lessons.

Covers success, 401/404/409/422, failed generation and retry, restart recovery, deletes.
"""

import asyncio

import pytest

from app.models import Curriculum, UserCurriculum
from app.services import learning
from tests.conftest import bearer
from tests.pdf_factory import SYLLABUS, make_pdf

pytestmark = pytest.mark.db

PDF = make_pdf([SYLLABUS, "Week 2: genetics and heredity."])
PREFS = {"learningStyle": "visual", "learningPace": "moderate", "mediaPreference": "text"}


async def _material(api, *, pdf: bytes = PDF) -> tuple[str, str]:
    """A verified student with one uploaded material. Returns (token, curriculum id)."""
    _, auth = await api.verified_user()
    token = auth["accessToken"]
    res = await api.upload(token, pdf)
    assert res.status_code == 201, res.text
    return token, res.json()["_id"]


async def _enroll(api, token: str, curriculum_id: str, prefs: dict | None = None):
    return await api.client.post(
        f"/curriculum/{curriculum_id}/enroll", headers=bearer(token), json=prefs or PREFS
    )


async def _course(api, token: str, curriculum_id: str):
    return await api.client.get(
        f"/curriculum/{curriculum_id}/user-curriculum", headers=bearer(token)
    )


async def _ready_course(api) -> tuple[str, str, dict]:
    token, cid = await _material(api)
    assert (await _enroll(api, token, cid)).status_code == 202
    await api.app.state.jobs.wait_idle()
    res = await _course(api, token, cid)
    assert res.json()["generationStatus"] == "ready", res.text
    return token, cid, res.json()


class GatedAI:
    """A provider that waits until the test opens the gate (to observe in-flight states)."""

    name = "gated"

    def __init__(self, inner) -> None:
        self.inner = inner
        self.gate = asyncio.Event()

    async def outline(self, request):
        await self.gate.wait()
        return await self.inner.outline(request)

    async def lessons(self, request):
        return await self.inner.lessons(request)


class BrokenAI:
    name = "broken"

    async def outline(self, request):
        return {"course_title": ""}  # always invalid

    async def lessons(self, request):
        return {}


# ============================================================ happy path


async def test_enroll_generates_course(api):
    token, cid = await _material(api)
    res = await _enroll(api, token, cid, {**PREFS, "additionalPreferences": "Use examples"})
    assert res.status_code == 202, res.text
    queued = res.json()
    assert queued["generationStatus"] == "queued" and queued["modules"] == []
    assert queued["curriculumId"] == cid
    assert queued["preferences"]["additionalPreferences"] == "Use examples"

    await api.app.state.jobs.wait_idle()
    body = (await _course(api, token, cid)).json()
    assert body["_id"] == queued["_id"]
    assert body["generationStatus"] == "ready" and body["generationError"] is None
    assert body["courseTitle"].startswith("Intro to Biology")
    assert body["metadata"]["summary"] and body["metadata"]["learningObjectives"]
    assert body["totalModules"] == len(body["modules"]) == api.settings.max_modules
    assert body["overallProgress"] == 0 and body["isCompleted"] is False
    assert body["videoGenerationStatus"] == "not_started"
    lesson = body["modules"][0]["lessons"][0]
    assert lesson["lessonNumber"] == 1 and lesson["script"] and lesson["quizCount"] >= 1
    assert len(lesson["quizzes"][0]["options"]) == 4
    assert all(
        q["correctIndex"] is None and q["explanation"] is None
        for m in body["modules"]
        for x in m["lessons"]
        for q in x["quizzes"]
    )

    me = (await api.client.get("/users/me", headers=bearer(token))).json()
    assert me["curriculaEnrolled"] == 1
    listed = (await api.client.get("/curriculum", headers=bearer(token))).json()
    assert listed[0]["hasLesson"] is True
    one = (await api.client.get(f"/curriculum/{cid}", headers=bearer(token))).json()
    assert one["hasLesson"] is True


async def test_course_is_queued_then_generating_while_in_flight(api):
    gated = GatedAI(api.app.state.ai)
    api.app.state.ai = gated
    token, cid = await _material(api)
    await _enroll(api, token, cid)
    await asyncio.sleep(0.05)  # let the job claim the course
    assert (await _course(api, token, cid)).json()["generationStatus"] == "generating"
    gated.gate.set()
    await api.app.state.jobs.wait_idle()
    assert (await _course(api, token, cid)).json()["generationStatus"] == "ready"


async def test_avatar_kept_only_for_video(api):
    token, cid = await _material(api)
    res = await _enroll(api, token, cid, {**PREFS, "mediaPreference": "video", "avatarId": "a1"})
    assert res.json()["avatarConfig"]["avatarId"] == "a1"

    token2, cid2 = await _material(api)
    res = await _enroll(api, token2, cid2, {**PREFS, "mediaPreference": "text", "avatarId": "a1"})
    assert res.json()["avatarConfig"]["avatarId"] is None
    await api.app.state.jobs.wait_idle()


async def test_answers_stored_in_database(api):
    token, cid, body = await _ready_course(api)
    doc = await UserCurriculum.get(body["_id"])
    quiz = doc.modules[0].lessons[0].quizzes[0]
    assert 0 <= quiz.correct_index < 4 and quiz.explanation


# ============================================================ lessons


async def test_get_lesson_with_navigation(api):
    token, _, course = await _ready_course(api)
    ucid = course["_id"]
    res = await api.client.get(f"/user-curricula/{ucid}/lessons/1/1", headers=bearer(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["userCurriculumId"] == ucid and body["courseTitle"] == course["courseTitle"]
    assert body["lesson"]["moduleNumber"] == 1 and body["lesson"]["lessonNumber"] == 1
    assert body["previous"] is None
    assert body["next"] == {"moduleNumber": 1, "lessonNumber": 2}

    last_module = course["modules"][-1]
    last = (last_module["moduleNumber"], last_module["lessons"][-1]["lessonNumber"])
    res = await api.client.get(
        f"/user-curricula/{ucid}/lessons/{last[0]}/{last[1]}", headers=bearer(token)
    )
    assert res.json()["next"] is None and res.json()["previous"] is not None


@pytest.mark.parametrize("path", ["9/1", "1/9"])
async def test_missing_lesson_is_404(api, path):
    token, _, course = await _ready_course(api)
    res = await api.client.get(
        f"/user-curricula/{course['_id']}/lessons/{path}", headers=bearer(token)
    )
    assert res.status_code == 404 and res.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize("path", ["0/1", "1/0", "x/1"])
async def test_bad_lesson_numbers_are_422(api, path):
    token, _, course = await _ready_course(api)
    res = await api.client.get(
        f"/user-curricula/{course['_id']}/lessons/{path}", headers=bearer(token)
    )
    assert res.status_code == 422


async def test_lesson_before_ready_is_409(api):
    gated = GatedAI(api.app.state.ai)
    api.app.state.ai = gated
    token, cid = await _material(api)
    ucid = (await _enroll(api, token, cid)).json()["_id"]
    res = await api.client.get(f"/user-curricula/{ucid}/lessons/1/1", headers=bearer(token))
    assert res.status_code == 409 and res.json()["code"] == "COURSE_NOT_READY"
    gated.gate.set()
    await api.app.state.jobs.wait_idle()


async def test_cannot_read_someone_elses_course(api):
    _, cid, course = await _ready_course(api)
    _, other = await api.verified_user()
    other_token = other["accessToken"]
    res = await api.client.get(
        f"/user-curricula/{course['_id']}/lessons/1/1", headers=bearer(other_token)
    )
    assert res.status_code == 404
    res = await _course(api, other_token, cid)
    assert res.status_code == 404
    res = await _enroll(api, other_token, cid)
    assert res.status_code == 404  # can't personalize someone else's material


# ============================================================ errors


async def test_enroll_requires_login(api):
    _, cid = await _material(api)
    res = await api.client.post(f"/curriculum/{cid}/enroll", json=PREFS)
    assert res.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {**PREFS, "learningStyle": "telepathic"},
        {**PREFS, "learningPace": None},
        {**PREFS, "additionalPreferences": "x" * 501},
    ],
)
async def test_enroll_invalid_preferences_is_422(api, body):
    token, cid = await _material(api)
    res = await api.client.post(f"/curriculum/{cid}/enroll", headers=bearer(token), json=body)
    assert res.status_code == 422 and res.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("cid", ["not-an-id", "64b000000000000000000000"])
async def test_enroll_unknown_material_is_404(api, cid):
    _, auth = await api.verified_user()
    res = await _enroll(api, auth["accessToken"], cid)
    assert res.status_code == 404


async def test_enroll_twice_is_409(api):
    token, cid = await _material(api)
    assert (await _enroll(api, token, cid)).status_code == 202
    res = await _enroll(api, token, cid)
    assert res.status_code == 409 and res.json()["code"] == "ALREADY_ENROLLED"
    await api.app.state.jobs.wait_idle()
    assert (await _enroll(api, token, cid)).status_code == 409  # ready courses too
    me = (await api.client.get("/users/me", headers=bearer(token))).json()
    assert me["curriculaEnrolled"] == 1


async def test_enroll_without_readable_text_is_422(api):
    token, cid = await _material(api, pdf=make_pdf([""]))
    res = await _enroll(api, token, cid)
    assert res.status_code == 422 and res.json()["code"] == "NO_READABLE_TEXT"


async def test_course_not_enrolled_is_404(api):
    token, cid = await _material(api)
    res = await _course(api, token, cid)
    assert res.status_code == 404 and res.json()["code"] == "NOT_ENROLLED"


# ============================================================ failure, retry, recovery


async def test_invalid_ai_output_marks_course_failed(api):
    api.app.state.ai = BrokenAI()
    token, cid = await _material(api)
    await _enroll(api, token, cid)
    await api.app.state.jobs.wait_idle()
    body = (await _course(api, token, cid)).json()
    assert body["generationStatus"] == "failed"
    assert "try personalizing again" in body["generationError"]
    assert body["modules"] == []


async def test_crashing_ai_shows_generic_message(api):
    token, cid = await _material(api)
    await _enroll(api, token, cid, {**PREFS, "additionalPreferences": "#fail"})
    await api.app.state.jobs.wait_idle()
    body = (await _course(api, token, cid)).json()
    assert body["generationStatus"] == "failed"
    assert body["generationError"] == learning.GENERIC_FAILURE  # no internal details


async def test_failed_course_can_be_retried_with_new_preferences(api):
    token, cid = await _material(api)
    first = (await _enroll(api, token, cid, {**PREFS, "additionalPreferences": "#fail"})).json()
    await api.app.state.jobs.wait_idle()

    res = await _enroll(api, token, cid, {**PREFS, "learningPace": "fast"})
    assert res.status_code == 202, res.text
    assert res.json()["_id"] == first["_id"]
    assert res.json()["generationStatus"] == "queued" and res.json()["generationError"] is None
    await api.app.state.jobs.wait_idle()

    body = (await _course(api, token, cid)).json()
    assert body["generationStatus"] == "ready"
    assert body["preferences"]["learningPace"] == "fast"
    me = (await api.client.get("/users/me", headers=bearer(token))).json()
    assert me["curriculaEnrolled"] == 1  # a retry is not a second enrollment


async def test_recover_interrupted_marks_stuck_courses_failed(api):
    gated = GatedAI(api.app.state.ai)
    api.app.state.ai = gated
    token, cid = await _material(api)
    await _enroll(api, token, cid)
    await asyncio.sleep(0.05)
    await api.app.state.jobs.shutdown()  # simulate the server stopping mid-job

    assert await learning.recover_interrupted() == 1
    body = (await _course(api, token, cid)).json()
    assert body["generationStatus"] == "failed"
    assert body["generationError"] == learning.INTERRUPTED


# ============================================================ deletes


async def test_deleting_material_deletes_its_course(api):
    token, cid, course = await _ready_course(api)
    res = await api.client.delete(f"/curriculum/{cid}", headers=bearer(token))
    assert res.status_code == 204
    assert await UserCurriculum.get(course["_id"]) is None
    me = (await api.client.get("/users/me", headers=bearer(token))).json()
    assert me["curriculaEnrolled"] == 0


async def test_delete_during_generation_stays_deleted(api):
    gated = GatedAI(api.app.state.ai)
    api.app.state.ai = gated
    token, cid = await _material(api)
    ucid = (await _enroll(api, token, cid)).json()["_id"]
    await asyncio.sleep(0.05)
    assert (await api.client.delete(f"/curriculum/{cid}", headers=bearer(token))).status_code == 204
    gated.gate.set()
    await api.app.state.jobs.wait_idle()
    assert await UserCurriculum.get(ucid) is None  # the job must not re-create it
    assert await Curriculum.get(cid) is None
