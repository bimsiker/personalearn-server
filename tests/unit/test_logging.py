import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.errors import AppError, register_error_handlers
from app.core.logging import configure_logging, mask_email, redact
from app.core.middleware import add_request_logging

# --- redaction ---------------------------------------------------------------------------


def test_redacts_mongodb_credentials():
    line = "connect mongodb+srv://someuser:S3cret!@cluster0.example.mongodb.net/?retryWrites=true"
    out = redact(line)
    assert "S3cret" not in out and "someuser" not in out
    assert "mongodb+srv://***:***@cluster0.example.mongodb.net" in out


def test_redacts_bearer_and_jwt():
    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    assert "c2lnbmF0dXJl" not in redact(f"Authorization: Bearer {jwt_like}")
    assert redact(f"token was {jwt_like}") == "token was <jwt>"


def test_plain_text_untouched():
    assert redact("User signed up user=123 role=student") == "User signed up user=123 role=student"


@pytest.mark.parametrize(
    "email,masked",
    [("jane.doe@example.com", "j***@example.com"), ("a@b.co", "a***@b.co"), ("bad", "***")],
)
def test_mask_email(email, masked):
    assert mask_email(email) == masked


# --- request logging middleware -----------------------------------------------------------


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    add_request_logging(app)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/teapot")
    async def teapot():
        raise AppError(409, "CONFLICT", "nope")

    @app.get("/crash")
    async def crash():
        raise RuntimeError("database password is hunter2")

    return app


@pytest.fixture
async def http():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        yield c


async def test_request_id_generated_and_returned(http):
    res = await http.get("/ok")
    assert len(res.headers["X-Request-ID"]) == 12


async def test_valid_incoming_request_id_is_kept(http):
    res = await http.get("/ok", headers={"X-Request-ID": "abc-123"})
    assert res.headers["X-Request-ID"] == "abc-123"


async def test_malicious_incoming_request_id_is_replaced(http):
    res = await http.get("/ok", headers={"X-Request-ID": "bad id\nINJECTED"})
    assert res.headers["X-Request-ID"] != "bad id\nINJECTED"


async def test_success_logged_at_info(http, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        await http.get("/ok?token=SECRET_IN_QUERY")
    record = next(r for r in caplog.records if r.name == "app.request")
    assert record.levelno == logging.INFO
    assert "GET /ok -> 200" in record.getMessage()
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("app")]
    assert not any("SECRET_IN_QUERY" in m for m in ours)  # query strings are never logged


def test_http_client_libraries_are_quieted():
    configure_logging(Settings(_env_file=None, jwt_secret="x" * 40, log_to_file=False))
    # httpx/httpcore log full URLs (query strings included) at INFO; keep them at WARNING.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


async def test_client_error_logged_at_warning(http, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        await http.get("/teapot")
    record = next(r for r in caplog.records if r.name == "app.request")
    assert record.levelno == logging.WARNING


async def test_unhandled_error_returns_safe_500_and_logs_traceback(http, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        res = await http.get("/crash")
    assert res.status_code == 500
    body = res.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["requestId"] == res.headers["X-Request-ID"]
    assert "hunter2" not in res.text  # internals never reach the client
    record = next(r for r in caplog.records if r.name == "app.request")
    assert record.levelno == logging.ERROR and record.exc_info is not None


# --- log files ------------------------------------------------------------------------------


async def test_log_files_written_and_split_by_level(tmp_path):
    settings = Settings(
        _env_file=None, jwt_secret="x" * 40, log_dir=str(tmp_path), log_to_file=True
    )
    configure_logging(settings)
    try:
        log = logging.getLogger("app.test")
        log.info("all good here")
        log.warning("something odd mongodb://admin:pw123@host")
        log.error("something broke")
        for handler in logging.getLogger().handlers:
            handler.flush()
        app_log = (tmp_path / "app.log").read_text(encoding="utf-8")
        err_log = (tmp_path / "errors.log").read_text(encoding="utf-8")
    finally:
        configure_logging(Settings(_env_file=None, jwt_secret="x" * 40, log_to_file=False))

    assert "all good here" in app_log and "something broke" in app_log
    assert "all good here" not in err_log
    assert "something odd" in err_log and "something broke" in err_log
    assert "pw123" not in app_log and "pw123" not in err_log
    assert "| INFO    |" in app_log  # format: time | level | request id | logger | message
