"""Email sending behind a switch (EMAIL_BACKEND). Milestone 1 uses the console backend."""

import logging
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings
from app.core.logging import DEV_EMAIL_LOGGER


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Development only: prints the email to the server console (never to log files)."""

    def __init__(self) -> None:
        self._logger = logging.getLogger(DEV_EMAIL_LOGGER)

    async def send(self, message: EmailMessage) -> None:
        bar = "=" * 64
        self._logger.info(
            "\n%s\n DEV EMAIL (not actually sent)\n To:      %s\n Subject: %s\n%s\n%s\n%s",
            bar,
            message.to,
            message.subject,
            bar,
            message.text,
            bar,
        )


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "console":
        return ConsoleEmailSender()
    raise RuntimeError(
        f"EMAIL_BACKEND={settings.email_backend!r} is not available yet (arrives in Milestone 2). "
        "Use EMAIL_BACKEND=console for now."
    )
