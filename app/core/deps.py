"""Shared FastAPI dependencies: settings, email sender, current user, role checks."""

import logging

from beanie import PydanticObjectId
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import TokenError, decode_token
from app.models.user import Role, User
from app.services.email import EmailSender
from app.services.storage import FileStorage

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(
    auto_error=False,
    description="Paste the `accessToken` returned by /users/login or /users/verify-email.",
)
_WWW_AUTH = {"WWW-Authenticate": "Bearer"}


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_email_sender(request: Request) -> EmailSender:
    return request.app.state.email_sender


def get_storage(request: Request) -> FileStorage:
    return request.app.state.storage


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    if credentials is None:
        raise AppError(401, "UNAUTHORIZED", "Please log in to continue.", headers=_WWW_AUTH)
    invalid = AppError(
        401, "INVALID_TOKEN", "Your session is invalid or has expired.", headers=_WWW_AUTH
    )
    try:
        payload = decode_token(
            credentials.credentials,
            secret=settings.jwt_secret.get_secret_value(),
            expected_type="access",
        )
        user_id = PydanticObjectId(payload["sub"])
    except (TokenError, ValueError, TypeError) as exc:
        logger.warning("Access token rejected: %s", exc)
        raise invalid from exc

    user = await User.get(user_id)
    if user is None:
        logger.warning("Access token rejected: user no longer exists user=%s", user_id)
        raise invalid
    request.state.user_id = str(user.id)  # shows up in the request log line
    return user


def require_role(*roles: Role):
    """Dependency factory: `user = Depends(require_role(Role.TEACHER))`."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            logger.warning("Forbidden: role=%s needs one of %s user=%s", user.role, roles, user.id)
            raise AppError(403, "FORBIDDEN", "You don't have access to this.")
        return user

    return _check
