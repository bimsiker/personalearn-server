"""Document detection and text extraction: PDF, .docx, .doc (positive and negative paths)."""

import io
import zipfile

import pytest

from app.services.documents import DocumentError, detect_type, extract_document, extract_metadata
from app.services.documents.doc import _to_plain
from tests.pdf_factory import (
    SYLLABUS,
    encrypt_pdf,
    fixture,
    make_docx,
    make_pdf,
    para,
)

MAX = 500_000
EXPECTED_LINES = [
    "Introduction to Biology",
    "University of Lagos",
    "Instructor: Dr. Ada Obi",
    "Week 1 covers cell structure and function.",
]


def extract(data: bytes, max_chars: int = MAX):
    return extract_document(data, max_chars=max_chars)


def error_code(data: bytes) -> str:
    with pytest.raises(DocumentError) as err:
        extract(data)
    return err.value.code


# ============================================================ detection


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"%PDF-1.7 ...", "pdf"),
        (b"PK\x03\x04 ...", "docx"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 ...", "doc"),
    ],
)
def test_detect_by_content(data, expected):
    assert detect_type(data) == expected


@pytest.mark.parametrize(
    "data", [b"", b"hello world", b"\x89PNG\r\n", b"GIF89a", b"{\\rtf1 ...", b"<html>"]
)
def test_unsupported_types(data):
    assert error_code(data) == "UNSUPPORTED_FILE_TYPE"


# ============================================================ PDF


def test_pdf_text_from_every_page():
    result = extract(make_pdf(["Page one about cells", "Page two about DNA"]))
    assert result.file_type == "pdf" and result.page_count == 2
    assert "Page one about cells" in result.text and "Page two about DNA" in result.text


def test_pdf_blank_is_valid_without_text():
    result = extract(make_pdf([""]))
    assert result.text == "" and result.page_count == 1


def test_pdf_owner_password_only_is_readable():
    assert "Readable" in extract(encrypt_pdf(make_pdf(["Readable"]), user_password="")).text


def test_pdf_password_protected():
    assert error_code(encrypt_pdf(make_pdf(["x"]), user_password="pw")) == "PASSWORD_PROTECTED"


@pytest.mark.parametrize("data", [b"%PDF-1.4\nnot really a pdf\n%%EOF", make_pdf(["Hello"])[:200]])
def test_pdf_damaged(data):
    assert error_code(data) == "INVALID_PDF"


def test_truncation():
    result = extract(make_pdf(["word " * 400]), max_chars=1000)
    assert len(result.text) == 1000 and result.truncated


# ============================================================ .docx (made by Microsoft Word)


def test_real_word_docx():
    result = extract(fixture("syllabus.docx"))
    assert result.file_type == "docx" and result.page_count == 1
    for line in EXPECTED_LINES:
        assert line in result.text
    assert "Genetics and heredity" in result.text and "Ecosystems" in result.text  # table


def test_real_word_docx_password_protected():
    assert error_code(fixture("syllabus-password.docx")) == "PASSWORD_PROTECTED"


# ============================================================ .docx (synthetic edge cases)


def test_docx_paragraphs_tabs_and_breaks():
    body = (
        para("First line")
        + "<w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p>"
    )
    assert extract(make_docx(body)).text == "First line\nA\tB\nC"


def test_docx_tables_and_text_boxes_without_duplicates():
    table = (
        "<w:tbl><w:tr><w:tc>"
        + para("Cell one")
        + "</w:tc><w:tc>"
        + para("Cell two")
        + "</w:tc></w:tr></w:tbl>"
    )
    textbox = (
        "<w:p><w:r><w:t>Outer</w:t><w:pict><w:txbxContent>"
        + para("Inside box")
        + "</w:txbxContent></w:pict></w:r></w:p>"
    )
    text = extract(make_docx(table + textbox)).text
    assert text.split("\n") == ["Cell one", "Cell two", "Outer", "Inside box"]


def test_docx_ignores_deleted_text_and_field_codes():
    body = (
        "<w:p><w:r><w:t>Kept</w:t></w:r><w:del><w:r><w:delText>Removed</w:delText></w:r></w:del>"
        '<w:r><w:instrText> HYPERLINK "x" </w:instrText></w:r><w:r><w:t> text</w:t></w:r></w:p>'
    )
    assert extract(make_docx(body)).text == "Kept text"


def test_docx_without_page_count_is_fine():
    assert extract(make_docx(para("Hi"))).page_count is None


def test_docx_metadata_page_count():
    app_xml = (
        b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'extended-properties"><Pages>7</Pages></Properties>'
    )
    data = make_docx(para("Hi"), extra_parts={"docProps/app.xml": app_xml})
    assert extract(data).page_count == 7


def test_zip_that_is_not_a_word_document():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")  # looks like .xlsx
    assert error_code(buffer.getvalue()) == "UNSUPPORTED_FILE_TYPE"


def test_macro_enabled_docx_refused():
    data = make_docx(para("Hi"), extra_parts={"word/vbaProject.bin": b"\x00macro"})
    assert error_code(data) == "MACROS_NOT_ALLOWED"


def test_damaged_zip():
    assert error_code(b"PK\x03\x04" + b"\x00" * 50) == "INVALID_DOCUMENT"


def test_malformed_xml():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document><unclosed>")
    assert error_code(buffer.getvalue()) == "INVALID_DOCUMENT"


def test_xml_entity_attack_blocked():
    """'Billion laughs': DTD entities that expand to gigabytes. defusedxml refuses DTDs."""
    evil = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        "<w:document><w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", evil)
    assert error_code(buffer.getvalue()) == "INVALID_DOCUMENT"


def test_zip_bomb_refused():
    """A tiny compressed part that would inflate to a huge size."""
    bomb = make_docx(para("x" * 2_000_000))  # ~2 MB of one character compresses ~1000:1
    assert error_code(bomb) == "INVALID_DOCUMENT"


# ============================================================ .doc (made by Microsoft Word)


def test_real_word_doc():
    result = extract(fixture("syllabus.doc"))
    assert result.file_type == "doc" and result.page_count == 1
    for line in EXPECTED_LINES:
        assert line in result.text.split("\n")
    assert "Week 2\tGenetics and heredity" in result.text  # one table row per line
    assert "Week 3\tEcosystems" in result.text


def test_real_word_doc_password_protected():
    assert error_code(fixture("syllabus-password.doc")) == "PASSWORD_PROTECTED"


def test_doc_damaged_after_header():
    data = fixture("syllabus.doc")
    assert error_code(data[:600]) == "INVALID_DOCUMENT"


def test_doc_fields_keep_only_visible_result():
    raw = 'See \x13 HYPERLINK "https://x" \x14the website\x15 for details.\r'
    assert _to_plain(raw) == "See the website for details.\n"


def test_doc_special_characters():
    assert _to_plain("a\x0bb\x0cc\x1ed\x1fe\x01") == "a\nb\nc-de"


# ============================================================ metadata (any format)


def test_metadata_same_for_every_format():
    from_pdf = extract_metadata(extract(make_pdf([SYLLABUS])).text)
    from_docx = extract_metadata(extract(fixture("syllabus.docx")).text)
    from_doc = extract_metadata(extract(fixture("syllabus.doc")).text)
    for meta in (from_pdf, from_docx, from_doc):
        assert meta["institution_name"] == "University of Lagos"  # old backend: "of Lagos"
        assert meta["tutor_name"] == "Dr. Ada Obi"
        assert meta["date_created"] == "12/09/2026"
        assert meta["professor_contact"] == "ada.obi@unilag.edu.ng"


def test_metadata_fallbacks():
    meta = extract_metadata("Some notes\nReach me at teacher@school.org\nMarch 3, 2026")
    assert meta["professor_contact"] == "teacher@school.org"
    assert meta["date_created"] == "March 3, 2026"


def test_metadata_absent_fields_omitted():
    assert extract_metadata("Photosynthesis converts light into chemical energy.") == {}


def test_metadata_labels_must_start_a_line():
    meta = extract_metadata("Remember to update your notes regularly.\nDue soon.")
    assert "date_created" not in meta and "tutor_name" not in meta


def test_metadata_values_capped():
    assert len(extract_metadata("Instructor: " + "A" * 500)["tutor_name"]) == 200
