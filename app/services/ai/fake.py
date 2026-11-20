"""AI_PROVIDER=fake: a fixed sample course, so the whole learning loop runs with no API key.

The content is generic study material wrapped around the upload's title; it does not read
the document. Pace changes lesson length and count, like the real agents will.

Testing hook: put "#fail" in "additional preferences" to make generation fail, so the
failed state and retry can be checked by hand (fake provider only).
"""

import asyncio
from typing import Any

from app.services.ai.schemas import LessonsRequest, OutlineRequest, TutorRequest

FAIL_MARKER = "#fail"

_DURATION = {"slow": "5-6 minutes", "moderate": "3-4 minutes", "fast": "2-3 minutes"}
_LESSONS_FOR_PACE = {"slow": 3, "moderate": 3, "fast": 2}
_STYLE_TIP = {
    "visual": "Sketch a quick diagram of the idea as you read.",
    "audio": "Read the key sentence aloud and explain it back in your own words.",
    "reading": "Write a two-line summary in your notes before moving on.",
    "kinesthetic": "Try the idea on a real example from your own work or life.",
}

_MODULES = [
    {
        "title": "Getting Started with {topic}",
        "summary": "What {topic} covers, why it matters, and the vocabulary you will need.",
        "lessons": [
            ("What {topic} Is About", "the big picture of {topic}"),
            ("Key Terms You Will Use", "the core vocabulary of {topic}"),
            ("How the Course Fits Together", "how the parts of {topic} connect"),
        ],
    },
    {
        "title": "Core Ideas of {topic}",
        "summary": "The central concepts of {topic}, one at a time, with worked examples.",
        "lessons": [
            ("The First Core Idea", "the first central concept in {topic}"),
            ("Seeing It in Practice", "a worked example from {topic}"),
            ("Common Mistakes to Avoid", "misconceptions learners often have about {topic}"),
        ],
    },
    {
        "title": "Applying {topic}",
        "summary": "Put {topic} to work on realistic problems and review what you learned.",
        "lessons": [
            ("Solving a Real Problem", "applying {topic} to a realistic task"),
            ("Connecting the Ideas", "how the ideas in {topic} work together"),
            ("Review and Next Steps", "a recap of {topic} and where to go next"),
        ],
    },
]


def _topic(request: OutlineRequest | LessonsRequest) -> str:
    return request.source_title.strip() or "This Course"


class FakeAIProvider:
    name = "fake"

    def __init__(self, delay_seconds: float = 0) -> None:
        self.delay_seconds = delay_seconds

    async def _think(self) -> None:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

    @staticmethod
    def _check_fail(request: OutlineRequest | LessonsRequest) -> None:
        extra = request.preferences.additional_preferences or ""
        if FAIL_MARKER in extra.lower():
            raise RuntimeError("Fake AI failure requested with #fail")

    async def outline(self, request: OutlineRequest) -> dict[str, Any]:
        await self._think()
        self._check_fail(request)
        topic = _topic(request)
        pace = request.preferences.learning_pace
        modules = _MODULES[: request.limits.max_modules]
        return {
            "course_title": f"{topic}: Your Personalized Course",
            "summary": (
                f"A {pace}-paced course on {topic}, shaped around how you like to learn. "
                "Each short lesson ends with a quick quiz so you can check your understanding."
            ),
            "learning_objectives": [
                f"Explain the main ideas of {topic} in your own words",
                f"Use the key terms of {topic} correctly",
                f"Apply {topic} to a realistic problem",
            ],
            "key_takeaways": [
                f"{topic} builds from a few core ideas",
                "Short, regular practice beats long cramming sessions",
                "Checking yourself with quizzes shows what to review",
            ],
            "source_type": "Curriculum",
            "estimated_total_duration": f"{len(modules) * 15} minutes",
            "modules": [
                {
                    "module_title": m["title"].format(topic=topic),
                    "summary": m["summary"].format(topic=topic),
                    "recommended_duration": "15 minutes",
                }
                for m in modules
            ],
        }

    async def lessons(self, request: LessonsRequest) -> dict[str, Any]:
        await self._think()
        self._check_fail(request)
        topic = _topic(request)
        prefs = request.preferences
        template = _MODULES[(request.module_number - 1) % len(_MODULES)]
        count = min(_LESSONS_FOR_PACE[prefs.learning_pace], request.limits.max_lessons_per_module)
        lessons = []
        for number, (title, focus) in enumerate(template["lessons"][:count]):
            focus = focus.format(topic=topic)
            lessons.append(
                {
                    "lesson_title": title.format(topic=topic),
                    "duration": _DURATION[prefs.learning_pace],
                    "script": _script(focus, request.module.module_title, prefs.learning_style),
                    "quizzes": [_quiz(focus, request.module_number + number)],
                }
            )
        return {"lessons": lessons}

    async def tutor(self, request: TutorRequest) -> dict[str, Any]:
        await self._think()
        chosen = request.options[request.selected_index]
        if request.selected_index == request.correct_index:
            text = f'Yes: "{chosen}" is right. {_STYLE_TIP[request.preferences.learning_style]}'
        elif request.may_reveal:
            answer = request.options[request.correct_index]
            text = (
                f'"{chosen}" isn\'t the best choice here. The answer is "{answer}": linking '
                "new ideas to what you already know makes them stick."
            )
        else:
            text = (
                f'"{chosen}" isn\'t quite right. Look again at the part of '
                f'"{request.lesson_title}" about connecting new ideas to what you already '
                "know, then try again."
            )
        return {"explanation": text}


def _script(focus: str, module_title: str, style: str) -> str:
    return "\n\n".join(
        [
            f'Welcome to this lesson in "{module_title}". Here we focus on {focus}.',
            "Start by asking yourself what you already know about this. Even a rough guess "
            "gives your brain something to attach the new ideas to.",
            f"The main point is simple: {focus} is easier to remember when you connect it to "
            "something familiar. Read the idea once, then put it in your own words.",
            f"Tip for you: {_STYLE_TIP[style]}",
            "When you are ready, take the short quiz below to check your understanding.",
        ]
    )


def _quiz(focus: str, position: int) -> dict[str, Any]:
    """The correct answer moves between positions so scoring can't pass by always picking A."""
    correct = "Connect it to something you already know"
    wrong = [
        "Memorize every detail word for word",
        "Skip ahead to the final review",
        "Read it once and move on without checking",
    ]
    index = position % 4
    return {
        "question": f"What is the best first step when learning {focus}?",
        "options": [*wrong[:index], correct, *wrong[index:]],
        "correct_index": index,
        "explanation": (
            "Linking new ideas to what you already know makes them easier to understand and "
            "remember. Memorizing without understanding fades quickly."
        ),
    }
