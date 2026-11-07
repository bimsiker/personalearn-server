"""Shared fixtures.

* `client`      app with no database (health/CORS/error tests).
* `api`         app wired to a real MongoDB test database, cleaned before every test.

Database tests use TEST_MONGO_URI if set, otherwise MONGO_URI from .env, but always the
separate database `personalearn_test` (never your real data). If no database is
reachable, those tests are skipped rather than failed.
"""

import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import db
from app.core.config import Settings
from app.core.logging import configure_logging
from app.main import create_app
from app.models import DOCUMENT_MODELS
from app.services.email import EmailMessage

TEST_ORIGIN = "http://localhost:5173"
TEST_DB_NAME = "personalearn_test"
TEST_JWT_SECRET = "test-secret-" + "x" * 40
STRONG_PASSWORD = "Sunflower42"


def _quiet_test_logging() -> None:
    # Importing app.main configures logging from .env (files on). Tests must never write
    # to the developer's logs/ folder, so switch to console-only straight away.
    configure_logging(Settings(_env_file=None, jwt_secret=TEST_JWT_SECRET, log_to_file=False))


def make_settings(**overrides) -> Settings:
    # _env_file=None: never read the developer's .env for app behaviour in tests.
    values = {
        "app_env": "test",
        "jwt_secret": TEST_JWT_SECRET,
        "cors_origins": TEST_ORIGIN,
        "mongo_uri": "mongodb://localhost:1",
        "log_to_file": False,
        "rate_limit_enabled": False,
        "verification_resend_cooldown_seconds": 60,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


_quiet_test_logging()


# --- no-database app ----------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


async def _client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # ASGITransport does not run the lifespan, so the app never opens its own connection.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async for c in _client_for(app):
        yield c


# --- database-backed app ----------------------------------------------------------------


def _test_mongo_uri() -> str | None:
    if os.environ.get("TEST_MONGO_URI"):
        return os.environ["TEST_MONGO_URI"]
    try:
        return Settings().mongo_uri.get_secret_value()  # code reads .env; value never printed
    except Exception:
        return None


@pytest.fixture(scope="session")
async def mongo_settings() -> AsyncIterator[Settings]:
    uri = _test_mongo_uri()
    if not uri:
        pytest.skip("No MongoDB URI available for database tests")
    s = make_settings(mongo_uri=uri, mongo_db_name=TEST_DB_NAME, mongo_timeout_ms=8000)
    await db.connect(s)
    if not await db.ping():
        await db.disconnect()
        pytest.skip("MongoDB not reachable; skipping database tests")
    yield s
    await db.drop_test_database(TEST_DB_NAME)
    await db.disconnect()


class RecordingEmailSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.messages.append(message)

    def last_to(self, email: str) -> EmailMessage:
        matches = [m for m in self.messages if m.to == email]
        assert matches, f"no email sent to {email}"
        return matches[-1]

    def code_for(self, email: str) -> str:
        found = re.search(r"\b(\d{6})\b", self.last_to(email).text)
        assert found, "no 6-digit code in email"
        return found.group(1)

    def reset_token_for(self, email: str) -> str:
        found = re.search(r"token=([A-Za-z0-9_\-]+)", self.last_to(email).text)
        assert found, "no reset token in email"
        return found.group(1)


@dataclass
class Api:
    client: AsyncClient
    app: FastAPI
    outbox: RecordingEmailSender
    settings: Settings
    _counter: list[int] = field(default_factory=lambda: [0])

    def new_email(self) -> str:
        self._counter[0] += 1
        return f"user{self._counter[0]}@example.com"

    async def signup(self, email: str | None = None, role: str = "student", **extra) -> str:
        email = email or self.new_email()
        body = {"name": "Test User", "email": email, "password": STRONG_PASSWORD, "role": role}
        body.update(extra)
        res = await self.client.post("/users/signup", json=body)
        assert res.status_code == 201, res.text
        return email

    async def verified_user(self, role: str = "student") -> tuple[str, dict]:
        """Sign up + verify. Returns (email, auth response body)."""
        email = await self.signup(role=role)
        res = await self.client.post(
            "/users/verify-email", json={"email": email, "code": self.outbox.code_for(email)}
        )
        assert res.status_code == 200, res.text
        return email, res.json()


@pytest.fixture
async def api(mongo_settings: Settings) -> AsyncIterator[Api]:
    for model in DOCUMENT_MODELS:
        await model.delete_all()
    app = create_app(mongo_settings)
    outbox = RecordingEmailSender()
    app.state.email_sender = outbox
    async for c in _client_for(app):
        yield Api(client=c, app=app, outbox=outbox, settings=mongo_settings)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
