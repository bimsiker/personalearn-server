# User acceptance testing (UAT)

Acceptance scenarios from the PersonaLearn Server Plan (section 9.4), run **in the real
frontend** against this server. Record every run in the results log at the bottom.

## How to run a round

1. Server: stand-in providers (`STORAGE_BACKEND=local`, `AI_PROVIDER=fake`,
   `EMAIL_BACKEND=console`, `VIDEO_PROVIDER=fake`), then
   `python -m uvicorn app.main:app --reload`.
2. Frontend: `npm run dev` in `personalearna-frontend` (http://localhost:5173).
3. Keep the server terminal visible: verification codes and reset links print there.
4. Keep MongoDB Compass open on the `personalearn` database to check the stored data.
5. Work through the scenarios, mark each step Pass/Fail, and add a row to the log.

**Until Milestone 1 Phase 6 (frontend wiring) is done**, the frontend still shows mock
data. Run the same steps through Swagger (http://localhost:8000/docs) or the Postman
collection instead and note "API only" in the log.

Useful test data: `postman/fixtures/sample-syllabus.pdf` (valid), any `.txt`/`.png`
(wrong type), a file over 25 MB (too large), and a scanned PDF with no text layer.

---

## Milestone 1: student learning loop

### S1: Sign up and verify email

| # | Step | Expected |
|---|---|---|
| 1 | Sign up as a student with a new email and a password like `Sunflower42` | Taken to Verify Email; a 6-digit code appears in the server terminal |
| 2 | Enter a wrong code | "Invalid code" message; you stay on the page |
| 3 | Press **Resend** twice quickly | First works; second asks you to wait (60 s cooldown) |
| 4 | Enter the newest code | Logged in and taken to the app |
| 5 | Try to sign up again with the same email | "Email already in use" |
| 6 | Compass: `users` | One document; `email.verified: true`; a `password_hash`, no plain password |

### S2: Log in, unverified login, password reset

| # | Step | Expected |
|---|---|---|
| 1 | Log out, log in with the right password | Logged in |
| 2 | Log in with a wrong password | Generic "email or password is wrong" (doesn't say which) |
| 3 | Sign up a second account, don't verify, log in | Sent to Verify Email |
| 4 | **Forgot password** with your email, then with an unknown email | Same message both times; reset link printed in the server terminal only for yours |
| 5 | Open the reset link, set a new password, log in with it | Works; old password no longer works |
| 6 | Open the same reset link again | "Link expired or already used" |

### S3: Upload a curriculum

| # | Step | Expected |
|---|---|---|
| 1 | Upload `sample-syllabus.pdf` with a title | Appears at the top of **Uploads** |
| 2 | Upload a `.docx` | Accepted; shows as a Word file |
| 3 | Upload a `.txt` or image | Clear "unsupported file type" message; nothing added |
| 4 | Upload a file over 25 MB | "File too large" message |
| 5 | **View PDF** on the upload | PDF opens in a new tab |
| 6 | Wait 5+ minutes, reuse that tab's URL | Link expired |
| 7 | Compass: `curricula` | Has `extracted_text`, `file.key` is random (not your filename) |

### S4: Personalize and generate a course

| # | Step | Expected |
|---|---|---|
| 1 | Open an upload → **Personalize** → pick style, pace, **Text**, add a note | A "building your course" state shows for a few seconds |
| 2 | Wait | Course overview: title, summary, objectives, modules and lessons |
| 3 | Repeat on another upload with **Video** or **Mixed** | Avatar step shown before generating; course builds |
| 4 | Repeat with **Audio** | No avatar step |
| 5 | Personalize an upload that already has a course | Told it's already personalized; taken to the course |
| 6 | Personalize with `#fail` in the extra notes (fake AI only) | Failed state with a "try again" option; retrying with new answers builds the course |
| 7 | Upload a scanned PDF (no text) and personalize it | Clear message that the file has no readable text |
| 8 | Compass: `user_curricula` | One per material; `generation_status: "ready"`; quizzes have `correct_index` |

### S5: Read a lesson, take a quiz, see progress *(quiz/progress arrive in Phase 5)*

| # | Step | Expected |
|---|---|---|
| 1 | From the overview, open lesson 1 | Lesson text shows; reading progress bar moves |
| 2 | **Next lesson** across a module boundary | Goes to the next module's first lesson |
| 3 | **Take the Quiz**, choose a wrong answer | Marked wrong; explanation shown |
| 4 | Choose the right answer | Marked correct; lesson marked complete |
| 5 | Back to the overview | Progress % increased; lesson shows a tick |

### S6: Delete and refresh

| # | Step | Expected |
|---|---|---|
| 1 | Refresh the browser on the Curriculum Detail, Overview and Lesson pages | Each page reloads with its data (no blank page) |
| 2 | Delete an upload that has a course | It disappears from Uploads; its course link now says not found |
| 3 | Compass | The `curricula` document and its `user_curricula` are gone; the file is gone from `uploads/` |

---

## Later milestones (filled in when they start)

| ID | Scenario | Milestone |
|---|---|---|
| T1 | Teacher sees their invite (class) code | 3 |
| T2 | Student joins a class with the code | 3 |
| T3 | Teacher assigns material to a student | 3 |
| T4 | Teacher dashboard shows progress, never the student's private preferences | 3 |
| P1 | Parent adds a child profile | 3 |
| P2 | Parent uploads material for the child | 3 |
| P3 | Parent uses "Learn as {child}" | 3 |
| P4 | Parent sees the child's progress | 3 |
| V1 | Choose a library avatar | 4 |
| V2 | Upload a custom avatar (consent required) | 4 |
| V3 | A video lesson generates and plays | 4 |

---

## Results log

Add one row per scenario per round. Result: Pass, Fail, or Partial (explain in notes).

| Date | Tester | Build / branch | Mode | Scenario | Result | Notes |
|---|---|---|---|---|---|---|
| | | | Frontend / API only | S1 | | |
