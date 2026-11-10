"""Curriculum uploads: validate the document (PDF/Word), extract text, store, manage library."""

import asyncio
import hashlib
import logging
import re
import unicodedata

from beanie import PydanticObjectId
from fastapi import UploadFile

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AppError
from app.models.curriculum import Curriculum, CurriculumMetadata, StoredFile
from app.models.user import User
from app.services.documents import (
    CONTENT_TYPES,
    EXTENSIONS,
    DocumentError,
    extract_document,
    extract_metadata,
)
from app.services.storage import FileStorage

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024
LIST_PROJECTION = {"extracted_text": 0}  # list views never load the (large) text


def _not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "We couldn't find that material.")


def safe_filename(name: str | None, extension: str = ".pdf") -> str:
    """Display-only name: basename, no control/path characters, sensible length, and an
    extension matching the *detected* type (a .docx renamed to .pdf is shown as .docx)."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "", name).strip(" .")
    stem = re.sub(r"\.(pdf|docx|docm|doc)$", "", name, flags=re.IGNORECASE).strip(" .")
    stem = stem or "document"
    return stem[-(150 - len(extension)) :] + extension


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise AppError(
                413, "FILE_TOO_LARGE", f"That file is larger than {max_bytes // 1_000_000} MB."
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def create_curriculum(
    *,
    owner: User,
    title: str,
    description: str,
    upload: UploadFile,
    settings: Settings,
    storage: FileStorage,
) -> Curriculum:
    data = await _read_limited(upload, settings.max_upload_bytes)
    if not data:
        raise AppError(422, "EMPTY_FILE", "That file is empty.")

    try:
        extracted = await asyncio.to_thread(
            extract_document, data, max_chars=settings.max_extracted_chars
        )
    except DocumentError as exc:
        logger.warning("Upload rejected (%s) user=%s", exc.code, owner.id)
        raise AppError(422, exc.code, exc.message) from exc

    extension = EXTENSIONS[extracted.file_type]
    filename = safe_filename(upload.filename, extension)
    key = await storage.save(data, folder="curricula", extension=extension)
    doc = Curriculum(
        title=title,
        description=description,
        file=StoredFile(
            backend=storage.backend,
            key=key,
            size_bytes=len(data),
            file_type=extracted.file_type,
            content_type=CONTENT_TYPES[extracted.file_type],
            original_filename=filename,
            sha256=hashlib.sha256(data).hexdigest(),
        ),
        extracted_text=extracted.text,
        text_truncated=extracted.truncated,
        page_count=extracted.page_count,
        metadata=CurriculumMetadata(**extract_metadata(extracted.text)),
        created_by=owner.id,
    )
    try:
        await doc.insert()
    except Exception:
        await storage.delete(key)  # don't leave an orphaned file behind
        raise
    await User.find_one({"_id": owner.id}).update({"$inc": {"curricula_created": 1}})

    if not extracted.text:
        logger.warning("Upload has no readable text (scanned/image-only?) curriculum=%s", doc.id)
    logger.info(
        "Curriculum uploaded curriculum=%s user=%s type=%s pages=%s size=%dKB truncated=%s",
        doc.id,
        owner.id,
        extracted.file_type,
        extracted.page_count,
        len(data) // 1024,
        extracted.truncated,
    )
    return doc


async def list_curricula(owner: User, *, limit: int) -> list[Curriculum]:
    # Milestone 3 adds material assigned to this user by a teacher/parent.
    cursor = (
        Curriculum.get_pymongo_collection()
        .find({"created_by": owner.id}, LIST_PROJECTION)
        .sort("created_at", -1)
        .limit(limit)
    )
    return [Curriculum.model_validate(raw) async for raw in cursor]


async def get_owned_curriculum(curriculum_id: str, owner: User) -> Curriculum:
    """404 for bad ids, missing docs, and other people's material alike (no existence leak)."""
    try:
        oid = PydanticObjectId(curriculum_id)
    except Exception as exc:
        raise _not_found() from exc
    doc = await Curriculum.find_one({"_id": oid, "created_by": owner.id})
    if doc is None:
        raise _not_found()
    return doc


async def delete_curriculum(doc: Curriculum, owner: User, storage: FileStorage) -> None:
    await doc.delete()
    try:
        await storage.delete(doc.file.key)
    except Exception:
        logger.exception("Could not delete stored file for curriculum=%s", doc.id)
    await User.find_one({"_id": owner.id, "curricula_created": {"$gt": 0}}).update(
        {"$inc": {"curricula_created": -1}}
    )
    # Milestone 1 step 4: also delete this material's personalized courses.
    logger.info("Curriculum deleted curriculum=%s user=%s", doc.id, owner.id)


def touch(doc: Curriculum) -> None:
    doc.updated_at = utcnow()
