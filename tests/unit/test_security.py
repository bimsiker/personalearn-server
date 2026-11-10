from datetime import timedelta

import jwt
import pytest

from app.core.security import (
    CLASS_CODE_ALPHABET,
    TokenError,
    create_token,
    decode_token,
    generate_class_code,
    generate_numeric_code,
    hash_password,
    hashes_match,
    keyed_hash,
    password_problem,
    verify_password,
)

SECRET = "k" * 40
OTHER_SECRET = "z" * 40


# --- passwords ---------------------------------------------------------------------


def test_hash_and_verify_password():
    hashed = hash_password("Sunflower42")
    assert hashed != "Sunflower42"
    assert hashed.startswith("$argon2")
    assert verify_password("Sunflower42", hashed)


def test_wrong_password_fails():
    assert not verify_password("sunflower42", hash_password("Sunflower42"))


def test_malformed_hash_is_a_mismatch_not_a_crash():
    assert not verify_password("Sunflower42", "not-a-hash")


def test_same_password_hashes_differently_each_time():
    assert hash_password("Sunflower42") != hash_password("Sunflower42")


@pytest.mark.parametrize("password", ["abcdefg1", "Sunflower42", "12345678", "a" * 127 + "1"])
def test_acceptable_passwords(password):
    assert password_problem(password) is None


@pytest.mark.parametrize(
    "password,reason",
    [
        ("", "at least 8"),
        ("abc1", "at least 8"),
        ("abcdefgh", "number"),
        ("a" * 128 + "1", "at most"),
    ],
)
def test_weak_passwords(password, reason):
    assert reason in password_problem(password)


# --- codes and hashes ---------------------------------------------------------------


def test_numeric_code_is_six_digits():
    for _ in range(50):
        code = generate_numeric_code()
        assert len(code) == 6 and code.isdigit()


def test_class_code_uses_unambiguous_alphabet():
    code = generate_class_code()
    assert len(code) == 8
    assert set(code) <= set(CLASS_CODE_ALPHABET)
    assert not set(code) & set("01OI")


def test_keyed_hash_depends_on_secret():
    assert keyed_hash("123456", SECRET) == keyed_hash("123456", SECRET)
    assert keyed_hash("123456", SECRET) != keyed_hash("123456", OTHER_SECRET)
    assert keyed_hash("123456", SECRET) != keyed_hash("123457", SECRET)


def test_hashes_match():
    assert hashes_match("abc", "abc")
    assert not hashes_match("abc", "abd")


# --- JWT -------------------------------------------------------------------------------


def _token(**kw):
    defaults = {
        "subject": "user-1",
        "token_type": "access",
        "secret": SECRET,
        "ttl": timedelta(minutes=5),
    }
    defaults.update(kw)
    return create_token(**defaults)


def test_token_round_trip():
    token, jti = _token(extra={"role": "student"})
    payload = decode_token(token, secret=SECRET, expected_type="access")
    assert payload["sub"] == "user-1"
    assert payload["jti"] == jti
    assert payload["role"] == "student"


def test_each_token_gets_unique_jti():
    assert _token()[1] != _token()[1]


def test_expired_token_rejected():
    token, _ = _token(ttl=timedelta(seconds=-1))
    with pytest.raises(TokenError, match="Expired"):
        decode_token(token, secret=SECRET, expected_type="access")


def test_wrong_secret_rejected():
    token, _ = _token()
    with pytest.raises(TokenError):
        decode_token(token, secret=OTHER_SECRET, expected_type="access")


def test_wrong_type_rejected():
    token, _ = _token(token_type="refresh")
    with pytest.raises(TokenError, match="wrong token type"):
        decode_token(token, secret=SECRET, expected_type="access")


def test_tampered_token_rejected():
    token, _ = _token()
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}x.{signature}"
    with pytest.raises(TokenError):
        decode_token(tampered, secret=SECRET, expected_type="access")


def test_unsigned_token_rejected():
    forged = jwt.encode(
        {"sub": "admin", "typ": "access", "jti": "x", "iat": 0, "exp": 9999999999},
        key="",
        algorithm="none",
    )
    with pytest.raises(TokenError):
        decode_token(forged, secret=SECRET, expected_type="access")


def test_token_missing_claims_rejected():
    incomplete = jwt.encode({"sub": "user-1", "exp": 9999999999}, SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(incomplete, secret=SECRET, expected_type="access")


@pytest.mark.parametrize("garbage", ["", "not.a.token", "abc"])
def test_garbage_rejected(garbage):
    with pytest.raises(TokenError):
        decode_token(garbage, secret=SECRET, expected_type="access")


def test_utcnow_is_utc_and_millisecond_precise():
    from datetime import UTC

    from app.core.clock import utcnow

    now = utcnow()
    assert now.tzinfo is UTC
    assert now.microsecond % 1000 == 0
