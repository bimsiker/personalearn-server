from datetime import datetime

from pydantic import BaseModel, Field

from app.models.user import Role, User
from app.schemas.common import CamelModel


class EmailOut(BaseModel):
    value: str
    verified: bool


class UserOut(CamelModel):
    """Mirrors the frontend's `User` type (src/types/user.ts). Never includes secrets."""

    id: str = Field(alias="_id")
    name: str
    email: EmailOut
    role: Role
    curricula_created: int
    curricula_enrolled: int
    class_code: str | None = None
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserOut":
        return cls(
            id=str(user.id),
            name=user.name,
            email=EmailOut(value=user.email.value, verified=user.email.verified),
            role=user.role,
            curricula_created=user.curricula_created,
            curricula_enrolled=user.curricula_enrolled,
            class_code=user.class_code,
            created_at=user.created_at,
        )
