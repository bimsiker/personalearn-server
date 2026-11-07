"""Simple in-memory sliding-window rate limiter, keyed by route scope + client IP.

Per-process only: fine for one server instance. Milestone 5 moves this to Redis.
"""

import logging
import math
from collections import defaultdict, deque
from time import monotonic

from fastapi import Depends, Request

from app.core.errors import AppError

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_seconds: float, now: float | None = None) -> float:
        """Record a hit. Return 0 if allowed, else seconds until the next hit is allowed."""
        if not self.enabled:
            return 0.0
        now = monotonic() if now is None else now
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return max(hits[0] + window_seconds - now, 0.001)
        hits.append(now)
        return 0.0


def rate_limit(scope: str, limit: int, window_seconds: int):
    """FastAPI dependency: allow `limit` requests per `window_seconds` per client IP."""

    async def _check(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        client_ip = request.client.host if request.client else "unknown"
        retry_after = limiter.hit(f"{scope}:{client_ip}", limit, window_seconds)
        if retry_after:
            logger.warning("Rate limit hit scope=%s ip=%s", scope, client_ip)
            raise AppError(
                429,
                "RATE_LIMITED",
                "Too many requests. Please wait a moment and try again.",
                headers={"Retry-After": str(math.ceil(retry_after))},
            )

    return Depends(_check)
