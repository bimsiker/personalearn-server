"""In-process background jobs for slow work (AI generation now, video later).

Milestone 1 runs jobs as asyncio tasks inside the API process: simple, no Redis. A job
lost to a restart is marked failed at the next startup (see generation.recover_interrupted)
and the learner can retry. Milestone 5 swaps this for ARQ + Redis behind the same `submit`.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, name: str, job: Callable[[], Awaitable[None]]) -> None:
        async def _run() -> None:
            try:
                await job()
            except asyncio.CancelledError:
                logger.warning("Job cancelled job=%s", name)
                raise
            except Exception:
                # Jobs record their own failures; this is the last-resort safety net.
                logger.exception("Job crashed job=%s", name)

        task = asyncio.create_task(_run(), name=name)
        self._tasks.add(task)  # keep a reference so the task isn't garbage-collected
        task.add_done_callback(self._tasks.discard)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    async def wait_idle(self, timeout: float = 30) -> None:
        """Wait until every job (including ones submitted meanwhile) has finished."""
        async with asyncio.timeout(timeout):
            while self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
