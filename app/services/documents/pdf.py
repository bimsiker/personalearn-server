"""PDF text extraction with pypdf (BSD licence)."""

import io
import logging

from pypdf import PasswordType, PdfReader

from app.services.documents.common import (
    DocumentError,
    ExtractedDocument,
    finish,
    password_protected,
)

logger = logging.getLogger(__name__)

MAX_PAGES = 1000


def extract_pdf(data: bytes, *, max_chars: int) -> ExtractedDocument:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise password_protected()
        page_count = len(reader.pages)
    except DocumentError:
        raise
    except Exception as exc:
        logger.warning("Unreadable PDF: %s", type(exc).__name__)
        raise DocumentError(
            "INVALID_PDF", "We couldn't read that PDF. It may be damaged; try re-saving it."
        ) from exc
    if page_count == 0:
        raise DocumentError("INVALID_PDF", "That PDF has no pages.")

    parts: list[str] = []
    for index, page in enumerate(reader.pages):
        if index >= MAX_PAGES:
            break
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # one bad page shouldn't sink the document
            logger.warning("Skipped unreadable PDF page %d: %s", index + 1, type(exc).__name__)

    return finish("pdf", "\n\n".join(parts), page_count, max_chars)
