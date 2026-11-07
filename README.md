# personalearn-server

Backend API for PersonaLearn (FastAPI + MongoDB/Beanie). It turns uploaded curricula into
personalized courses and connects to AI, storage, email and avatar-video providers. Each
provider has a local stand-in, so the server runs with **only MongoDB** during development.

## Setup (Windows)

```bash
cd personalearn-server
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env
```

Then edit `.env`:

- `MONGO_URI`: your MongoDB Atlas connection string, or `mongodb://localhost:27017`.
- `JWT_SECRET`: your own random value. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
  Each developer uses their own; never share or commit it.

Activate the venv (optional when you call `.venv/Scripts/python` directly):
PowerShell `.venv\Scripts\Activate.ps1`, Git Bash `source .venv/Scripts/activate`.

## Run

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

- Health check: http://localhost:8000/health
- Interactive API docs (Swagger): http://localhost:8000/docs. To call protected endpoints,
  click **Authorize** and paste the `accessToken` from `/users/login` or `/users/verify-email`.

With `EMAIL_BACKEND=console`, emails such as verification codes and reset links are
**printed in the server terminal** instead of being sent.

## Endpoints so far

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service + database status |
| POST | `/users/signup` | Create account (student/parent/teacher); emails a 6-digit code |
| POST | `/users/verify-email` | Confirm code → login tokens |
| POST | `/users/resend-verification-code` | New code (60 s cooldown) |
| POST | `/users/login` | Email + password → tokens (403 `EMAIL_NOT_VERIFIED` if unverified) |
| GET | `/users/me` | Current user (needs `Authorization: Bearer <accessToken>`) |
| POST | `/auth/refresh` | New token pair; the old refresh token stops working |
| POST | `/auth/logout` | Revoke a refresh token |
| POST | `/auth/forgot-password` | Email a reset link (same answer whether or not the account exists) |
| POST | `/auth/reset-password` | Set a new password with the link's token |

Every error response has the shape `{"code": "...", "message": "..."}`.

## Logs

Every request gets one log line with a request ID (also returned as the `X-Request-ID`
header), its status and its duration, plus the user ID when the request is logged in.

- `logs/app.log`: everything (requests, sign-ups, logins, warnings, errors)
- `logs/errors.log`: warnings and errors only. Start here when something goes wrong.

Levels: `INFO` = success, `WARNING` = rejected request (bad password, bad code,
rate limit, 4xx), `ERROR` = server failure (5xx, with full traceback). Files rotate at 5 MB
(5 kept). Passwords, codes, tokens and database credentials are never written to the logs.
Emails are masked (`d***@example.com`).

## Tests

```bash
.venv/Scripts/python -m pytest              # all tests (unit + API)
.venv/Scripts/python -m pytest --cov=app    # with coverage
.venv/Scripts/python -m pytest -m live      # opt-in: real Atlas connection check
.venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format --check .
```

Database tests use a **separate `personalearn_test` database** (from `TEST_MONGO_URI`, or
`MONGO_URI` if that's unset), wiped after each run. Your real data is never touched. If no
database is reachable, those tests are skipped.

## Security notes

- Passwords: argon2id; 8+ characters including a number.
- Verification codes and reset tokens are stored only as keyed hashes; they expire after
  10 and 30 minutes and work once. Wrong codes lock after 5 attempts.
- Access tokens last 15 minutes. Refresh tokens are rotated, and reusing an old one logs out
  every device.
- Login, forgot-password and resend never reveal whether an email is registered.
- The `admin` role can't be chosen at signup.
- Auth endpoints are rate-limited per IP (in-memory for now; Redis later).
