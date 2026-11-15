# Infy Voice — Stage 4 provider architecture

Every stage of the voice loop is an env-configured provider. Nothing is
hardcoded in business logic; `pipeline.TutorSession` only sees the
`LLM`/`TTS`/`Retriever` interfaces and the browser sees a command stream.

## Provider matrix

| Stage | Env var | Options | Default | Where |
|---|---|---|---|---|
| Voice path | `VOICE_MODE` | `auto`, `live`, `cascade` | `auto` | `backend.py` — validated, unknown values warn + `auto` |
| Realtime voice | `GEMINI_API_KEY` | Gemini Live (speech-to-speech) | — | `backend.py` `/api/tutor/live` mints single-use ephemeral tokens; key never reaches the browser |
| STT | `STT_PROVIDER` | `webspeech` | `webspeech` | Browser-side Web Speech in cascade mode; live mode uses Gemini's native ASR (no STT provider needed) |
| LLM | `LLM_PROVIDER` | `llamacpp`, `ollama`, `gemini`, `mock` | `llamacpp` | `providers.make_llm` — unknown name → `ValueError` |
| LLM fallback | `LLM_FALLBACK` | any LLM provider name | — | `FallbackLLM`: only when the primary produced **zero** output does the fallback answer (no mid-answer swap, no silent dual-answer) |
| TTS | `TTS_PROVIDER` | `edge`, `browser`, `mock` | `edge` | `providers.make_tts` — unknown name → `ValueError` |
| Retrieval | `RETRIEVAL_PROVIDER` | `fts`, `null` | `fts` | `providers.make_retriever`; `null` = prompt-only A/B harness |

Optional model/base overrides: `GGUF_MODEL`, `OLLAMA_MODEL`,
`OLLAMA_BASE`, `GEMINI_MODEL`. `LIVE_MAX_SESSION_MINUTES` caps a live
session (spending guard; default 10).

## Secrets

- `GEMINI_API_KEY` lives only in `.env` (gitignored); the frontend receives
  a single-use ephemeral token, never the key — the key goes in the
  `x-goog-api-key` header only, never in URLs or error strings
  (verified by `test_gemini_key_only_in_header_and_never_in_errors`).
- `describe()`/health report only presence (`gemini: true`), never values
  (`test_describe_reports_presence_not_value`).
- `test_no_secrets_in_tracked_files_or_frontend_bundle` scans git-tracked
  files + the built bundle for key patterns.
- `.env` must NOT be committed; rotate the key if it was ever pasted in
  chat/screenshots.

## Error handling and fallback behavior

- Unknown provider name → `ValueError` at startup (fail fast, not silent).
- Unknown `VOICE_MODE` → warning + `auto`.
- Primary LLM dies mid-spawn/before output → `FallbackLLM` answers with
  the fallback provider; if the primary already emitted output the error
  propagates (never silently swaps mid-answer).
- Live mode: mint/setup/mic failure sets `failed` → frontend drops to
  cascade automatically (local pipeline, no vendor). Live WS close →
  re-mint + `session_resumption` handle reconnect with backoff (≤15 s).
- Worker stall → 40 s watchdog kills the turn cleanly ("please ask again").
- Timeouts: httpx client timeouts on hosted calls; stall watchdog on the
  local worker.

## STT decision (measured, not integrated for its own sake)

- **Web Speech (current, cascade):** free, zero setup, decent Hindi —
  but cannot hear "infy" (renders "in face"/"and fish"/"इन फिर"). Bare
  "stop/start/continue" rescue covers commands; accuracy ceiling reached.
- **Gemini Live (implemented):** native ASR inside speech-to-speech —
  the wake-word problem disappears because commands are spoken, not
  wake-worded. This is the real fix, already shipped behind `VOICE_MODE`.
- **Deepgram:** keyword-boosting exists for wake words (~$0.004/min+);
  not integrated — adds a paid dependency to fix what live mode already
  fixes. Revisit only if cascade must stay the primary mode.
- **Vosk:** free, offline, grammar-constrained "infy" possible, but lower
  general accuracy and adds a VAD/audio pipeline; not justified while
  live mode exists.

## Migration notes

- `VOICE_MODE=cascade` = full local pipeline (no vendor calls, free).
- `VOICE_MODE=live` = forces live; fails visibly if no key.
- `auto` = live when `GEMINI_API_KEY` present, else cascade.
- OpenAI realtime adapter: not built — adding one means implementing
  the same `mint → WS → PCM` shape in `frontend/src/live.ts` +
  `backend.py` live section; interfaces already isolate this.
