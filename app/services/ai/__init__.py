"""Course generation behind a switch (AI_PROVIDER).

A provider only produces raw JSON-like dicts. `agents.py` validates them against
`schemas.py`, retries once on invalid output, and applies the size limits from config,
so a new provider (Gemini in Milestone 2) cannot bypass any of those checks.
"""

from typing import Any, Protocol

from app.core.config import Settings
from app.services.ai.schemas import LessonsRequest, OutlineRequest


class AIProvider(Protocol):
    name: str

    async def outline(self, request: OutlineRequest) -> dict[str, Any]:
        """Agent 1: course title, summary, objectives, takeaways and module list."""
        ...

    async def lessons(self, request: LessonsRequest) -> dict[str, Any]:
        """Agent 2: {"lessons": [...]} for one module, each with 1-3 quizzes."""
        ...


def build_ai_provider(settings: Settings) -> AIProvider:
    if settings.ai_provider == "fake":
        from app.services.ai.fake import FakeAIProvider

        return FakeAIProvider(delay_seconds=settings.fake_ai_delay_seconds)
    raise RuntimeError(
        f"AI_PROVIDER={settings.ai_provider!r} is not available yet (arrives in Milestone 2). "
        "Use AI_PROVIDER=fake for now."
    )
