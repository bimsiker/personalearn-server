from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """JSON in/out uses camelCase (matches the frontend); Python code uses snake_case."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Any | None = None


class MessageResponse(CamelModel):
    message: str


def error_docs(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Swagger documentation for the error responses an endpoint can return."""
    return {status: {"model": ErrorResponse} for status in statuses}
