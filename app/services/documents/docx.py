"""Word .docx text extraction: a zip of XML, read with zipfile + defusedxml.

Safety: only two small parts are ever read, each size-checked before decompressing
(zip bombs), and XML is parsed with defusedxml (no DTDs/entities, so no XXE or
"billion laughs"). Macro-enabled packages (.docm) are refused.
"""

import io
import logging
import zipfile
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from app.services.documents.common import (
    SUPPORTED_HINT,
    DocumentError,
    ExtractedDocument,
    finish,
    macros_not_allowed,
)

logger = logging.getLogger(__name__)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
EP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
DOCUMENT_PART = "word/document.xml"
MAX_PART_BYTES = 50_000_000  # uncompressed
MAX_COMPRESSION_RATIO = 200


def _invalid() -> DocumentError:
    return DocumentError(
        "INVALID_DOCUMENT", "We couldn't read that Word document. It may be damaged."
    )


def _read_part(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_PART_BYTES or (
        info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
    ):
        logger.warning("DOCX part %s rejected: suspicious size", name)
        raise _invalid()
    return archive.read(info)


def _paragraph_text(paragraph: Element, nested: list[Element]) -> str:
    """Text of one w:p. Paragraphs nested inside it (text boxes) are collected separately."""
    parts: list[str] = []

    def visit(node: Element) -> None:
        for child in node:
            tag = child.tag
            if tag == f"{W}p":
                nested.append(child)
            elif tag == f"{W}t":
                parts.append(child.text or "")
            elif tag == f"{W}tab":
                parts.append("\t")
            elif tag in (f"{W}br", f"{W}cr"):
                parts.append("\n")
            elif tag in (f"{W}delText", f"{W}instrText"):
                continue  # deleted tracked-change text and field codes aren't content
            else:
                visit(child)

    visit(paragraph)
    return "".join(parts)


def _document_lines(root: Element) -> list[str]:
    lines: list[str] = []

    def walk(node: Element) -> None:
        for child in node:
            if child.tag == f"{W}p":
                nested: list[Element] = []
                lines.append(_paragraph_text(child, nested))
                for inner in nested:
                    walk_paragraph(inner)
            else:
                walk(child)

    def walk_paragraph(paragraph: Element) -> None:
        nested: list[Element] = []
        lines.append(_paragraph_text(paragraph, nested))
        for inner in nested:
            walk_paragraph(inner)

    walk(root)
    return lines


def _page_count(archive: zipfile.ZipFile) -> int | None:
    try:
        root = fromstring(_read_part(archive, "docProps/app.xml"))
        pages = root.find(f"{EP}Pages")
        return int(pages.text) if pages is not None and pages.text else None
    except Exception:  # optional metadata: never fail the upload over it
        return None


def extract_docx(data: bytes, *, max_chars: int) -> ExtractedDocument:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = set(archive.namelist())
    except (zipfile.BadZipFile, ValueError) as exc:
        raise _invalid() from exc

    if DOCUMENT_PART not in names:
        # A zip, but not a Word document (e.g. .xlsx, .pptx, plain .zip).
        raise DocumentError(
            "UNSUPPORTED_FILE_TYPE", f"That file type isn't supported. {SUPPORTED_HINT}"
        )
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise macros_not_allowed()

    try:
        root = fromstring(_read_part(archive, DOCUMENT_PART))
    except DocumentError:
        raise
    except (DefusedXmlException, ParseError, zipfile.BadZipFile, RuntimeError, OSError) as exc:
        logger.warning("Unreadable DOCX: %s", type(exc).__name__)
        raise _invalid() from exc

    return finish("docx", "\n".join(_document_lines(root)), _page_count(archive), max_chars)
