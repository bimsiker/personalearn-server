from fastapi import APIRouter, Depends, Response, status

from app.core.config import Settings
from app.core.deps import get_email_sender, get_settings_dep
from app.core.rate_limit import rate_limit
from app.schemas.auth import AuthResponse, EmailRequest, RefreshRequest, ResetPasswordRequest
from app.schemas.common import MessageResponse, error_docs
from app.services import auth
from app.services.email import EmailSender

router = APIRouter(prefix="/auth", tags=["users & auth"])


@router.post(
    "/refresh",
    response_model=AuthResponse,
    summary="Swap a refresh token for a new token pair (the old refresh token stops working)",
    responses=error_docs(401, 422, 429),
    dependencies=[rate_limit("refresh", limit=30, window_seconds=60)],
)
async def refresh(
    body: RefreshRequest, settings: Settings = Depends(get_settings_dep)
) -> AuthResponse:
    return await auth.refresh_session(body.refresh_token, settings)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="End this session (revokes the refresh token)",
    responses=error_docs(422),
)
async def logout(body: RefreshRequest, settings: Settings = Depends(get_settings_dep)) -> Response:
    await auth.logout(body.refresh_token, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Email a password reset link (same response whether or not the email exists)",
    responses=error_docs(422, 429),
    dependencies=[rate_limit("forgot-password", limit=5, window_seconds=600)],
)
async def forgot_password(
    body: EmailRequest,
    settings: Settings = Depends(get_settings_dep),
    sender: EmailSender = Depends(get_email_sender),
) -> MessageResponse:
    await auth.forgot_password(body.email, settings, sender)
    return MessageResponse(
        message="If an account exists for that email, we've sent a password reset link."
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Set a new password using the token from the reset link",
    responses=error_docs(400, 422, 429),
    dependencies=[rate_limit("reset-password", limit=10, window_seconds=600)],
)
async def reset_password(
    body: ResetPasswordRequest, settings: Settings = Depends(get_settings_dep)
) -> MessageResponse:
    await auth.reset_password(body, settings)
    return MessageResponse(message="Your password has been reset. You can now log in.")
