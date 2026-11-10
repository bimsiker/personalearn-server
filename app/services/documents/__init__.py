"""Uploaded document handling: detect the type by content, then extract text.

Supported: PDF, Word .docx, Word .doc. The file name/extension is never trusted.
All functions are synchronous and CPU-bound: call `extract_document` via asyncio.to_thread.
"""

from app.services.documents.common import (
    CONTENT_TYPES,
    EXTENSIONS,
    SUPPORTED_HINT,
    DocumentError,
    ExtractedDocument,
    FileType,
)
from app.services.documents.doc import extract_doc
from app.services.documents.docx import extract_docx
from app.services.documents.metadata import extract_metadata
from app.services.documents.pdf import extract_pdf

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_EXTRACTORS = {"pdf": extract_pdf, "docx": extract_docx, "doc": extract_doc}


def detect_type(data: bytes) -> FileType:
    if data.startswith(PDF_MAGIC):
        return "pdf"
    if data.startswith(ZIP_MAGIC):
        return "docx"  # confirmed (or refused) by the .docx reader
    if data.startswith(OLE_MAGIC):
        return "doc"  # confirmed (or refused) by the .doc reader
    raise DocumentError(
        "UNSUPPORTED_FILE_TYPE", f"That file type isn't supported. {SUPPORTED_HINT}"
    )


def extract_document(data: bytes, *, max_chars: int) -> ExtractedDocument:
    return _EXTRACTORS[detect_type(data)](data, max_chars=max_chars)


__all__ = [
    "CONTENT_TYPES",
    "EXTENSIONS",
    "DocumentError",
    "ExtractedDocument",
    "FileType",
    "detect_type",
    "extract_document",
    "extract_metadata",
]
