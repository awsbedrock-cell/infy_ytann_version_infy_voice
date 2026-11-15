# Stage 2 — Cost & Security Controls

## Voice modes

| Mode | What it is | Needs | Cost |
|---|---|---|---|
| `cascade` | Local pipeline: Web Speech → RAG → llamacpp → edge-tts | nothing | ₹0 |
| `live` | Gemini Live speech-to-speech (native audio, server VAD) | `GEMINI_API_KEY` | per-minute (below) |
| `auto` (default) | live when a key exists, else cascade | — | — |

## Verified Gemini pricing (ai.google.dev/gemini-api/docs/pricing, checked 2026-10-29)

For the exact model we mint (`gemini-2.5-flash-native-audio-preview-12-2025`):

| | Free tier | Paid tier (per 1M tokens) |
|---|---|---|
| Input | free | $0.50 text · **$3.00 audio** |
| Output | free | $2.00 text · **$12.00 audio** |
| Data used to improve products | yes | no |

Free-tier quota has low rate limits — fine for this dev/testing setup;
billing is opt-in in AI Studio, so nothing charges without you enabling
it. At ~25 audio tokens/sec the paid-tier math lands ≈ **$0.005/min in +
$0.018/min out ≈ $0.023/min** of elapsed conversation.

## Cost estimate (Live mode, worst-case paid-tier, continuous speech)

| Usage | Est. cost |
|---|---|
| 30 min session | ~$0.70 |
| 1 hour | ~$1.40 |
| 10 hours | ~$14 |

Real tutoring speech duty-cycle is ~40–60% — actual costs land lower.
**Cascade mode is always free** and remains the zero-cost floor.

## Spending guards (implemented)

1. `LIVE_MAX_SESSION_MINUTES` (default 10): the minted ephemeral token
   *itself expires* — server-side, cannot be bypassed.
2. The client enforces the same cap and auto-ends the session.
3. Ephemeral tokens: `uses: 1`, `new_session_expire_time` = 1 min —
   a leaked token is useless after the session starts or one minute
   passes.
4. `VOICE_MODE=cascade` fully disables live usage — set it to guarantee
   zero spend.

## What leaves the machine

| Mode | Audio | Text | Course content |
|---|---|---|---|
| cascade | → Google (Web Speech STT) only | question text → Microsoft edge-tts | never leaves (local SQLite FTS) |
| live | → Google Gemini Live | transcripts → Google | chapter context baked into the system prompt at mint time |

## Secrets hygiene

- `GEMINI_API_KEY` lives only in `.env` (gitignored), read server-side.
- The mint sends it as the `x-goog-api-key` **header** — never in a URL
  (URLs leak into logs/exception text).
- The browser only ever sees the single-use ephemeral token.
- `tests/test_providers.py::test_no_secrets_in_bundle` scans the built
  bundle for the key pattern.
