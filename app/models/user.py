from datetime import datetime
from enum import StrEnum

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel

from app.core.clock import utcnow


class Role(StrEnum):
    ADMIN = "admin"
    STUDENT = "student"
    PARENT = "parent"
    TEACHER = "teacher"


class EmailInfo(BaseModel):
    value: str  # always stored lowercase
    verified: bool = False


class VerificationCode(BaseModel):
    code_hash: str
    expires_at: datetime
    attempts: int = 0
    last_sent_at: datetime


class PasswordReset(BaseModel):
    token_hash: str
    expires_at: datetime


class User(Document):
    name: str
    email: EmailInfo
    password_hash: str
    role: Role
    class_code: str | None = None  # teachers only
    verification: VerificationCode | None = None
    password_reset: PasswordReset | None = None
    curricula_created: int = 0
    curricula_enrolled: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_login: datetime | None = None

    class Settings:
        name = "users"
        indexes = [
            IndexModel([("email.value", ASCENDING)], unique=True, name="email_unique"),
            IndexModel(
                [("class_code", ASCENDING)],
                unique=True,
                name="class_code_unique",
                partialFilterExpression={"class_code": {"$type": "string"}},
            ),
            IndexModel(
                [("password_reset.token_hash", ASCENDING)],
                name="password_reset_token",
                partialFilterExpression={"password_reset.token_hash": {"$type": "string"}},
            ),
        ]

    def touch(self) -> None:
        self.updated_at = utcnow()
