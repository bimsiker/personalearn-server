"""Best-effort syllabus details (institution, instructor, date, ...) from extracted text.

Ported from the old backend's pdf.service.ts with fixes: labels must start a line (the
old patterns matched words mid-sentence) and institution lines keep the full name.
"""

import re

METADATA_SCAN_CHARS = 20_000
METADATA_MAX_LEN = 200

_FLAGS = re.IGNORECASE | re.MULTILINE
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"

_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "institution_name": [
        re.compile(r"^\s*(?:institution|school|university|college)\s*[:\-]\s*(.+)$", _FLAGS),
        # A whole line naming an institution, e.g. "University of Lagos".
        re.compile(
            r"^\s*(.{0,80}\b(?:university|college|institute|academy|polytechnic|school)\b.{0,80})$",
            _FLAGS,
        ),
    ],
    "tutor_name": [
        re.compile(
            r"^\s*(?:professor|instructor|tutor|teacher|lecturer|facilitator)\s*[:\-]\s*(.+)$",
            _FLAGS,
        ),
        re.compile(r"^\s*taught\s+by\s*[:\-]?\s*(.+)$", _FLAGS),
    ],
    "date_created": [
        re.compile(r"^\s*(?:date(?:\s+created)?|created(?:\s+on)?)\s*[:\-]\s*(.+)$", _FLAGS),
        re.compile(r"\b(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})\b"),
        re.compile(rf"\b({_MONTH}\s+\d{{1,2}},?\s+\d{{4}})\b", re.IGNORECASE),
    ],
    "professor_contact": [
        re.compile(r"^\s*(?:contact|e-?mail)\s*[:\-]\s*(.+)$", _FLAGS),
        re.compile(r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"),
    ],
    "course_requirements": [
        re.compile(r"^\s*(?:course\s+requirements|prerequisites?)\s*[:\-]?\s*(.{10,})$", _FLAGS),
    ],
    "technology_requirements": [
        re.compile(r"^\s*(?:technology|technical)\s+requirements\s*[:\-]?\s*(.{10,})$", _FLAGS),
    ],
}


def extract_metadata(text: str) -> dict[str, str]:
    """Labels found near the top of a syllabus. Missing fields are omitted."""
    head = text[:METADATA_SCAN_CHARS]
    found: dict[str, str] = {}
    for field, patterns in _PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(head)
            if match:
                value = " ".join(match.group(1).split())[:METADATA_MAX_LEN]
                if value:
                    found[field] = value
                    break
    return found
