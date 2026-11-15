# Postman collection

End-to-end API checks, one folder per milestone phase. Each request has test scripts
(status code + key fields), and the negative cases (401/404/409/422) sit next to the
happy path.

| File | What it is |
|---|---|
| `PersonaLearn.postman_collection.json` | The requests and their tests |
| `local.postman_environment.json` | `baseUrl` (http://localhost:8000) and the test `password` |
| `fixtures/` | Files the upload requests send |

## Run it in the Postman app

1. Start the server with the stand-in providers (`AI_PROVIDER=fake`, `EMAIL_BACKEND=console`).
2. **Import** both JSON files, then pick the **PersonaLearn local** environment (top right).
3. Open the collection → **Run**. Untick everything after **1. Auth → Sign up (new student)**
   for the first pass, or just run that one request.
4. Copy the 6-digit code from the server terminal (box `DEV EMAIL (not actually sent)`)
   into the collection variable **verificationCode** (collection → *Variables* tab).
5. Run the whole collection. Tokens, IDs and file links are captured automatically.

If an upload request shows the file as missing, open it and re-select the file from
`postman/fixtures/` (the Postman app sometimes needs this once after import).

## Run it from the command line (Newman)

```bash
npm install -g newman
newman run postman/PersonaLearn.postman_collection.json \
  -e postman/local.postman_environment.json \
  --working-dir postman \
  --env-var verificationCode=123456   # the code from the server terminal
```

Sign-up needs a code from the server terminal, so a fully unattended run (CI) has to
wait for Milestone 5, which adds a test-only way to read it.

## Keeping it up to date

When an endpoint is added or changed, add or update its request in the matching folder,
including at least one negative case. Folder `9. Clean up` stays last.
