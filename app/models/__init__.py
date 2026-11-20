"""Beanie document models. Add each new Document class to DOCUMENT_MODELS."""

from beanie import Document

from app.models.curriculum import Curriculum
from app.models.quiz_attempt import QuizAttempt
from app.models.refresh_session import RefreshSession
from app.models.user import User
from app.models.user_curriculum import UserCurriculum

DOCUMENT_MODELS: list[type[Document]] = [
    User,
    RefreshSession,
    Curriculum,
    UserCurriculum,
    QuizAttempt,
]

__all__ = [
    "DOCUMENT_MODELS",
    "Curriculum",
    "QuizAttempt",
    "RefreshSession",
    "User",
    "UserCurriculum",
]
