import re
from dataclasses import dataclass
from typing import Literal

FileType = Literal["pdf", "docx", "doc"]

EXTENSIONS: dict[FileType, str] = {"pdf": ".pdf", "docx": ".docx", "doc": ".doc"}
CONTENT_TYPES: dict[FileType, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
}
SUPPORTED_HINT = "Please upload a PDF or Word document (.pdf, .docx or .doc)."


class DocumentError(Exception):
    """A user-facing problem with an uploaded document (maps to HTTP 422)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExtractedDocument:
    file_type: FileType
    text: str
    page_count: int | None  # None when the format doesn't record pages
    truncated: bool


def password_protected() -> DocumentError:
    return DocumentError(
        "PASSWORD_PROTECTED",
        "That document is password-protected. Remove the password and upload it again.",
    )


def macros_not_allowed() -> DocumentError:
    return DocumentError(
        "MACROS_NOT_ALLOWED",
        "Documents containing macros aren't supported. Save it as a regular .docx and "
        "upload it again.",
    )


def clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def finish(file_type: FileType, raw_text: str, page_count: int | None, max_chars: int):
    text = clean_text(raw_text)
    return ExtractedDocument(
        file_type=file_type,
        text=text[:max_chars],
        page_count=page_count,
        truncated=len(text) > max_chars,
    )
