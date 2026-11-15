# Infy Voice — security review (Stage 6)

## Credentials
- `GEMINI_API_KEY` lives only in `.env` (gitignored — verified
  `git check-ignore .env`). Loaded server-side only.
- Live mode: `/api/tutor/live` mints a **single-use ephemeral token**
  (`uses:1`, ~1-min `new_session_expire_time`, model pinned). The browser
  never sees the API key — it receives the short-lived token for the
  constrained `BidiGenerateContentConstrained` websocket.
- Key travels only in the `x-goog-api-key` header — never in URLs, logs,
  or error bodies (test: `test_gemini_key_only_in_header_and_never_in_errors`).
- Automated scan: `test_no_secrets_in_tracked_files_or_frontend_bundle`
  greps every git-tracked file + built bundle for key patterns.
- **Exposure note:** the key was pasted in this Devin chat once — rotate
  it at aistudio.google.com if the project is ever shared/distributed.

## Network surface
- Uvicorn binds `127.0.0.1:8200` — loopback only, not LAN-reachable.
- CORS `allow_origins=["*"]` — acceptable while loopback-bound; if the
  port is ever exposed, restrict origins first.
- No auth on `/api/*` — fine for single-user localhost; adding auth is a
  precondition for any remote deployment.

## Logging & privacy
- Transcripts, command decisions and drops are logged to
  `backend_run.log` (gitignored `*.log`). No keys, no tokens logged.
- Learning progress (`store["progress"]`) is in-memory only — session
  ends → gone. No student profile is persisted anywhere.
- Session resumption handles expire with the websocket session.

## Abuse & cost guards
- Live tokens are single-use + time-capped (`LIVE_MAX_SESSION_MINUTES`,
  client-side timer mirrors it).
- `/api/metrics` exposes `live_sessions_minted` + a cost upper bound —
  check it to spot unexpected vendor usage.
- No paid billing is enabled by this project; the Gemini key is the free
  dev tier unless the owner opts in at AI Studio.

## Remaining risks (honest)
- Anyone on this machine can call `/api/tutor/live` and mint tokens —
  acceptable on a personal dev box, not on a shared host.
- Web Speech (cascade) and Gemini Live both send audio to a cloud
  recognizer — documented in docs/COST.md data-flow table.
- `eval_*.py` spawn llama workers that can orphan (see DEPLOYMENT.md).
