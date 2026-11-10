"""Build test documents in memory (PDF, .docx). Real Word-made .doc/.docx files live in
tests/fixtures/ because there is no pure-Python way to create genuine .doc files."""

import io
import zipfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str]) -> bytes:
    """One PDF page per string; '\\n' starts a new line. Uses the built-in Helvetica font."""
    page_ids = [4 + 2 * i for i in range(len(pages))]
    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] "
        f"/Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, text in enumerate(pages):
        ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
        ops += [f"({_escape(line)}) Tj T*" for line in text.split("\n")] if text else []
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        bodies.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {page_ids[index] + 1} 0 R >>"
            ).encode()
        )
        bodies.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def encrypt_pdf(data: bytes, *, user_password: str, owner_password: str = "owner-pw") -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


SYLLABUS = (
    "Introduction to Biology\n"
    "University of Lagos\n"
    "Instructor: Dr. Ada Obi\n"
    "Date: 12/09/2026\n"
    "Contact: ada.obi@unilag.edu.ng\n"
    "Prerequisites: Basic chemistry and high school biology\n"
    "Technology Requirements: A laptop with a modern web browser\n"
    "Week 1 covers cell structure and function."
)


# --- Word .docx (synthetic) ------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)


def make_docx(body_xml: str, *, extra_parts: dict[str, bytes] | None = None) -> bytes:
    """A minimal .docx whose <w:body> contains `body_xml` (WordprocessingML)."""
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W_NS}">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("word/document.xml", document)
        for name, data in (extra_parts or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def para(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
