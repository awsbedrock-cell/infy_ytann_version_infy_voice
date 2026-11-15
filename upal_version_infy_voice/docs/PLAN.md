# Infy Voice — Phased Implementation Plan

Branch `stage1-stabilize`; `main` is the rollback. Nothing is pushed without
Nishant's OK. "Verified" below means a command was actually run and its output
recorded; everything else is open.

## Stage 1 — Stabilize the cascade
Done in code (verified by unit tests, `pytest tests` → 32 passed):
- [x] Turn ids on every server message; client drops audio/text of old turns
- [x] Server stays `speaking` until the browser reports `playback_done`
- [x] Echo check only while audible + 2 s tail (no 25 s window eating follow-ups)
- [x] Devanagari-safe normalization (Hindi finals no longer "duplicates")
- [x] Typed questions work with the mic off; session resume on reconnect (no re-greet)
- [x] Stale `interrupt` ignored; client errors sanitized
- [x] Empty LLM reply → spoken "please ask again" (was silence)
- [x] `LLM_FALLBACK` provider for 429/outage before first token
- [x] Hindi wake/stop/start commands (Python + TS)
- [x] Ordered, generation-safe audio player; adaptive VAD barge-in; STT
      restart without deaf gaps on `no-speech`
- [x] Frontend `npm run build` passes

Acceptance (each must be shown by `tests/e2e/loopback.py` with the real mic
and speakers at normal volume, plus one human pass):

| # | Criterion | Measure | Status |
|---|---|---|---|
| 1 | 10 consecutive turns | 10/10 answered, transcript matches prompt (≥50% word overlap) | WebSocket-level 10/10 *verified* (Ollama); real-mic **not verified** |
| 2 | No phantom turns | 0 "you" turns that match no prompt during whole run | not verified |
| 3 | Barge-in | VAD interrupt <1 s after speech starts; 0 stale chunks; new question answered | not verified |
| 4 | Stop/start | "infy stop" → stopped; speech ignored while stopped; "infy start" → listening, no re-greet | not verified |
| 5 | Silence recovery | after 75 s silence, next question answered; 0 unsolicited speech | not verified |
| 6 | Reconnect | socket drop → `ready: resumed` <15 s, no re-greet, next question answered | not verified |
| 7 | Hindi ↔ English | 2 Hindi turns transcribed+answered in Devanagari; Hindi stop; English answer after switch | not verified |
| 8 | Human pass | Nishant: 10 turns + 1 interrupt + stop/start + 1 Hindi turn | not done |

## Stage 2 — Hybrid voice (see ARCHITECTURE.md)
- `VOICE_MODE=live|cascade`; Gemini Live adapter with `retrieve` tool → our
  FTS5; `sources` over WS; ephemeral tokens minted server-side.
- **Accept:** mic-end→first-audio p50 < 1.5 s on 15 prompts; barge-in stop
  < 500 ms; `eval_grounding.py` pass rate ≥ cascade's; cost/session logged.

## Stage 3 — Educational intelligence
- Eval set `eval/qa_<chapter>.jsonl`: in-chapter, out-of-chapter,
  wrong-premise, Hindi; gold page ids. Metrics: retrieval precision@3,
  citation accuracy, refusal rate on out-of-chapter, answer correctness
  (LLM judge + human spot check of 10%).
- Chunk audit of `fts_chunk`: coverage, duplicates, chunk length histogram.
- **Accept:** baseline numbers recorded; each change (prompt / retrieval /
  fine-tune) shows a measured delta on the same set. No training claims
  without dataset + config + eval results.

## Stage 4 — Provider configuration
- `LLM_PROVIDER`, `LLM_FALLBACK`, `TTS_PROVIDER`, `STT_PROVIDER`,
  `VOICE_MODE`; keys only in env/secret store.
- **Accept:** each provider swap covered by a test; `grep` of the built
  bundle and logs finds no key material.

## Stage 5 — Product quality
- Persistent history (db), explicit mic permission flow, visible
  listening/thinking/speaking states, cancel button, error/recovery paths,
  local-only privacy mode, usage/cost meter for hosted paths, mobile layout.
- **Accept:** checklist run on Chrome desktop, Android Chrome and one
  desktop wrapper; cost meter matches vendor usage metadata within 10%.
