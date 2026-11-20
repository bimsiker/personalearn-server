"""Run the AI agents safely: validate every response, retry once, enforce config limits."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.services.ai import AIProvider
from app.services.ai.schemas import (
    CourseOutline,
    LessonsRequest,
    ModuleLessons,
    OutlineRequest,
    TutorReply,
    TutorRequest,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # the first try plus one retry


class GenerationError(Exception):
    """Generation could not produce a valid course. `message` is safe to show the learner."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def _validated[ModelT: BaseModel](
    step: str, call: Callable[[], Awaitable[Any]], model: type[ModelT]
) -> ModelT:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = await call()
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            # Log the shape of the problem only; never the content (it may echo the upload).
            problems = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors()][:5]
            logger.warning(
                "AI returned invalid %s (attempt %d/%d): %s", step, attempt, MAX_ATTEMPTS, problems
            )
    raise GenerationError("The AI returned an incomplete course. Please try personalizing again.")


async def generate_outline(provider: AIProvider, request: OutlineRequest) -> CourseOutline:
    outline = await _validated("outline", lambda: provider.outline(request), CourseOutline)
    if len(outline.modules) > request.limits.max_modules:
        logger.info(
            "Trimming outline from %d to %d modules",
            len(outline.modules),
            request.limits.max_modules,
        )
        outline.modules = outline.modules[: request.limits.max_modules]
    return outline


async def generate_lessons(provider: AIProvider, request: LessonsRequest) -> ModuleLessons:
    result = await _validated(
        f"lessons for module {request.module_number}",
        lambda: provider.lessons(request),
        ModuleLessons,
    )
    result.lessons = result.lessons[: request.limits.max_lessons_per_module]
    for lesson in result.lessons:
        lesson.quizzes = lesson.quizzes[: request.limits.max_quizzes_per_lesson]
    return result


async def explain_answer(provider: AIProvider, request: TutorRequest) -> str:
    reply = await _validated("tutor reply", lambda: provider.tutor(request), TutorReply)
    return reply.explanation
