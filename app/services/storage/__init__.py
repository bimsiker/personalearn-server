"""File storage behind a switch (STORAGE_BACKEND). Milestone 1 uses local disk.

Files are stored under random keys (never user-supplied names) and are only ever handed
out through short-lived signed links.
"""

import asyncio
import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

from app.core.config import Settings
from app.core.security import create_token

_KEY_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}/[a-f0-9]{32}\.[a-z0-9]{1,8}$")


class FileStorage(Protocol):
    backend: str

    async def save(self, data: bytes, *, folder: str, extension: str) -> str: ...

    async def delete(self, key: str) -> None: ...

    def download_url(self, key: str, *, filename: str, file_type: str, base_url: str) -> str: ...


class LocalFileStorage:
    """Development storage on the server's disk. Downloads go through /files/download."""

    backend = "local"

    def __init__(self, base_dir: str, settings: Settings) -> None:
        self.base_dir = Path(base_dir).resolve()
        self._settings = settings

    def path_for(self, key: str) -> Path:
        if not _KEY_PATTERN.match(key):
            raise ValueError("invalid storage key")
        path = (self.base_dir / key).resolve()
        if self.base_dir not in path.parents:
            raise ValueError("storage key escapes the upload folder")
        return path

    async def save(self, data: bytes, *, folder: str, extension: str) -> str:
        key = f"{folder}/{uuid.uuid4().hex}{extension}"
        path = self.path_for(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        return key

    async def delete(self, key: str) -> None:
        path = self.path_for(key)
        await asyncio.to_thread(path.unlink, True)  # missing_ok=True

    def download_url(self, key: str, *, filename: str, file_type: str, base_url: str) -> str:
        token, _ = create_token(
            subject=key,
            token_type="file",  # noqa: S106 - token kind, not a secret
            secret=self._settings.jwt_secret.get_secret_value(),
            ttl=timedelta(seconds=self._settings.file_link_ttl_seconds),
            extra={"fn": filename, "ft": file_type},
        )
        return f"{base_url.rstrip('/')}/files/download?{urlencode({'token': token})}"


def build_storage(settings: Settings) -> FileStorage:
    if settings.storage_backend == "local":
        return LocalFileStorage(settings.upload_dir, settings)
    raise RuntimeError(
        f"STORAGE_BACKEND={settings.storage_backend!r} is not available yet (arrives in "
        "Milestone 2). Use STORAGE_BACKEND=local for now."
    )
