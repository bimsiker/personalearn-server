"""MongoDB connection (PyMongo async client) and Beanie initialisation."""

import logging

from beanie import init_beanie
from pymongo import AsyncMongoClient

from app.core.config import Settings
from app.models import DOCUMENT_MODELS

logger = logging.getLogger(__name__)

_client: AsyncMongoClient | None = None


async def connect(settings: Settings) -> None:
    """Create the client and register Beanie models. Safe to call once at startup."""
    global _client
    _client = AsyncMongoClient(
        settings.mongo_uri.get_secret_value(),
        serverSelectionTimeoutMS=settings.mongo_timeout_ms,
        appname="personalearn-server",
        tz_aware=True,
    )
    await init_beanie(
        database=_client[settings.mongo_db_name],
        document_models=DOCUMENT_MODELS,
    )
    logger.info("MongoDB client ready (database=%s)", settings.mongo_db_name)


async def disconnect() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def drop_test_database(name: str) -> None:
    """Tests only. Refuses any database whose name doesn't end in '_test'."""
    if not name.endswith("_test"):
        raise ValueError(f"Refusing to drop non-test database {name!r}")
    if _client is not None:
        await _client.drop_database(name)


async def ping() -> bool:
    """Return True if MongoDB answers a ping. Never raises; never logs the URI."""
    if _client is None:
        return False
    try:
        await _client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - any failure means "unreachable"
        logger.warning("MongoDB ping failed: %s", type(exc).__name__)
        return False
    return True
