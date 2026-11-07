from fastapi import APIRouter, Depends, status

from app.core.config import Settings
from app.core.deps import get_current_user, get_email_sender, get_settings_dep
from app.core.rate_limit import rate_limit
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    EmailRequest,
    LoginRequest,
    ResendResponse,
    SignupRequest,
    SignupResponse,
    VerifyEmailRequest,
)
from app.schemas.common import error_docs
from app.schemas.user import UserOut
from app.services import auth
from app.services.email import EmailSender

router = APIRouter(prefix="/users", tags=["users & auth"])


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=SignupResponse,
    summary="Create an account and email a 6-digit verification code",
    responses=error_docs(409, 422, 429),
    dependencies=[rate_limit("signup", limit=10, window_seconds=600)],
)
async def signup(
    body: SignupRequest,
    settings: Settings = Depends(get_settings_dep),
    sender: EmailSender = Depends(get_email_sender),
) -> SignupResponse:
    user = await auth.signup(body, settings, sender)
    return SignupResponse(
        message="Account created. Check your email for a 6-digit verification code.",
        email=user.email.value,
    )


@router.post(
    "/verify-email",
    response_model=AuthResponse,
    summary="Confirm the email code; returns login tokens",
    responses=error_docs(400, 422, 429),
    dependencies=[rate_limit("verify-email", limit=10, window_seconds=60)],
)
async def verify_email(
    body: VerifyEmailRequest, settings: Settings = Depends(get_settings_dep)
) -> AuthResponse:
    user = await auth.verify_email(body.email, body.code, settings)
    return await auth.issue_tokens(user, settings)


@router.post(
    "/resend-verification-code",
    response_model=ResendResponse,
    summary="Send a new verification code (same response whether or not the email exists)",
    responses=error_docs(422, 429),
    dependencies=[rate_limit("resend-code", limit=5, window_seconds=600)],
)
async def resend_verification_code(
    body: EmailRequest,
    settings: Settings = Depends(get_settings_dep),
    sender: EmailSender = Depends(get_email_sender),
) -> ResendResponse:
    await auth.resend_verification(body.email, settings, sender)
    return ResendResponse(
        message="If that account needs verifying, a new code is on its way.",
        retry_after_seconds=settings.verification_resend_cooldown_seconds,
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Log in with email and password",
    responses=error_docs(401, 403, 422, 429),
    dependencies=[rate_limit("login", limit=10, window_seconds=60)],
)
async def login(body: LoginRequest, settings: Settings = Depends(get_settings_dep)) -> AuthResponse:
    user = await auth.login(body)
    return await auth.issue_tokens(user, settings)


@router.get(
    "/me",
    response_model=UserOut,
    summary="The logged-in user's profile",
    responses=error_docs(401),
)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.from_user(user)
