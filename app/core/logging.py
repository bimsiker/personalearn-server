"""Logging setup: console + rotating files, request IDs on every line, secrets redacted.

Files (in LOG_DIR, default ./logs):
  app.log     everything at LOG_LEVEL and above (requests, successes, warnings, errors)
  errors.log  warnings and errors only, for a quick "what went wrong" scan

Both rotate at 5 MB and keep 5 old files. Development emails (verification codes,
reset links) go to the console only, never to these files.
"""

import logging
import re
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import Settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

DEV_EMAIL_LOGGER = "personalearn.dev_email"
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(request_id)s | %(name)s | %(message)s"
_MARKER = "_personalearn_handler"

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # credentials inside MongoDB connection strings
    (re.compile(r"(mongodb(?:\+srv)?://)[^@\s/]+@"), r"\1***:***@"),
    # Authorization headers
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), r"\1***"),
    # anything shaped like a JWT
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<jwt>"),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def mask_email(email: str) -> str:
    """'jane.doe@example.com' -> 'j***@example.com' (enough to correlate, not to identify)."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class RedactingFormatter(logging.Formatter):
    """Redacts the fully formatted line, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def _handler(handler: logging.Handler, level: int, formatter: logging.Formatter) -> logging.Handler:
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(RequestContextFilter())
    setattr(handler, _MARKER, True)
    return handler


def configure_logging(settings: Settings) -> None:
    """Idempotent: replaces only handlers this module installed (pytest's stay intact)."""
    level = getattr(logging, settings.log_level)
    formatter = RedactingFormatter(LOG_FORMAT)

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _MARKER, False):
            root.removeHandler(existing)
            existing.close()
    root.setLevel(level)

    root.addHandler(_handler(logging.StreamHandler(sys.stderr), level, formatter))

    if settings.log_to_file:
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        for filename, file_level in (("app.log", level), ("errors.log", logging.WARNING)):
            file_handler = RotatingFileHandler(
                log_dir / filename, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
            )
            root.addHandler(_handler(file_handler, max(level, file_level), formatter))

    # Uvicorn: route its server messages through our handlers; our middleware is the
    # access log, so silence uvicorn's own per-request lines.
    for name in ("uvicorn", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False

    # Third-party libraries: pymongo is chatty; httpx/httpcore log full URLs (with query
    # strings that may hold tokens or API keys) at INFO.
    for noisy in ("pymongo", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Development emails: console only, so codes/links never land in log files.
    dev_email = logging.getLogger(DEV_EMAIL_LOGGER)
    for existing in list(dev_email.handlers):
        dev_email.removeHandler(existing)
    dev_email.addHandler(
        _handler(logging.StreamHandler(sys.stdout), logging.INFO, logging.Formatter("%(message)s"))
    )
    dev_email.setLevel(logging.INFO)
    dev_email.propagate = False
