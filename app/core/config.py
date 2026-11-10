"""Application settings, loaded from environment variables and the `.env` file.

Values here may be secrets: never log a Settings object or any of its secret fields.
"""

import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: Literal["development", "test", "staging", "production"] = "development"
    cors_origins: str = "http://localhost:5173"
    frontend_url: str = "http://localhost:5173"

    # --- MongoDB ---
    mongo_uri: SecretStr = SecretStr("mongodb://localhost:27017")
    mongo_db_name: str = "personalearn"
    mongo_timeout_ms: int = Field(default=5000, ge=500)

    # --- Auth ---
    jwt_secret: SecretStr = SecretStr("")
    jwt_access_ttl_minutes: int = Field(default=15, ge=1)
    jwt_refresh_ttl_days: int = Field(default=7, ge=1)

    # --- Verification / reset ---
    verification_code_ttl_minutes: int = Field(default=10, ge=1)
    verification_max_attempts: int = Field(default=5, ge=1)
    verification_resend_cooldown_seconds: int = Field(default=60, ge=0)
    password_reset_ttl_minutes: int = Field(default=30, ge=1)

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: str = "logs"
    log_to_file: bool = True

    # --- Abuse protection ---
    rate_limit_enabled: bool = True

    # --- Uploads & files ---
    upload_dir: str = "uploads"  # used when STORAGE_BACKEND=local
    max_upload_mb: int = Field(default=25, ge=1, le=100)  # matches the frontend's limit
    file_link_ttl_seconds: int = Field(default=300, ge=30)  # "View PDF" links expire
    max_extracted_chars: int = Field(default=500_000, ge=1000)  # keeps documents < 16 MB

    # --- Provider switches ---
    storage_backend: Literal["local", "cloudinary"] = "local"
    ai_provider: Literal["fake", "gemini"] = "fake"
    email_backend: Literal["console", "resend"] = "console"
    video_provider: Literal["fake", "heygen"] = "fake"

    # --- AI generation limits ---
    max_modules: int = Field(default=2, ge=1)
    max_lessons_per_module: int = Field(default=3, ge=1)

    @property
    def is_dev_like(self) -> bool:
        return self.app_env in ("development", "test")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1_000_000

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _check_secrets(self) -> "Settings":
        secret = self.jwt_secret.get_secret_value()
        if not secret:
            if not self.is_dev_like:
                raise ValueError("JWT_SECRET must be set outside development/test.")
            # Throwaway secret so local dev still runs; tokens won't survive a restart.
            self.jwt_secret = SecretStr(secrets.token_urlsafe(48))
            logger.warning("JWT_SECRET is empty; using a temporary secret for this run.")
        elif len(secret) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters.")

        if "*" in self.cors_origin_list:
            raise ValueError("CORS_ORIGINS must list explicit origins, not '*'.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
