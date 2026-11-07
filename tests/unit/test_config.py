import pytest
from pydantic import ValidationError

from app.core.config import MIN_JWT_SECRET_LENGTH, Settings

GOOD_SECRET = "s" * MIN_JWT_SECRET_LENGTH


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- positive paths ---------------------------------------------------------


def test_defaults_run_with_fake_providers():
    s = make(jwt_secret=GOOD_SECRET)
    assert (s.storage_backend, s.ai_provider, s.email_backend, s.video_provider) == (
        "local",
        "fake",
        "console",
        "fake",
    )


def test_empty_secret_in_development_gets_temporary_secret():
    s = make(app_env="development", jwt_secret="")
    assert len(s.jwt_secret.get_secret_value()) >= MIN_JWT_SECRET_LENGTH


def test_cors_origins_are_split_and_trimmed():
    s = make(jwt_secret=GOOD_SECRET, cors_origins=" http://a.test , http://b.test,")
    assert s.cors_origin_list == ["http://a.test", "http://b.test"]


def test_secrets_are_masked_in_repr():
    s = make(jwt_secret=GOOD_SECRET, mongo_uri="mongodb://user:hunter2@host")
    text = repr(s)
    assert GOOD_SECRET not in text
    assert "hunter2" not in text


# --- negative paths ---------------------------------------------------------


@pytest.mark.parametrize("env", ["staging", "production"])
def test_missing_secret_outside_dev_is_rejected(env):
    with pytest.raises(ValidationError, match="JWT_SECRET must be set"):
        make(app_env=env, jwt_secret="")


def test_short_secret_is_rejected():
    with pytest.raises(ValidationError, match="at least"):
        make(jwt_secret="too-short")


def test_wildcard_cors_is_rejected():
    with pytest.raises(ValidationError, match="explicit origins"):
        make(jwt_secret=GOOD_SECRET, cors_origins="*")


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_env", "prod"),
        ("ai_provider", "openai"),
        ("storage_backend", "s3"),
        ("max_modules", 0),
        ("jwt_access_ttl_minutes", 0),
    ],
)
def test_invalid_values_are_rejected(field, value):
    with pytest.raises(ValidationError):
        make(jwt_secret=GOOD_SECRET, **{field: value})
