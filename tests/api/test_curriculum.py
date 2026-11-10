"""Curriculum upload & library (PDF, .docx, .doc): success and 401/404/413/422 paths."""

from datetime import timedelta
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import create_token
from app.main import create_app
from app.models import Curriculum, User
from tests.conftest import TEST_JWT_SECRET, bearer
from tests.pdf_factory import SYLLABUS, encrypt_pdf, fixture, make_docx, make_pdf, para

pytestmark = pytest.mark.db

PDF = make_pdf([SYLLABUS, "Week 2: genetics and heredity."])


async def _student(api) -> str:
    _, auth = await api.verified_user()
    return auth["accessToken"]


def _path_and_query(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}"


# ============================================================ upload


async def test_upload_creates_curriculum(api):
    token = await _student(api)
    res = await api.upload(token, PDF, title="  Intro   to Biology ", filename="Bio Syllabus.pdf")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["title"] == "Intro to Biology"
    assert body["description"] == "Cells and genetics"
    assert body["fileName"] == "Bio Syllabus.pdf"
    assert body["pageCount"] == 2 and body["hasText"] is True and body["hasLesson"] is False
    assert body["fileSizeBytes"] == len(PDF)
    assert body["metadata"]["institutionName"] == "University of Lagos"
    assert body["metadata"]["tutorName"] == "Dr. Ada Obi"
    assert "extractedText" not in body and "key" not in str(body)

    doc = await Curriculum.get(body["_id"])
    assert "genetics and heredity" in doc.extracted_text
    assert doc.file.backend == "local" and doc.file.key.startswith("curricula/")
    assert api.app.state.storage.path_for(doc.file.key).read_bytes() == PDF
    me = await api.client.get("/users/me", headers=bearer(token))
    assert me.json()["curriculaCreated"] == 1


async def test_scanned_pdf_is_accepted_but_flagged(api):
    token = await _student(api)
    res = await api.upload(token, make_pdf([""]))
    assert res.status_code == 201
    assert res.json()["hasText"] is False


@pytest.mark.parametrize("role", ["student", "parent", "teacher"])
async def test_every_role_can_upload(api, role):
    _, auth = await api.verified_user(role=role)
    assert (await api.upload(auth["accessToken"], PDF)).status_code == 201


async def test_upload_requires_login(api):
    res = await api.client.post(
        "/curriculum/create",
        data={"title": "x"},
        files={"file": ("a.pdf", PDF, "application/pdf")},
    )
    assert res.status_code == 401


@pytest.mark.parametrize(
    "data,code",
    [
        (b"just some text", "UNSUPPORTED_FILE_TYPE"),
        (b"%PDF-1.4 broken", "INVALID_PDF"),
        (encrypt_pdf(make_pdf(["x"]), user_password="pw"), "PASSWORD_PROTECTED"),
        (fixture("syllabus-password.docx"), "PASSWORD_PROTECTED"),
        (fixture("syllabus-password.doc"), "PASSWORD_PROTECTED"),
        (make_docx(para("x"), extra_parts={"word/vbaProject.bin": b"m"}), "MACROS_NOT_ALLOWED"),
        (b"", "EMPTY_FILE"),
    ],
)
async def test_bad_files_rejected_and_nothing_stored(api, data, code):
    token = await _student(api)
    stored_files = lambda: len(list(api.app.state.storage.base_dir.glob("curricula/*")))  # noqa: E731
    files_before = stored_files()
    res = await api.upload(token, data, filename="fake.pdf")
    assert res.status_code == 422
    assert res.json()["code"] == code
    assert await Curriculum.count() == 0
    assert stored_files() == files_before


@pytest.mark.parametrize(
    "form,missing_file",
    [
        ({"title": ""}, False),
        ({"title": "x" * 201}, False),
        ({"title": "Ok", "description": "d" * 2001}, False),
        ({}, False),  # no title
        ({"title": "Ok"}, True),  # no file
    ],
)
async def test_upload_validation(api, form, missing_file):
    token = await _student(api)
    files = None if missing_file else {"file": ("a.pdf", PDF, "application/pdf")}
    res = await api.client.post("/curriculum/create", headers=bearer(token), data=form, files=files)
    assert res.status_code == 422 and res.json()["code"] == "VALIDATION_ERROR"


async def test_file_over_limit_rejected(api):
    small = create_app(api.settings.model_copy(update={"max_upload_mb": 1}))
    small.state.email_sender = api.outbox
    token = await _student(api)
    too_big = PDF + b"0" * 1_100_000
    async with AsyncClient(transport=ASGITransport(app=small), base_url="http://t") as c:
        res = await c.post(
            "/curriculum/create",
            headers=bearer(token),
            data={"title": "Big"},
            files={"file": ("big.pdf", too_big, "application/pdf")},
        )
    assert res.status_code == 413
    assert res.json()["code"] == "FILE_TOO_LARGE"


async def test_oversized_request_refused_before_reading(api):
    token = await _student(api)
    res = await api.client.post(
        "/curriculum/create",
        headers={**bearer(token), "Content-Length": str(500_000_000)},
        content=b"tiny",
    )
    assert res.status_code == 413 and res.json()["code"] == "PAYLOAD_TOO_LARGE"


# ============================================================ library


async def test_list_shows_only_my_uploads_newest_first(api):
    mine = await _student(api)
    other = await _student(api)
    await api.upload(mine, PDF, title="First")
    await api.upload(mine, PDF, title="Second")
    await api.upload(other, PDF, title="Not mine")

    res = await api.client.get("/curriculum", headers=bearer(mine))
    assert res.status_code == 200
    assert [c["title"] for c in res.json()] == ["Second", "First"]
    assert all("extractedText" not in c for c in res.json())


async def test_list_empty_and_limit(api):
    token = await _student(api)
    assert (await api.client.get("/curriculum", headers=bearer(token))).json() == []
    for i in range(3):
        await api.upload(token, PDF, title=f"T{i}")
    res = await api.client.get("/curriculum?limit=2", headers=bearer(token))
    assert len(res.json()) == 2
    bad = await api.client.get("/curriculum?limit=0", headers=bearer(token))
    assert bad.status_code == 422


async def test_get_one(api):
    token = await _student(api)
    created = (await api.upload(token, PDF)).json()
    res = await api.client.get(f"/curriculum/{created['_id']}", headers=bearer(token))
    assert res.status_code == 200 and res.json() == created


@pytest.mark.parametrize("bad_id", ["not-an-id", "6aef9c7d078006d6a31c3502"])
async def test_get_missing_returns_404(api, bad_id):
    token = await _student(api)
    res = await api.client.get(f"/curriculum/{bad_id}", headers=bearer(token))
    assert res.status_code == 404 and res.json()["code"] == "NOT_FOUND"


async def test_other_users_material_is_invisible(api):
    owner = await _student(api)
    intruder = await _student(api)
    cid = (await api.upload(owner, PDF)).json()["_id"]
    for method, path in [
        ("GET", f"/curriculum/{cid}"),
        ("GET", f"/curriculum/{cid}/pdf"),
        ("DELETE", f"/curriculum/{cid}"),
    ]:
        res = await api.client.request(method, path, headers=bearer(intruder))
        assert res.status_code == 404, f"{method} {path}"
    assert await Curriculum.get(cid) is not None


async def test_library_requires_login(api):
    assert (await api.client.get("/curriculum")).status_code == 401


# ============================================================ viewing the PDF


async def test_pdf_link_opens_the_file(api):
    token = await _student(api)
    cid = (await api.upload(token, PDF, filename="Bio Syllabus.pdf")).json()["_id"]
    link = await api.client.get(f"/curriculum/{cid}/file", headers=bearer(token))
    assert link.status_code == 200
    assert link.json()["expiresIn"] == 300
    assert link.json()["fileType"] == "pdf" and link.json()["opensInBrowser"] is True

    res = await api.client.get(_path_and_query(link.json()["url"]))  # no auth header needed
    assert res.status_code == 200
    assert res.content == PDF
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"].startswith("inline")
    assert (
        "Bio%20Syllabus.pdf" in res.headers["content-disposition"]
        or "Bio Syllabus.pdf" in (res.headers["content-disposition"])
    )
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in res.headers["cache-control"]


async def test_expired_link_rejected(api):
    token = await _student(api)
    cid = (await api.upload(token, PDF)).json()["_id"]
    doc = await Curriculum.get(cid)
    expired, _ = create_token(
        subject=doc.file.key, token_type="file", secret=TEST_JWT_SECRET, ttl=timedelta(seconds=-1)
    )
    res = await api.client.get("/files/download", params={"token": expired})
    assert res.status_code == 403 and res.json()["code"] == "LINK_EXPIRED"


async def test_access_token_cannot_be_used_as_file_link(api):
    token = await _student(api)
    res = await api.client.get("/files/download", params={"token": token})
    assert res.status_code == 403


async def test_link_with_forged_path_rejected(api):
    forged, _ = create_token(
        subject="../../.env", token_type="file", secret=TEST_JWT_SECRET, ttl=timedelta(minutes=5)
    )
    res = await api.client.get("/files/download", params={"token": forged})
    assert res.status_code == 403


async def test_garbage_link_rejected(api):
    res = await api.client.get("/files/download", params={"token": "x" * 40})
    assert res.status_code == 403
    assert (await api.client.get("/files/download")).status_code == 422


# ============================================================ delete


async def test_delete_removes_record_file_and_link(api):
    token = await _student(api)
    cid = (await api.upload(token, PDF)).json()["_id"]
    doc = await Curriculum.get(cid)
    path = api.app.state.storage.path_for(doc.file.key)
    link = (await api.client.get(f"/curriculum/{cid}/pdf", headers=bearer(token))).json()["url"]

    res = await api.client.delete(f"/curriculum/{cid}", headers=bearer(token))
    assert res.status_code == 204
    assert await Curriculum.get(cid) is None
    assert not path.exists()
    assert (await api.client.get(_path_and_query(link))).status_code == 404
    assert (await api.client.get("/curriculum", headers=bearer(token))).json() == []
    me = await api.client.get("/users/me", headers=bearer(token))
    assert me.json()["curriculaCreated"] == 0

    again = await api.client.delete(f"/curriculum/{cid}", headers=bearer(token))
    assert again.status_code == 404


async def test_upload_counter_never_goes_negative(api):
    email, auth = await api.verified_user()
    token = auth["accessToken"]
    cid = (await api.upload(token, PDF)).json()["_id"]
    user = await User.find_one({"email.value": email})
    user.curricula_created = 0  # simulate drift
    await user.save()
    await api.client.delete(f"/curriculum/{cid}", headers=bearer(token))
    assert (await User.find_one({"email.value": email})).curricula_created == 0


# ============================================================ Word documents


WORD_CASES = [
    (
        "syllabus.docx",
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ("syllabus.doc", "doc", "application/msword"),
]


@pytest.mark.parametrize("name,file_type,content_type", WORD_CASES)
async def test_word_upload_and_download(api, name, file_type, content_type):
    token = await _student(api)
    data = fixture(name)
    res = await api.upload(token, data, title="Biology (Word)", filename=name)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["fileType"] == file_type and body["fileName"] == name
    assert body["hasText"] is True and body["pageCount"] == 1
    assert body["metadata"]["institutionName"] == "University of Lagos"
    assert body["metadata"]["tutorName"] == "Dr. Ada Obi"

    doc = await Curriculum.get(body["_id"])
    assert "Genetics and heredity" in doc.extracted_text
    assert doc.file.key.endswith(f".{file_type}")

    link = (await api.client.get(f"/curriculum/{body['_id']}/file", headers=bearer(token))).json()
    assert link["fileType"] == file_type and link["opensInBrowser"] is False
    download = await api.client.get(_path_and_query(link["url"]))
    assert download.status_code == 200 and download.content == data
    assert download.headers["content-type"] == content_type
    assert download.headers["content-disposition"].startswith("attachment")


async def test_file_type_comes_from_content_not_name(api):
    """A Word file named .pdf is stored and labelled as Word."""
    token = await _student(api)
    res = await api.upload(token, fixture("syllabus.docx"), filename="sneaky.pdf")
    assert res.status_code == 201
    assert res.json()["fileType"] == "docx" and res.json()["fileName"] == "sneaky.docx"


async def test_pdf_alias_still_works(api):
    token = await _student(api)
    cid = (await api.upload(token, PDF)).json()["_id"]
    res = await api.client.get(f"/curriculum/{cid}/pdf", headers=bearer(token))
    assert res.status_code == 200 and res.json()["fileType"] == "pdf"


async def test_unsupported_office_files_rejected(api):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    token = await _student(api)
    res = await api.upload(token, buffer.getvalue(), filename="grades.xlsx")
    assert res.status_code == 422 and res.json()["code"] == "UNSUPPORTED_FILE_TYPE"
