# personalearn-server

Backend API for PersonaLearn (FastAPI + MongoDB/Beanie). It turns uploaded curricula into
personalized courses and connects to AI, storage, email and avatar-video providers. Each
provider has a local stand-in, so the server runs with **only MongoDB** during development.

## Setup

Needs **Python 3.12 or newer** (3.13 recommended).

### Windows

```bash
cd personalearn-server
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env
```

Activate it (optional when you call `.venv/Scripts/python` directly):
PowerShell `.venv\Scripts\Activate.ps1`, Git Bash `source .venv/Scripts/activate`.

### macOS

macOS ships Python 3.9, which is too old, so use [uv](https://docs.astral.sh/uv/) to get 3.13.
Keep the virtual environment **outside OneDrive** (thousands of small files shouldn't sync):

```bash
python3 -m pip install --user uv                  # once; installs to ~/Library/Python/3.9/bin
export PATH="$HOME/Library/Python/3.9/bin:$PATH"  # add this line to ~/.zshrc to keep it
uv venv --python 3.13 ~/.venvs/personalearn-server
cd personalearn-server
VIRTUAL_ENV=~/.venvs/personalearn-server uv pip install -e ".[dev]"
cp .env.example .env
source ~/.venvs/personalearn-server/bin/activate  # in each new terminal
```

In VS Code: **Python: Select Interpreter** → **Enter interpreter path…** →
`~/.venvs/personalearn-server/bin/python`.

### Then edit `.env`

- `MONGO_URI`: your MongoDB Atlas connection string, or `mongodb://localhost:27017`.
  The name must be exactly `MONGO_URI`. Comments start with `#` (not `//`).
  For Atlas, add your computer's IP under **Security → Network Access** first.
- `JWT_SECRET`: your own random value. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
  Each developer uses their own; never share or commit it.

## Run

With the virtual environment activated (Windows without activating: prefix with
`.venv/Scripts/`):

```bash
python -m uvicorn app.main:app --reload
```

- Health check: http://localhost:8000/health
- Interactive API docs (Swagger): http://localhost:8000/docs. To call protected endpoints,
  click **Authorize** and paste the `accessToken` from `/users/login` or `/users/verify-email`.

With `EMAIL_BACKEND=console`, emails such as verification codes and reset links are
**printed in the server terminal** instead of being sent.

## Try it in Swagger

1. Start the server, then open http://localhost:8000 (it redirects to `/docs`).
2. `POST /users/signup` → **Try it out** → **Execute**. The example body is valid as is.
3. Look in the **server terminal** for the box `DEV EMAIL (not actually sent)` and copy the
   6-digit code. Codes expire after 10 minutes; `POST /users/resend-verification-code`
   sends a new one (once a minute).
4. `POST /users/verify-email` with that email and code → copy `accessToken` from the response.
5. Click **Authorize** (top right), paste the token, then try `GET /users/me` and the
   `curriculum` endpoints. Access tokens last 15 minutes; log in again or use
   `/auth/refresh` when it expires.

## Endpoints so far

| Method | Path                                                 | Purpose                                                                                                                                                                                                                                                                            |
| ------ | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/health`                                          | Service + database status                                                                                                                                                                                                                                                          |
| POST   | `/users/signup`                                    | Create account (student/parent/teacher); emails a 6-digit code                                                                                                                                                                                                                     |
| POST   | `/users/verify-email`                              | Confirm code → login tokens                                                                                                                                                                                                                                                       |
| POST   | `/users/resend-verification-code`                  | New code (60 s cooldown)                                                                                                                                                                                                                                                           |
| POST   | `/users/login`                                     | Email + password → tokens (403 `EMAIL_NOT_VERIFIED` if unverified)                                                                                                                                                                                                              |
| GET    | `/users/me`                                        | Current user (needs `Authorization: Bearer <accessToken>`)                                                                                                                                                                                                                       |
| POST   | `/auth/refresh`                                    | New token pair; the old refresh token stops working                                                                                                                                                                                                                                |
| POST   | `/auth/logout`                                     | Revoke a refresh token                                                                                                                                                                                                                                                             |
| POST   | `/auth/forgot-password`                            | Email a reset link (same answer whether or not the account exists)                                                                                                                                                                                                                 |
| POST   | `/auth/reset-password`                             | Set a new password with the link's token                                                                                                                                                                                                                                           |
| POST   | `/curriculum/create`                               | Upload a PDF, Word `.docx` or `.doc` (multipart: `title`, `description`, `file`; max 25 MB)                                                                                                                                                                              |
| GET    | `/curriculum`                                      | Your uploads, newest first                                                                                                                                                                                                                                                         |
| GET    | `/curriculum/{id}`                                 | One upload (404 if it isn't yours)                                                                                                                                                                                                                                                 |
| GET    | `/curriculum/{id}/file`                            | Short-lived link to the original file (5 minutes). PDFs open in the browser; Word files download. `/curriculum/{id}/pdf` is an alias                                                                                                                                             |
| DELETE | `/curriculum/{id}`                                 | Delete an upload and its file                                                                                                                                                                                                                                                      |
| GET    | `/files/download?token=…`                         | Serves the file from that link (no login header needed)                                                                                                                                                                                                                            |
| POST   | `/curriculum/{id}/enroll`                          | Personalize: `learningStyle`, `learningPace`, `mediaPreference` (+ optional `avatarId`, `voiceId`, `additionalPreferences`). Returns **202** with `generationStatus: "queued"`; the course is built in the background. Enrolling again after a failure retries |
| GET    | `/curriculum/{id}/user-curriculum`                 | Your personalized course. Poll every 2-3 s until `generationStatus` is `ready` or `failed` (then `generationError` says why)                                                                                                                                               |
| GET    | `/user-curricula/{ucId}/lessons/{module}/{lesson}` | One lesson (numbers start at 1) with `previous`/`next` positions. 409 `COURSE_NOT_READY` while generating                                                                                                                                                                    |
| POST   | `/curriculum/{ucId}/attempt`                       | Answer a quiz question: `moduleNumber`, `lessonNumber`, `quizIndex`, `selectedIndex` (0-3). Returns whether it's right, the lesson's state and course progress                                                                                                               |
| POST   | `/curriculum/{ucId}/explain`                       | Ask the tutor about your answer (answer the question first, or 409 `ANSWER_FIRST`). Hints only until the answer is revealed                                                                                                                                                  |
| POST   | `/user-curricula/{ucId}/lessons/{m}/{l}/complete`  | Mark a lesson done (safe to repeat). Returns progress and the `next` lesson                                                                                                                                                                                                  |
| GET    | `/progress/{ucId}`                                 | Progress summary: % done, lessons completed, average quiz score, where to resume                                                                                                                                                                                             |

Every error response has the shape `{"code": "...", "message": "..."}`.

Quiz answers are checked on the server. A question's correct answer and explanation are
only sent once it is **revealed**: after a right answer, or after `QUIZ_REVEAL_AFTER_ATTEMPTS`
wrong tries (default 3). A lesson's quiz score is the % of questions right on the first try.
A lesson completes when every question is passed (or it's marked complete); course progress
is completed lessons ÷ lessons. Every answer is also stored in `quiz_attempts`. With `AI_PROVIDER=fake` a course is a fixed sample built around the material's
title, ready after a few seconds (`FAKE_AI_DELAY_SECONDS`). Type `#fail` in the extra notes
to see a failed generation.

## Postman and UAT

- `postman/`: a collection with tests for every endpoint, including the negative cases. See
  [postman/README.md](postman/README.md).
- `docs/UAT.md`: acceptance scenarios to run in the real frontend, with a results log.

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

With the virtual environment activated:

```bash
python -m pytest              # all tests (unit + API)
python -m pytest --cov=app    # with coverage
python -m pytest -m live      # opt-in: real Atlas connection check
python -m ruff check . && python -m ruff format --check .
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
- Auth endpoints, uploads and course generation are rate-limited per IP (in-memory for
  now; Redis later).
- Uploaded text is treated as untrusted. Every AI response is validated (4 options, answer
  index in range, size limits from config) and retried once before the course is marked
  failed; learners only ever see a safe failure message.
- Uploads may be PDF, Word `.docx` or Word `.doc`. The type is detected from the file's
  content, not its name. Password-protected, damaged and macro-enabled documents (`.docm`,
  or `.doc` with macros) are refused. Files are stored under random names and only served
  through links that expire after 5 minutes.
- Text extraction: pypdf for PDF; zipfile + defusedxml for `.docx` (blocks malicious XML and
  zip bombs); olefile + a small [MS-DOC] reader for `.doc`. All are permissively licensed
  (no AGPL). Scanned, image-only files upload fine but report `hasText: false`.
- `tests/fixtures/` holds a few small Word-made documents, since genuine `.doc` files can't
  be created in pure Python.
