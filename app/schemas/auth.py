from typing import Literal

from pydantic import ConfigDict, EmailStr, Field, field_validator

from app.core.security import PASSWORD_MAX_LENGTH, password_problem
from app.schemas.common import CamelModel
from app.schemas.user import UserOut

SignupRole = Literal["student", "parent", "teacher"]  # "admin" can never be self-assigned


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _check_password(value: str) -> str:
    problem = password_problem(value)
    if problem:
        raise ValueError(problem)
    return value


class _EmailField(CamelModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return _normalize_email(value)


# --- requests --------------------------------------------------------------------


class SignupRequest(_EmailField):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "password": "Sunflower42",
                "role": "student",
            }
        }
    )

    name: str = Field(min_length=1, max_length=100)
    password: str
    role: SignupRole

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Name is required.")
        return value

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return _check_password(value)


class VerifyEmailRequest(_EmailField):
    model_config = ConfigDict(
        json_schema_extra={"example": {"email": "ada@example.com", "code": "123456"}}
    )

    code: str = Field(pattern=r"^\d{6}$", description="The 6-digit code from the email")


class EmailRequest(_EmailField):
    model_config = ConfigDict(json_schema_extra={"example": {"email": "ada@example.com"}})


class LoginRequest(_EmailField):
    model_config = ConfigDict(
        json_schema_extra={"example": {"email": "ada@example.com", "password": "Sunflower42"}}
    )

    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class RefreshRequest(CamelModel):
    refresh_token: str = Field(min_length=1, max_length=2048)


class ResetPasswordRequest(CamelModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"token": "paste-the-token-from-the-reset-link", "password": "NewPassword99"}
        }
    )

    token: str = Field(min_length=16, max_length=256)
    password: str

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return _check_password(value)


# --- responses -------------------------------------------------------------------


class SignupResponse(CamelModel):
    message: str
    email: str


class ResendResponse(CamelModel):
    message: str
    retry_after_seconds: int


class AuthResponse(CamelModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - not a secret
    expires_in: int = Field(description="Access token lifetime in seconds")
    user: UserOut
