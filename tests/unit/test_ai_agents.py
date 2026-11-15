"""AI agents: schema validation, one retry, limits from config, and the fake provider."""

import copy

import pytest

from app.models.user_curriculum import Preferences
from app.services.ai import build_ai_provider
from app.services.ai.agents import GenerationError, generate_lessons, generate_outline
from app.services.ai.fake import FakeAIProvider
from app.services.ai.schemas import LessonsRequest, Limits, ModuleOutline, OutlineRequest
from tests.conftest import make_settings

PREFS = Preferences(learning_style="visual", learning_pace="moderate", media_preference="text")
LIMITS = Limits(max_modules=2, max_lessons_per_module=3, max_quizzes_per_lesson=2)

QUIZ = {
    "question": "What is a cell?",
    "options": ["A unit of life", "A rock", "A planet", "A song"],
    "correct_index": 0,
    "explanation": "Cells are the basic unit of life.",
}
LESSON = {
    "lesson_title": "Cells",
    "duration": "3 minutes",
    "script": "Cells are...",
    "quizzes": [QUIZ],
}
OUTLINE = {
    "course_title": "Biology",
    "summary": "All about cells.",
    "learning_objectives": ["Describe a cell"],
    "key_takeaways": ["Cells matter"],
    "modules": [{"module_title": "Cells"}],
}


def outline_request(prefs: Preferences = PREFS, limits: Limits = LIMITS) -> OutlineRequest:
    return OutlineRequest("Intro to Biology", "Week 1: cells.", prefs, limits)


def lessons_request(
    prefs: Preferences = PREFS, limits: Limits = LIMITS, module_number: int = 1
) -> LessonsRequest:
    return LessonsRequest(
        "Intro to Biology",
        "Week 1: cells.",
        prefs,
        limits,
        course_title="Biology",
        module_number=module_number,
        module=ModuleOutline(module_title="Cells"),
    )


class ScriptedProvider:
    """Returns the queued responses in order and counts calls."""

    name = "scripted"

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def _next(self):
        self.calls += 1
        return copy.deepcopy(self.responses.pop(0))

    async def outline(self, request):
        return await self._next()

    async def lessons(self, request):
        return await self._next()


def with_quiz(**changes) -> dict:
    return {"lessons": [{**LESSON, "quizzes": [{**QUIZ, **changes}]}]}


# ============================================================ outline


async def test_valid_outline_passes_first_time():
    provider = ScriptedProvider(OUTLINE)
    outline = await generate_outline(provider, outline_request())
    assert outline.course_title == "Biology" and provider.calls == 1
    assert outline.source_type == "Curriculum"  # default filled in


async def test_outline_trimmed_to_max_modules():
    many = {**OUTLINE, "modules": [{"module_title": f"M{i}"} for i in range(5)]}
    outline = await generate_outline(ScriptedProvider(many), outline_request())
    assert [m.module_title for m in outline.modules] == ["M0", "M1"]


async def test_invalid_outline_retried_once_then_succeeds():
    provider = ScriptedProvider({"summary": "no title"}, OUTLINE)
    outline = await generate_outline(provider, outline_request())
    assert outline.course_title == "Biology" and provider.calls == 2


@pytest.mark.parametrize(
    "bad",
    [
        {**OUTLINE, "modules": []},
        {**OUTLINE, "course_title": "   "},
        "not even a dict",
        None,
    ],
)
async def test_invalid_outline_twice_fails_with_safe_message(bad):
    provider = ScriptedProvider(bad, bad)
    with pytest.raises(GenerationError) as info:
        await generate_outline(provider, outline_request())
    assert provider.calls == 2
    assert "try personalizing again" in info.value.message


# ============================================================ lessons


async def test_valid_lessons():
    result = await generate_lessons(ScriptedProvider({"lessons": [LESSON]}), lessons_request())
    assert result.lessons[0].quizzes[0].correct_index == 0


async def test_lessons_and_quizzes_trimmed_to_limits():
    lessons = [{**LESSON, "quizzes": [QUIZ] * 4} for _ in range(6)]
    result = await generate_lessons(ScriptedProvider({"lessons": lessons}), lessons_request())
    assert len(result.lessons) == 3
    assert all(len(lesson.quizzes) == 2 for lesson in result.lessons)


@pytest.mark.parametrize(
    "bad",
    [
        with_quiz(options=["A", "B", "C"]),  # wrong option count
        with_quiz(options=["A", "B", "C", "D", "E"]),
        with_quiz(correct_index=4),  # out of range
        with_quiz(correct_index=-1),
        with_quiz(options=["Same", "same", "C", "D"]),  # duplicate options
        with_quiz(options=["A", " ", "C", "D"]),  # blank option
        with_quiz(explanation=""),
        {"lessons": [{**LESSON, "quizzes": []}]},  # every lesson needs a quiz
        {"lessons": [{**LESSON, "script": ""}]},
        {"lessons": []},
        {"wrong_key": []},
    ],
)
async def test_invalid_lessons_rejected(bad):
    with pytest.raises(GenerationError):
        await generate_lessons(ScriptedProvider(bad, bad), lessons_request())


async def test_options_are_trimmed():
    result = await generate_lessons(
        ScriptedProvider(with_quiz(options=[" A ", "B", "C", "D"])), lessons_request()
    )
    assert result.lessons[0].quizzes[0].options[0] == "A"


# ============================================================ fake provider


async def test_fake_provider_builds_a_valid_course_within_limits():
    fake = FakeAIProvider()
    outline = await generate_outline(fake, outline_request())
    assert outline.course_title.startswith("Intro to Biology")
    assert len(outline.modules) == LIMITS.max_modules
    for number in range(1, len(outline.modules) + 1):
        lessons = await generate_lessons(fake, lessons_request(module_number=number))
        assert 1 <= len(lessons.lessons) <= LIMITS.max_lessons_per_module


async def test_fake_provider_respects_tight_limits_and_pace():
    tight = Limits(max_modules=1, max_lessons_per_module=1, max_quizzes_per_lesson=1)
    fast = Preferences(learning_style="audio", learning_pace="fast", media_preference="audio")
    fake = FakeAIProvider()
    assert len((await generate_outline(fake, outline_request(fast, tight))).modules) == 1
    lessons = await generate_lessons(fake, lessons_request(fast, tight))
    assert len(lessons.lessons) == 1 and lessons.lessons[0].duration == "2-3 minutes"


async def test_fake_quiz_answer_position_varies():
    fake = FakeAIProvider()
    positions = set()
    for number in (1, 2, 3):
        lessons = await generate_lessons(fake, lessons_request(module_number=number))
        positions |= {q.correct_index for lesson in lessons.lessons for q in lesson.quizzes}
    assert len(positions) > 1


async def test_fake_provider_fail_marker():
    prefs = PREFS.model_copy(update={"additional_preferences": "please #FAIL now"})
    with pytest.raises(RuntimeError):
        await FakeAIProvider().outline(outline_request(prefs))


def test_build_provider_switch():
    assert build_ai_provider(make_settings(ai_provider="fake")).name == "fake"
    with pytest.raises(RuntimeError, match="Milestone 2"):
        build_ai_provider(make_settings(ai_provider="gemini"))
