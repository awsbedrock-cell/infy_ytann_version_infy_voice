# Infy Voice — Stage 1 Audit (reproduced failures)

Reproduced over the real websocket (`tests/ws_echo.py`) and real Chrome runs
(`tests/e2e/drive.py`, fake-audio capture + CDP). No mocks.

## Failures observed by the user, with root causes

### F1 — Infy speaks/answers without a user question (self-echo loop)
- **Cause A:** The mic hears Infy's own TTS through the speakers. Web Speech
  transcribes it; ≥3-word interims triggered `interrupt()`, and the final
  transcript became a new `ask()` — Infy answering its own voice, repeatedly.
- **Cause B:** Verbatim/4-gram echo matching is insufficient — STT garbles
  speaker audio ("is the force stop on page top" heard from a sentence about
  force/pressure), so text-similarity alone can never fully suppress it.
- **Fix:** transcripts are *gated while Infy speaks* (commands still honored);
  barge-in is detected on an echo-cancelled mic channel (getUserMedia
  `echoCancellation:true` + energy VAD), not on STT transcripts. Server keeps
  a secondary echo/dedup guard as defense-in-depth.

### F2 — Follow-up question produces no response
- **Cause A:** `sr.onresult` read only `results[last]`; Chrome batches a
  final (Q1) with the next interim (Q2) in one event → the final was
  silently dropped. Fixed: iterate `resultIndex → length`.
- **Cause B:** a cancelled turn's llama generation kept running in the
  executor thread; the next `stream()` raced the same non-reentrant Llama
  instance → hang/garbage. Fixed: per-call generation lock.
- **Cause C:** after a turn, recognition could be dead while status read
  "listening". Fixed: server "listening" now re-arms STT if it's down.

### F3 — Duplicate answers / stale transcripts
- Chrome re-fires identical finals (observed in continuous mode). Fixed:
  client + server dedup of identical normalized finals within 4 s.

### F4 — Noise interrupts speech
- ≥3-word interim → interrupt was too easy; TTS echo and room noise
  qualified. Now ≥4 words AND only when VAD (real mic energy, echo-cancelled)
  confirms a human is actually talking.

## Verified after fixes (real socket + real Chrome)
- Echo transcript → **no turn**. Duplicate final → **one turn**.
  Second consecutive question → answered. (`tests/ws_echo.py` ALL PASS)
- Browser run: 2 consecutive turns, grounded answers, first audio 2.1/2.4 s,
  a "stop"-containing phrase did NOT trigger stop.

## Second audit (branch `stage1-stabilize`) — more causes found in code
- **F5 — mic reopened while audio still playing.** Server sent `listening` when
  it finished *sending* audio; Chrome played the queue for seconds more, so
  the gate opened onto Infy's own voice. Fix: server waits for client
  `playback_done` (turn-scoped) before `listening`.
- **F6 — stale / out-of-order audio after interrupt.** In-flight
  `decodeAudioData` completions played after a cut, and parallel decodes
  could reorder sentences. Fix: player generation token + serialized decode
  chain; every message carries `turn`, old turns dropped.
- **F7 — real follow-ups dropped as "echo".** Text echo window was 25 s; a
  question repeating 4 words of the answer was ignored. Fix: echo check only
  while audible + 2 s tail.
- **F8 — every Hindi final after the first treated as duplicate.**
  Normalizer kept only `[a-z0-9]`, so Devanagari normalized to "". Fix:
  keep U+0900–097F.
- **F9 — typed questions ignored with mic off; reconnect re-greeted and lost
  history.** Fix: `ask` from text works any time; `session_id` resume store.
- **F10 — empty LLM reply = silence.** Seen live (Gemini, turn 2 of 10).
  Fix: spoken fallback line; `LLM_FALLBACK` for errors before first token.
- **F11 — deaf gaps after silence.** Each Chrome `no-speech` end grew the
  restart backoff up to 5 s, missing question starts. Fix: backoff only on
  real errors.
- Hosted model `gemini-2.5-flash` returns 404 for new keys; free-tier quota
  returned 429 after ~40 calls.

## Verified on `stage1-stabilize` (commands actually run)
- `pytest tests` → 32 passed (regressions for F5–F11, commands, protocol).
- `npm run build` → passes.
- `tests/ws_turns.py --voice`, Ollama qwen3:8b + edge-tts, real backend:
  greeting + **10/10 consecutive turns**, first audio p50 12.8 s, max 22.6 s.
- `tests/ws_turns.py`, Gemini 3.5 Flash: 9/10 (turn 2 empty reply → F10).
- **Not verified:** real-mic Chrome acceptance (`tests/e2e/loopback.py`). The
  one run was invalid — Windows speakers were muted at 0%, mic heard only
  noise floor (RMS 0.006 with and without playback, `tests/e2e/mic_probe.py`).

## Remaining known limits (honest)
- Web Speech STT has **no AEC**: on speakers (no headphones), echo suppression
  is heuristic during playback; full-duplex barge-in quality is capped.
  Headphones eliminate the whole class. True full-duplex needs a
  speech-to-speech API or a custom audio path (Stage 2).
- 3B local model: good Hindi, occasionally awkward phrasing.
- Fake-audio-capture test audio is lossy through Google's recognizer —
  synthetic-voice E2E under-measures; human-voice manual pass still required.

## 2026-10-29 real-mic session round 2 (branch stage1-stabilize @1fab72c)

New defects reproduced from the live Chrome mic + fixed:

- **F12 — wake-word remainder became a question.** `continue Infi
  continue` → wake stripped → `continue continue` answered as a question
  instead of resuming. Fix: `_bare_command` applies to the wake-stripped
  remainder too (`commands.py` + `commands.ts`).
- **F13 — STT drops/mangles the wake word.** `infy start` → `start`,
  `in face`, `in fee`, `in fi`, `contin`. Fixes: bare whole-utterance
  command verbs (≤3 core words) now fire; wake variants extended
  (`in face|in feet|in faith|in fi|in fie|in fit|win fi`).
- **F14 — Chrome re-fires the same final seconds apart** → each re-fire
  cancelled the in-flight turn via `ask` → answer restarted forever.
  Fix: identical final dropped while its turn runs (plus 10 s tail).
- **F15 — model stream wedge = stuck "speaking" forever.** llama worker
  silently stalled (earlier: WinError 0xe06d7363). Fix: `_LLM_STALL_S=40`
  watchdog on the sentence stream → honest error state → back to
  listening. Observed firing correctly on a live wedged turn.
- **F16 — WebAudio device error = silence.** "The AudioContext
  encountered an error from the audio device". Fix: `SentencePlayer`
  falls back to a plain `<audio>` element (`webAudioDead` flag);
  interrupt clears element players too. User confirmed audio audible.
- **Observability**: `log.info` on transcripts/drops/ask in `pipeline.py`
  + `logging.basicConfig` in `backend.py` (uvicorn was silencing the
  `infy` logger, so earlier diagnostics printed nothing).

Verified 2026-10-29 (live backend, real sockets, real model):
- `tests/ws_turns.py`: **10/10 consecutive turns PASS**, greeting PASS,
  first-audio p50 2283 ms, max 2543 ms (llamacpp, GGUF_GPU_LAYERS=0).
- `tests/ws_echo.py`: ALL PASS (echo guard, dedup, second turn).
- `pytest`: **49/49**.
- Real-mic partial verification (Uday): answers audible; `infy stop`
  latches reliably (bare `stop` interim fires too); `infy start` resumes
  but STT frequently mangles the wake word — bare `continue`/`start`
  rescues it. Server log: 12 turns, 0 cancelled, stop+start latched.
- **BLOCKED (needs user)**: full 10-consecutive-mic-turn pass, barge-in
  with real voice, Hindi voice questions, reconnect recovery. STT quality
  on `infy` is a Web Speech limit — real fix is a provider swap
  (Stage 4: Deepgram keyword boost or Vosk grammar; see ARCHITECTURE.md).
