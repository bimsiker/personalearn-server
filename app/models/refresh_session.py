from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.core.clock import utcnow


class RefreshSession(Document):
    """One row per issued refresh token. Rotated on every refresh; MongoDB deletes
    rows automatically once they expire (TTL index)."""

    jti: str
    user_id: PydanticObjectId
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "refresh_sessions"
        indexes = [
            IndexModel([("jti", ASCENDING)], unique=True, name="jti_unique"),
            IndexModel([("user_id", ASCENDING)], name="user_id"),
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="expires_ttl"),
        ]
