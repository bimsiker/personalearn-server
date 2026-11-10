"""Passwords, signed tokens (JWT), one-time codes and keyed hashes."""

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash

from app.core.clock import utcnow

TokenType = Literal["access", "refresh", "file"]
JWT_ALGORITHM = "HS256"

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
CLASS_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion

_hasher = PasswordHash.recommended()  # argon2id
_DUMMY_HASH = _hasher.hash("timing-equalizer-password-1")


# --- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password, password_hash)
    except Exception:  # malformed hash etc. -> treat as mismatch
        return False


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check, so unknown emails can't be detected by timing."""
    verify_password(password, _DUMMY_HASH)


def password_problem(password: str) -> str | None:
    """Return a user-facing reason the password is too weak, or None if it's acceptable."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    if not any(ch.isdigit() for ch in password):
        return "Password must include at least one number."
    return None


# --- codes, tokens, hashes ------------------------------------------------------


def generate_numeric_code(digits: int = 6) -> str:
    return f"{secrets.randbelow(10**digits):0{digits}d}"


def generate_url_token() -> str:
    return secrets.token_urlsafe(32)


def generate_class_code(length: int = 8) -> str:
    return "".join(secrets.choice(CLASS_CODE_ALPHABET) for _ in range(length))


def keyed_hash(value: str, secret: str) -> str:
    """HMAC-SHA256. Used to store codes/tokens so a database leak doesn't reveal them."""
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def hashes_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# --- JWT -------------------------------------------------------------------------


class TokenError(Exception):
    """Token is missing, malformed, expired, tampered with, or of the wrong type."""


def create_token(
    *,
    subject: str,
    token_type: TokenType,
    secret: str,
    ttl: timedelta,
    extra: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return (encoded token, jti)."""
    now = utcnow()
    jti = uuid.uuid4().hex
    payload = {
        "sub": subject,
        "typ": token_type,
        "jti": jti,
        "iat": now,
        "exp": now + ttl,
        **(extra or {}),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM), jti


def decode_token(token: str, *, secret: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "typ", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(type(exc).__name__) from exc
    if payload.get("typ") != expected_type:
        raise TokenError("wrong token type")
    return payload
