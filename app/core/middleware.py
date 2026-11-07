"""Per-request logging: one line per request, plus a safe 500 for unexpected errors."""

import logging
import re
import uuid
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import request_id_var

logger = logging.getLogger("app.request")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-]{1,64}$")


def add_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        start = perf_counter()
        # Path only: query strings can carry tokens and must not reach the logs.
        method, path = request.method, request.url.path
        try:
            try:
                response = await call_next(request)
            except Exception:
                elapsed = (perf_counter() - start) * 1000
                logger.exception("%s %s -> 500 unhandled error (%.0f ms)", method, path, elapsed)
                response = JSONResponse(
                    status_code=500,
                    content={
                        "code": "INTERNAL_ERROR",
                        "message": "Something went wrong on our side. Please try again.",
                        "requestId": request_id,
                    },
                )
            else:
                elapsed = (perf_counter() - start) * 1000
                status = response.status_code
                level = (
                    logging.ERROR
                    if status >= 500
                    else logging.WARNING
                    if status >= 400
                    else logging.INFO
                )
                user = getattr(request.state, "user_id", None)
                suffix = f" user={user}" if user else ""
                logger.log(level, "%s %s -> %s (%.0f ms)%s", method, path, status, elapsed, suffix)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(token)
