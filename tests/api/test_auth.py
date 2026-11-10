"""Auth & user endpoints: success paths and failure paths (422/400/401/403/409/429)."""

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.clock import utcnow
from app.core.security import create_token, hash_password
from app.main import create_app
from app.models import RefreshSession, User
from tests.conftest import STRONG_PASSWORD, TEST_JWT_SECRET, bearer

pytestmark = pytest.mark.db


async def _user(email: str) -> User:
    user = await User.find_one({"email.value": email})
    assert user is not None
    return user


# ============================================================ signup


async def test_signup_creates_unverified_user_and_emails_code(api):
    res = await api.client.post(
        "/users/signup",
        json={
            "name": "  Ada   Lovelace ",
            "email": "Ada@Example.com",
            "password": STRONG_PASSWORD,
            "role": "student",
        },
    )
    assert res.status_code == 201
    assert res.json()["email"] == "ada@example.com"
    assert STRONG_PASSWORD not in res.text and "hash" not in res.text.lower()

    user = await _user("ada@example.com")
    assert user.name == "Ada Lovelace"
    assert user.email.verified is False
    assert user.password_hash != STRONG_PASSWORD and user.password_hash.startswith("$argon2")
    assert user.class_code is None
    code = api.outbox.code_for("ada@example.com")
    assert user.verification.code_hash != code  # stored hashed, never in plain text


async def test_teacher_signup_gets_class_code(api):
    email = await api.signup(role="teacher")
    user = await _user(email)
    assert user.class_code and len(user.class_code) == 8


@pytest.mark.parametrize("role", ["parent", "teacher", "student"])
async def test_each_public_role_can_sign_up(api, role):
    email = await api.signup(role=role)
    assert (await _user(email)).role == role


async def test_duplicate_email_is_rejected_case_insensitively(api):
    email = await api.signup()
    res = await api.client.post(
        "/users/signup",
        json={"name": "X", "email": email.upper(), "password": STRONG_PASSWORD, "role": "parent"},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    "change,field",
    [
        ({"role": "admin"}, "role"),  # can never self-assign admin
        ({"role": "creator"}, "role"),
        ({"email": "not-an-email"}, "email"),
        ({"password": "short1"}, "password"),
        ({"password": "nonumbershere"}, "password"),
        ({"name": "   "}, "name"),
        ({"name": "x" * 101}, "name"),
    ],
)
async def test_signup_validation_errors(api, change, field):
    body = {"name": "Ok", "email": api.new_email(), "password": STRONG_PASSWORD, "role": "student"}
    body.update(change)
    res = await api.client.post("/users/signup", json=body)
    assert res.status_code == 422
    assert res.json()["code"] == "VALIDATION_ERROR"
    assert field in str(res.json()["details"])
    assert body["password"] not in res.text  # submitted values never echoed
    assert await User.find_one({"email.value": body["email"]}) is None


async def test_signup_missing_fields(api):
    res = await api.client.post("/users/signup", json={})
    assert res.status_code == 422


# ============================================================ verify email


async def test_verify_with_correct_code_returns_tokens(api):
    email = await api.signup()
    res = await api.client.post(
        "/users/verify-email", json={"email": email, "code": api.outbox.code_for(email)}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["tokenType"] == "bearer" and body["accessToken"] and body["refreshToken"]
    assert body["expiresIn"] == 15 * 60
    assert body["user"]["email"] == {"value": email, "verified": True}
    user = await _user(email)
    assert user.email.verified and user.verification is None


async def test_wrong_code_counts_attempts_then_locks(api):
    email = await api.signup()
    real = api.outbox.code_for(email)
    wrong = "000000" if real != "000000" else "111111"
    for attempt in range(1, 6):
        res = await api.client.post("/users/verify-email", json={"email": email, "code": wrong})
        assert res.status_code == 400 and res.json()["code"] == "INVALID_CODE"
        assert (await _user(email)).verification.attempts == attempt
    # Locked: even the right code is refused until a new one is requested.
    res = await api.client.post("/users/verify-email", json={"email": email, "code": real})
    assert res.status_code == 429 and res.json()["code"] == "TOO_MANY_ATTEMPTS"


async def test_expired_code_rejected(api):
    email = await api.signup()
    user = await _user(email)
    user.verification.expires_at = utcnow() - timedelta(seconds=1)
    await user.save()
    res = await api.client.post(
        "/users/verify-email", json={"email": email, "code": api.outbox.code_for(email)}
    )
    assert res.status_code == 400 and res.json()["code"] == "CODE_EXPIRED"


async def test_code_cannot_be_reused(api):
    email = await api.signup()
    code = api.outbox.code_for(email)
    assert (
        await api.client.post("/users/verify-email", json={"email": email, "code": code})
    ).status_code == 200
    res = await api.client.post("/users/verify-email", json={"email": email, "code": code})
    assert res.status_code == 400 and res.json()["code"] == "INVALID_CODE"


async def test_verify_unknown_email_looks_like_wrong_code(api):
    res = await api.client.post(
        "/users/verify-email", json={"email": "ghost@example.com", "code": "123456"}
    )
    assert res.status_code == 400 and res.json()["code"] == "INVALID_CODE"


@pytest.mark.parametrize("code", ["12345", "1234567", "12ab56", ""])
async def test_verify_code_format_validated(api, code):
    res = await api.client.post(
        "/users/verify-email", json={"email": api.new_email(), "code": code}
    )
    assert res.status_code == 422


# ============================================================ resend code


async def test_resend_after_cooldown_sends_new_code_and_old_stops_working(api):
    email = await api.signup()
    old = api.outbox.code_for(email)
    user = await _user(email)
    user.verification.last_sent_at = utcnow() - timedelta(minutes=5)
    await user.save()

    res = await api.client.post("/users/resend-verification-code", json={"email": email})
    assert res.status_code == 200 and res.json()["retryAfterSeconds"] == 60
    new = api.outbox.code_for(email)
    assert len([m for m in api.outbox.messages if m.to == email]) == 2

    if new != old:
        bad = await api.client.post("/users/verify-email", json={"email": email, "code": old})
        assert bad.status_code == 400
    good = await api.client.post("/users/verify-email", json={"email": email, "code": new})
    assert good.status_code == 200


async def test_resend_within_cooldown_sends_nothing(api):
    email = await api.signup()
    res = await api.client.post("/users/resend-verification-code", json={"email": email})
    assert res.status_code == 200
    assert len([m for m in api.outbox.messages if m.to == email]) == 1


async def test_resend_unknown_or_verified_email_gives_same_answer(api):
    verified, _ = await api.verified_user()
    sent_before = len(api.outbox.messages)
    a = await api.client.post("/users/resend-verification-code", json={"email": "no@example.com"})
    b = await api.client.post("/users/resend-verification-code", json={"email": verified})
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()
    assert len(api.outbox.messages) == sent_before


# ============================================================ login


async def test_login_success(api):
    email, _ = await api.verified_user()
    res = await api.client.post(
        "/users/login", json={"email": email.upper(), "password": STRONG_PASSWORD}
    )
    assert res.status_code == 200
    assert res.json()["user"]["email"]["value"] == email
    assert (await _user(email)).last_login is not None


async def test_wrong_password_and_unknown_email_look_identical(api):
    email, _ = await api.verified_user()
    wrong = await api.client.post("/users/login", json={"email": email, "password": "Wrong12345"})
    ghost = await api.client.post(
        "/users/login", json={"email": "ghost@example.com", "password": STRONG_PASSWORD}
    )
    assert wrong.status_code == ghost.status_code == 401
    assert (
        wrong.json()
        == ghost.json()
        == {
            "code": "INVALID_CREDENTIALS",
            "message": "Email or password is incorrect.",
        }
    )


async def test_unverified_user_with_right_password_gets_403(api):
    email = await api.signup()
    res = await api.client.post("/users/login", json={"email": email, "password": STRONG_PASSWORD})
    assert res.status_code == 403 and res.json()["code"] == "EMAIL_NOT_VERIFIED"


async def test_unverified_user_with_wrong_password_gets_401(api):
    email = await api.signup()
    res = await api.client.post("/users/login", json={"email": email, "password": "Wrong12345"})
    assert res.status_code == 401


async def test_login_validation(api):
    res = await api.client.post("/users/login", json={"email": "x@example.com", "password": ""})
    assert res.status_code == 422


# ============================================================ /users/me


async def test_me_returns_frontend_shaped_user(api):
    email, auth = await api.verified_user(role="teacher")
    res = await api.client.get("/users/me", headers=bearer(auth["accessToken"]))
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"_id", "name", "email", "role", "curriculaCreated", "curriculaEnrolled"}
    assert body["role"] == "teacher" and len(body["classCode"]) == 8
    assert "passwordHash" not in body and "password_hash" not in body
    assert "verification" not in body


async def test_me_without_token(api):
    res = await api.client.get("/users/me")
    assert res.status_code == 401 and res.json()["code"] == "UNAUTHORIZED"
    assert res.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("token", ["garbage", "a.b.c"])
async def test_me_with_garbage_token(api, token):
    res = await api.client.get("/users/me", headers=bearer(token))
    assert res.status_code == 401 and res.json()["code"] == "INVALID_TOKEN"


async def test_me_rejects_refresh_token(api):
    _, auth = await api.verified_user()
    res = await api.client.get("/users/me", headers=bearer(auth["refreshToken"]))
    assert res.status_code == 401


async def test_me_rejects_expired_token(api):
    _, auth = await api.verified_user()
    expired, _ = create_token(
        subject=auth["user"]["_id"],
        token_type="access",
        secret=TEST_JWT_SECRET,
        ttl=timedelta(seconds=-5),
    )
    res = await api.client.get("/users/me", headers=bearer(expired))
    assert res.status_code == 401


async def test_me_rejects_token_signed_with_other_secret(api):
    _, auth = await api.verified_user()
    forged, _ = create_token(
        subject=auth["user"]["_id"], token_type="access", secret="y" * 40, ttl=timedelta(minutes=5)
    )
    res = await api.client.get("/users/me", headers=bearer(forged))
    assert res.status_code == 401


async def test_me_rejects_token_for_deleted_user(api):
    email, auth = await api.verified_user()
    await (await _user(email)).delete()
    res = await api.client.get("/users/me", headers=bearer(auth["accessToken"]))
    assert res.status_code == 401


# ============================================================ refresh & logout


async def test_refresh_rotates_tokens(api):
    _, auth = await api.verified_user()
    res = await api.client.post("/auth/refresh", json={"refreshToken": auth["refreshToken"]})
    assert res.status_code == 200
    new = res.json()
    assert new["refreshToken"] != auth["refreshToken"]
    me = await api.client.get("/users/me", headers=bearer(new["accessToken"]))
    assert me.status_code == 200


async def test_reusing_old_refresh_token_revokes_every_session(api):
    _, auth = await api.verified_user()
    rotated = (
        await api.client.post("/auth/refresh", json={"refreshToken": auth["refreshToken"]})
    ).json()
    # Attacker replays the old token -> rejected, and the legitimate new one dies too.
    replay = await api.client.post("/auth/refresh", json={"refreshToken": auth["refreshToken"]})
    assert replay.status_code == 401
    after = await api.client.post("/auth/refresh", json={"refreshToken": rotated["refreshToken"]})
    assert after.status_code == 401


@pytest.mark.parametrize("which", ["access", "garbage"])
async def test_refresh_rejects_non_refresh_tokens(api, which):
    _, auth = await api.verified_user()
    token = auth["accessToken"] if which == "access" else "garbage"
    res = await api.client.post("/auth/refresh", json={"refreshToken": token})
    assert res.status_code == 401 and res.json()["code"] == "INVALID_TOKEN"


async def test_refresh_validation(api):
    res = await api.client.post("/auth/refresh", json={})
    assert res.status_code == 422


async def test_logout_revokes_refresh_token(api):
    _, auth = await api.verified_user()
    out = await api.client.post("/auth/logout", json={"refreshToken": auth["refreshToken"]})
    assert out.status_code == 204
    res = await api.client.post("/auth/refresh", json={"refreshToken": auth["refreshToken"]})
    assert res.status_code == 401


async def test_logout_with_bad_token_is_harmless(api):
    res = await api.client.post("/auth/logout", json={"refreshToken": "garbage"})
    assert res.status_code == 204


# ============================================================ forgot & reset password


async def test_password_reset_full_flow(api):
    email, auth = await api.verified_user()
    res = await api.client.post("/auth/forgot-password", json={"email": email})
    assert res.status_code == 200
    token = api.outbox.reset_token_for(email)
    assert "/reset-password?token=" in api.outbox.last_to(email).text

    res = await api.client.post(
        "/auth/reset-password", json={"token": token, "password": "NewPassword99"}
    )
    assert res.status_code == 200

    old = await api.client.post("/users/login", json={"email": email, "password": STRONG_PASSWORD})
    new = await api.client.post("/users/login", json={"email": email, "password": "NewPassword99"})
    assert old.status_code == 401 and new.status_code == 200
    # Sessions from before the reset are revoked.
    stale = await api.client.post("/auth/refresh", json={"refreshToken": auth["refreshToken"]})
    assert stale.status_code == 401


async def test_reset_token_is_single_use(api):
    email, _ = await api.verified_user()
    await api.client.post("/auth/forgot-password", json={"email": email})
    token = api.outbox.reset_token_for(email)
    body = {"token": token, "password": "NewPassword99"}
    assert (await api.client.post("/auth/reset-password", json=body)).status_code == 200
    again = await api.client.post("/auth/reset-password", json=body)
    assert again.status_code == 400 and again.json()["code"] == "INVALID_TOKEN"


async def test_expired_reset_token_rejected(api):
    email, _ = await api.verified_user()
    await api.client.post("/auth/forgot-password", json={"email": email})
    user = await _user(email)
    user.password_reset.expires_at = utcnow() - timedelta(seconds=1)
    await user.save()
    res = await api.client.post(
        "/auth/reset-password",
        json={"token": api.outbox.reset_token_for(email), "password": "NewPassword99"},
    )
    assert res.status_code == 400


async def test_unknown_reset_token_rejected(api):
    res = await api.client.post(
        "/auth/reset-password", json={"token": "x" * 43, "password": "NewPassword99"}
    )
    assert res.status_code == 400 and res.json()["code"] == "INVALID_TOKEN"


async def test_reset_requires_strong_password(api):
    res = await api.client.post(
        "/auth/reset-password", json={"token": "x" * 43, "password": "weak"}
    )
    assert res.status_code == 422


async def test_forgot_password_never_reveals_accounts(api):
    email, _ = await api.verified_user()
    known = await api.client.post("/auth/forgot-password", json={"email": email})
    unknown = await api.client.post("/auth/forgot-password", json={"email": "no@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert not [m for m in api.outbox.messages if m.to == "no@example.com"]


async def test_reset_also_verifies_unverified_email(api):
    email = await api.signup()
    await api.client.post("/auth/forgot-password", json={"email": email})
    await api.client.post(
        "/auth/reset-password",
        json={"token": api.outbox.reset_token_for(email), "password": "NewPassword99"},
    )
    assert (await _user(email)).email.verified is True


# ============================================================ storage & rate limits


async def test_refresh_sessions_are_stored_with_expiry(api):
    email, _ = await api.verified_user()
    user = await _user(email)
    session = await RefreshSession.find_one({"user_id": user.id})
    assert session is not None and session.revoked_at is None
    assert session.expires_at > utcnow() + timedelta(days=6)


async def test_login_is_rate_limited(api):
    limited_app = create_app(api.settings.model_copy(update={"rate_limit_enabled": True}))
    async with AsyncClient(transport=ASGITransport(app=limited_app), base_url="http://t") as c:
        body = {"email": "ghost@example.com", "password": "Wrong12345"}
        codes = [(await c.post("/users/login", json=body)).status_code for _ in range(11)]
        assert codes[:10] == [401] * 10
        assert codes[10] == 429
        res = await c.post("/users/login", json=body)
        assert res.json()["code"] == "RATE_LIMITED"
        assert int(res.headers["Retry-After"]) > 0


async def test_admin_cannot_be_created_but_can_exist(api):
    """Admins are created by operators directly in the database, never via the API."""
    admin = User(
        name="Ops",
        email={"value": "ops@example.com", "verified": True},
        password_hash=hash_password(STRONG_PASSWORD),
        role="admin",
    )
    await admin.insert()
    res = await api.client.post(
        "/users/login", json={"email": "ops@example.com", "password": STRONG_PASSWORD}
    )
    assert res.status_code == 200 and res.json()["user"]["role"] == "admin"


# ============================================================ role checks


async def test_require_role_allows_and_forbids(api):
    from fastapi import Depends

    from app.core.deps import require_role
    from app.models.user import Role

    @api.app.get("/_test/teachers-only")
    async def teachers_only(user: User = Depends(require_role(Role.TEACHER))):
        return {"ok": True}

    _, teacher = await api.verified_user(role="teacher")
    _, student = await api.verified_user(role="student")
    ok = await api.client.get("/_test/teachers-only", headers=bearer(teacher["accessToken"]))
    no = await api.client.get("/_test/teachers-only", headers=bearer(student["accessToken"]))
    anon = await api.client.get("/_test/teachers-only")
    assert ok.status_code == 200
    assert no.status_code == 403 and no.json()["code"] == "FORBIDDEN"
    assert anon.status_code == 401


# ============================================================ Swagger examples


def _example(api, path: str) -> dict:
    schema = api.app.openapi()
    ref = schema["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
    name = ref["$ref"].rsplit("/", 1)[-1]
    return schema["components"]["schemas"][name]["example"]


async def test_swagger_examples_are_valid_requests(api):
    """'Try it out' in /docs must work without editing the example body."""
    signup = _example(api, "/users/signup")
    assert (await api.client.post("/users/signup", json=signup)).status_code == 201
    code = api.outbox.code_for(signup["email"])
    verify = {**_example(api, "/users/verify-email"), "code": code}
    assert (await api.client.post("/users/verify-email", json=verify)).status_code == 200
    login = _example(api, "/users/login")
    assert (await api.client.post("/users/login", json=login)).status_code == 200
    resend = _example(api, "/users/resend-verification-code")
    assert (
        await api.client.post("/users/resend-verification-code", json=resend)
    ).status_code == 200
