from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import Settings
from app.core.security import decode_token
from app.services.curriculum import safe_filename
from app.services.storage import LocalFileStorage
from tests.conftest import TEST_JWT_SECRET


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:
    settings = Settings(_env_file=None, jwt_secret=TEST_JWT_SECRET, log_to_file=False)
    return LocalFileStorage(str(tmp_path), settings)


async def test_save_uses_random_key_and_writes_file(storage):
    key = await storage.save(b"%PDF-data", folder="curricula", extension=".pdf")
    assert key.startswith("curricula/") and key.endswith(".pdf")
    assert storage.path_for(key).read_bytes() == b"%PDF-data"
    assert key != await storage.save(b"%PDF-data", folder="curricula", extension=".pdf")


async def test_delete_removes_file_and_is_idempotent(storage):
    key = await storage.save(b"x", folder="curricula", extension=".pdf")
    await storage.delete(key)
    assert not storage.path_for(key).exists()
    await storage.delete(key)  # second delete: no error


@pytest.mark.parametrize(
    "key",
    [
        "../secrets.txt",
        "curricula/../../etc/passwd",
        "curricula/abc.pdf",  # not a generated key
        "/abs/path.pdf",
        "curricula\\..\\x.pdf",
        "",
    ],
)
def test_unsafe_keys_rejected(storage, key):
    with pytest.raises(ValueError):
        storage.path_for(key)


def test_download_url_carries_short_lived_file_token(storage):
    url = storage.download_url(
        "curricula/" + "a" * 32 + ".pdf",
        filename="Bio.pdf",
        file_type="pdf",
        base_url="http://api.test/",
    )
    parsed = urlparse(url)
    assert parsed.path == "/files/download"
    token = parse_qs(parsed.query)["token"][0]
    payload = decode_token(token, secret=TEST_JWT_SECRET, expected_type="file")
    assert payload["fn"] == "Bio.pdf" and payload["ft"] == "pdf"
    assert payload["exp"] - payload["iat"] == 300


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Biology Syllabus.pdf", "Biology Syllabus.pdf"),
        ("C:\\Users\\me\\Desktop\\notes.pdf", "notes.pdf"),
        ("../../etc/passwd", "passwd.pdf"),
        ('bad"<name>|.pdf', "badname.pdf"),
        ("", "document.pdf"),
        (None, "document.pdf"),
        ("report", "report.pdf"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


@pytest.mark.parametrize(
    "raw,extension,expected",
    [
        ("Syllabus.docx", ".docx", "Syllabus.docx"),
        ("Old notes.DOC", ".doc", "Old notes.doc"),
        ("disguised.pdf", ".docx", "disguised.docx"),  # name follows the detected type
        ("macro.docm", ".docx", "macro.docx"),
    ],
)
def test_safe_filename_uses_detected_extension(raw, extension, expected):
    assert safe_filename(raw, extension) == expected


def test_safe_filename_length_capped():
    assert len(safe_filename("x" * 400 + ".pdf")) == 150
