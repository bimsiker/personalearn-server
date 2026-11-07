"""FastAPI application factory. Run with: python -m uvicorn app.main:app --reload"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api.routes import auth, health, users
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import add_request_logging
from app.core.rate_limit import RateLimiter
from app.services.email import build_email_sender

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "PersonaLearn API starting env=%s storage=%s ai=%s email=%s video=%s",
            settings.app_env,
            settings.storage_backend,
            settings.ai_provider,
            settings.email_backend,
            settings.video_provider,
        )
        await db.connect(settings)
        if await db.ping():
            logger.info("MongoDB reachable")
        else:
            logger.error("MongoDB is NOT reachable at startup; /health will report degraded")
        try:
            yield
        finally:
            await db.disconnect()
            logger.info("PersonaLearn API stopped")

    app = FastAPI(
        title="PersonaLearn API",
        version="0.1.0",
        description="Backend for PersonaLearn. Use **Authorize** with an `accessToken` "
        "from /users/login to call protected endpoints.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.email_sender = build_email_sender(settings)
    app.state.rate_limiter = RateLimiter(enabled=settings.rate_limit_enabled)

    # Order matters: the last middleware added runs first. CORS is outermost so even
    # 500 responses from the logging middleware carry CORS headers.
    add_request_logging(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(users.router)
    app.include_router(auth.router)
    return app


app = create_app()
