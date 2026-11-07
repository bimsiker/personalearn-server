import pytest

from app import db
from tests.conftest import TEST_ORIGIN


async def _up() -> bool:
    return True


async def _down() -> bool:
    return False


async def test_health_ok_when_database_answers(client, monkeypatch):
    monkeypatch.setattr(db, "ping", _up)
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "ok"}


async def test_health_503_when_database_unreachable(client, monkeypatch):
    monkeypatch.setattr(db, "ping", _down)
    res = await client.get("/health")
    assert res.status_code == 503
    assert res.json() == {"status": "degraded", "database": "unreachable"}


async def test_ping_without_connection_is_false(monkeypatch):
    # Not connected: must report False, not raise.
    monkeypatch.setattr(db, "_client", None)
    assert await db.ping() is False


async def test_cors_allows_configured_frontend(client, monkeypatch):
    monkeypatch.setattr(db, "ping", _up)
    res = await client.get("/health", headers={"Origin": TEST_ORIGIN})
    assert res.headers.get("access-control-allow-origin") == TEST_ORIGIN


async def test_cors_rejects_unknown_origin(client, monkeypatch):
    monkeypatch.setattr(db, "ping", _up)
    res = await client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in res.headers


async def test_openapi_docs_are_served(client):
    res = await client.get("/openapi.json")
    assert res.status_code == 200
    assert "/health" in res.json()["paths"]


# --- live: talks to the real database from .env --------------------------------


@pytest.mark.live
async def test_live_database_ping():
    from app.core.config import Settings

    settings = Settings()  # reads the real .env
    await db.connect(settings)
    try:
        assert await db.ping() is True
    finally:
        await db.disconnect()
