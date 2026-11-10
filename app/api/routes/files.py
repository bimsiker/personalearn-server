"""Signed, short-lived file downloads for the local storage backend.

The link itself is the credential (a browser tab can't send our Authorization header),
so it expires after FILE_LINK_TTL_SECONDS. The token travels in the query string, which
our request log never records. PDFs open in the browser; Word files download.
"""

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.core.config import Settings
from app.core.deps import get_settings_dep, get_storage
from app.core.errors import AppError
from app.core.security import TokenError, decode_token
from app.schemas.common import error_docs
from app.services.documents import CONTENT_TYPES
from app.services.storage import FileStorage, LocalFileStorage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["files"])


@router.get(
    "/download",
    response_class=FileResponse,
    summary="Open a file using a link from GET /curriculum/{id}/file",
    responses=error_docs(403, 404),
)
async def download(
    token: str = Query(min_length=10, max_length=2048),
    settings: Settings = Depends(get_settings_dep),
    storage: FileStorage = Depends(get_storage),
) -> FileResponse:
    expired = AppError(
        403, "LINK_EXPIRED", "This link has expired. Go back and open the file again."
    )
    try:
        payload = decode_token(
            token, secret=settings.jwt_secret.get_secret_value(), expected_type="file"
        )
    except TokenError as exc:
        logger.warning("File link rejected: %s", exc)
        raise expired from exc

    if not isinstance(storage, LocalFileStorage):
        raise AppError(404, "NOT_FOUND", "File not found.")
    try:
        path = storage.path_for(payload["sub"])
    except ValueError as exc:
        logger.warning("File link rejected: bad storage key")
        raise expired from exc
    if not path.is_file():
        raise AppError(404, "NOT_FOUND", "That file no longer exists.")

    file_type = payload.get("ft") if payload.get("ft") in CONTENT_TYPES else "pdf"
    return FileResponse(
        path,
        media_type=CONTENT_TYPES[file_type],
        filename=str(payload.get("fn") or "document"),
        content_disposition_type="inline" if file_type == "pdf" else "attachment",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
