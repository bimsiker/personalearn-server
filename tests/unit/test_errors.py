import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.errors import AppError, register_error_handlers


class LoginBody(BaseModel):
    email: str
    password: str


@pytest.fixture
async def client():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise AppError(409, "EMAIL_TAKEN", "That email is already registered.")

    @app.post("/login")
    async def login(body: LoginBody):
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        yield http


async def test_app_error_uses_standard_shape(client):
    res = await client.get("/boom")
    assert res.status_code == 409
    assert res.json() == {"code": "EMAIL_TAKEN", "message": "That email is already registered."}


async def test_validation_error_shape_and_no_input_echo(client):
    res = await client.post("/login", json={"password": "SuperSecret123"})
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["loc"] == ["body", "email"]
    assert "SuperSecret123" not in res.text


async def test_unknown_route_returns_not_found_code(client):
    res = await client.get("/does-not-exist")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"


async def test_wrong_method_returns_405_code(client):
    res = await client.get("/login")
    assert res.status_code == 405
    assert res.json()["code"] == "METHOD_NOT_ALLOWED"
