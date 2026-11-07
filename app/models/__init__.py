"""Beanie document models. Add each new Document class to DOCUMENT_MODELS."""

from beanie import Document

from app.models.refresh_session import RefreshSession
from app.models.user import User

DOCUMENT_MODELS: list[type[Document]] = [User, RefreshSession]

__all__ = ["DOCUMENT_MODELS", "RefreshSession", "User"]
