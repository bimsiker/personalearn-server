"""Account lifecycle: signup, email verification, login, token rotation, password reset.

Logging rules: log user ids and masked emails only - never passwords, codes, or tokens.
"""

import logging
from datetime import timedelta

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import mask_email
from app.core.security import (
    TokenError,
    burn_password_check,
    create_token,
    decode_token,
    generate_class_code,
    generate_numeric_code,
    generate_url_token,
    hash_password,
    hashes_match,
    keyed_hash,
    verify_password,
)
from app.models.refresh_session import RefreshSession
from app.models.user import EmailInfo, PasswordReset, Role, User, VerificationCode
from app.schemas.auth import AuthResponse, LoginRequest, ResetPasswordRequest, SignupRequest
from app.schemas.user import UserOut
from app.services.email import EmailSender
from app.services.email.templates import password_reset_email, verification_email

logger = logging.getLogger(__name__)


def _email_taken() -> AppError:
    return AppError(409, "EMAIL_TAKEN", "An account with this email already exists.")


INVALID_CREDENTIALS_MSG = "Email or password is incorrect."
INVALID_CODE_MSG = "That code is invalid. Check the email and try again."
INVALID_RESET_MSG = "This reset link is invalid or has expired. Request a new one."
INVALID_SESSION_MSG = "Your session is invalid or has expired. Please log in again."


def _secret(settings: Settings) -> str:
    return settings.jwt_secret.get_secret_value()


async def find_user_by_email(email: str) -> User | None:
    return await User.find_one({"email.value": email})


# --- signup & verification ------------------------------------------------------


def _new_verification_code(user: User, settings: Settings) -> str:
    code = generate_numeric_code()
    now = utcnow()
    user.verification = VerificationCode(
        code_hash=keyed_hash(code, _secret(settings)),
        expires_at=now + timedelta(minutes=settings.verification_code_ttl_minutes),
        attempts=0,
        last_sent_at=now,
    )
    return code


async def _send_safely(sender: EmailSender, message, purpose: str, user: User) -> None:
    try:
        await sender.send(message)
    except Exception:
        # The account action already succeeded; the user can ask for another email.
        logger.exception("Failed to send %s email to user=%s", purpose, user.id)


async def _unique_class_code() -> str:
    for _ in range(10):
        code = generate_class_code()
        if not await User.find_one({"class_code": code}):
            return code
    raise RuntimeError("Could not generate a unique class code")


async def signup(data: SignupRequest, settings: Settings, sender: EmailSender) -> User:
    if await find_user_by_email(data.email):
        logger.warning("Signup rejected: email already registered (%s)", mask_email(data.email))
        raise _email_taken()

    role = Role(data.role)
    user = User(
        name=data.name,
        email=EmailInfo(value=data.email),
        password_hash=hash_password(data.password),
        role=role,
        class_code=await _unique_class_code() if role is Role.TEACHER else None,
    )
    code = _new_verification_code(user, settings)
    try:
        await user.insert()
    except DuplicateKeyError as exc:  # two signups raced for the same email
        raise _email_taken() from exc

    logger.info("User signed up user=%s role=%s email=%s", user.id, role, mask_email(data.email))
    await _send_safely(
        sender,
        verification_email(
            user.email.value, user.name, code, settings.verification_code_ttl_minutes
        ),
        "verification",
        user,
    )
    return user


async def verify_email(email: str, code: str, settings: Settings) -> User:
    invalid = AppError(400, "INVALID_CODE", INVALID_CODE_MSG)
    user = await find_user_by_email(email)
    if user is None or user.email.verified or user.verification is None:
        logger.warning("Verification rejected: no pending code (%s)", mask_email(email))
        raise invalid

    pending = user.verification
    if pending.attempts >= settings.verification_max_attempts:
        logger.warning("Verification blocked: too many attempts user=%s", user.id)
        raise AppError(429, "TOO_MANY_ATTEMPTS", "Too many incorrect attempts. Request a new code.")
    if utcnow() > pending.expires_at:
        logger.warning("Verification rejected: code expired user=%s", user.id)
        raise AppError(400, "CODE_EXPIRED", "That code has expired. Request a new one.")
    if not hashes_match(keyed_hash(code, _secret(settings)), pending.code_hash):
        pending.attempts += 1
        user.touch()
        await user.save()
        logger.warning(
            "Verification rejected: wrong code user=%s attempt=%d", user.id, pending.attempts
        )
        raise invalid

    user.email.verified = True
    user.verification = None
    user.touch()
    await user.save()
    logger.info("Email verified user=%s", user.id)
    return user


async def resend_verification(email: str, settings: Settings, sender: EmailSender) -> None:
    """Always 'succeeds' from the caller's view so it never reveals whether an email exists."""
    user = await find_user_by_email(email)
    if user is None or user.email.verified:
        logger.info("Resend ignored: no unverified account (%s)", mask_email(email))
        return
    pending = user.verification
    cooldown = timedelta(seconds=settings.verification_resend_cooldown_seconds)
    if pending and utcnow() - pending.last_sent_at < cooldown:
        logger.info("Resend skipped: cooldown active user=%s", user.id)
        return

    code = _new_verification_code(user, settings)
    user.touch()
    await user.save()
    logger.info("Verification code re-sent user=%s", user.id)
    await _send_safely(
        sender,
        verification_email(
            user.email.value, user.name, code, settings.verification_code_ttl_minutes
        ),
        "verification",
        user,
    )


# --- login & sessions --------------------------------------------------------------


async def login(data: LoginRequest) -> User:
    invalid = AppError(401, "INVALID_CREDENTIALS", INVALID_CREDENTIALS_MSG)
    user = await find_user_by_email(data.email)
    if user is None:
        burn_password_check(data.password)
        logger.warning("Login failed: unknown email (%s)", mask_email(data.email))
        raise invalid
    if not verify_password(data.password, user.password_hash):
        logger.warning("Login failed: wrong password user=%s", user.id)
        raise invalid
    if not user.email.verified:
        logger.warning("Login blocked: email not verified user=%s", user.id)
        raise AppError(403, "EMAIL_NOT_VERIFIED", "Please verify your email before logging in.")

    user.last_login = utcnow()
    await user.save()
    logger.info("Login succeeded user=%s role=%s", user.id, user.role)
    return user


async def issue_tokens(user: User, settings: Settings) -> AuthResponse:
    secret = _secret(settings)
    access_ttl = timedelta(minutes=settings.jwt_access_ttl_minutes)
    refresh_ttl = timedelta(days=settings.jwt_refresh_ttl_days)
    access, _ = create_token(
        subject=str(user.id),
        token_type="access",  # noqa: S106 - token kind, not a secret
        secret=secret,
        ttl=access_ttl,
        extra={"role": str(user.role)},
    )
    refresh, jti = create_token(
        subject=str(user.id),
        token_type="refresh",  # noqa: S106 - token kind, not a secret
        secret=secret,
        ttl=refresh_ttl,
    )
    await RefreshSession(jti=jti, user_id=user.id, expires_at=utcnow() + refresh_ttl).insert()
    return AuthResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=int(access_ttl.total_seconds()),
        user=UserOut.from_user(user),
    )


async def revoke_all_sessions(user_id: PydanticObjectId) -> None:
    await RefreshSession.find({"user_id": user_id, "revoked_at": None}).update(
        {"$set": {"revoked_at": utcnow()}}
    )


async def refresh_session(refresh_token: str, settings: Settings) -> AuthResponse:
    invalid = AppError(401, "INVALID_TOKEN", INVALID_SESSION_MSG)
    try:
        payload = decode_token(refresh_token, secret=_secret(settings), expected_type="refresh")
    except TokenError as exc:
        logger.warning("Refresh rejected: %s", exc)
        raise invalid from exc

    session = await RefreshSession.find_one({"jti": payload["jti"]})
    if session is None:
        logger.warning("Refresh rejected: unknown session")
        raise invalid
    if session.revoked_at is not None:
        # A rotated-out token was used again: likely stolen. Log out every device.
        await revoke_all_sessions(session.user_id)
        logger.warning(
            "Refresh token reuse detected; all sessions revoked user=%s", session.user_id
        )
        raise invalid

    user = await User.get(session.user_id)
    if user is None:
        raise invalid
    session.revoked_at = utcnow()
    await session.save()
    logger.info("Session refreshed user=%s", user.id)
    return await issue_tokens(user, settings)


async def logout(refresh_token: str, settings: Settings) -> None:
    """Idempotent: an unknown or invalid token is simply ignored."""
    try:
        payload = decode_token(refresh_token, secret=_secret(settings), expected_type="refresh")
    except TokenError:
        return
    session = await RefreshSession.find_one({"jti": payload["jti"]})
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
        await session.save()
        logger.info("Logged out user=%s", session.user_id)


# --- password reset ------------------------------------------------------------------


async def forgot_password(email: str, settings: Settings, sender: EmailSender) -> None:
    """Always 'succeeds' from the caller's view so it never reveals whether an email exists."""
    user = await find_user_by_email(email)
    if user is None:
        logger.info("Password reset requested for unknown email (%s)", mask_email(email))
        return
    token = generate_url_token()
    user.password_reset = PasswordReset(
        token_hash=keyed_hash(token, _secret(settings)),
        expires_at=utcnow() + timedelta(minutes=settings.password_reset_ttl_minutes),
    )
    user.touch()
    await user.save()
    logger.info("Password reset link issued user=%s", user.id)
    link = f"{settings.frontend_url.rstrip('/')}/reset-password?token={token}"
    await _send_safely(
        sender,
        password_reset_email(
            user.email.value, user.name, link, settings.password_reset_ttl_minutes
        ),
        "password reset",
        user,
    )


async def reset_password(data: ResetPasswordRequest, settings: Settings) -> None:
    invalid = AppError(400, "INVALID_TOKEN", INVALID_RESET_MSG)
    token_hash = keyed_hash(data.token, _secret(settings))
    user = await User.find_one({"password_reset.token_hash": token_hash})
    if user is None or user.password_reset is None:
        logger.warning("Password reset rejected: unknown token")
        raise invalid
    if utcnow() > user.password_reset.expires_at:
        logger.warning("Password reset rejected: link expired user=%s", user.id)
        raise invalid

    user.password_hash = hash_password(data.password)
    user.password_reset = None
    # Clicking the emailed link proves ownership of the address.
    user.email.verified = True
    user.verification = None
    user.touch()
    await user.save()
    await revoke_all_sessions(user.id)
    logger.info("Password reset completed; all sessions revoked user=%s", user.id)
