# Infy Voice — Stage 1 Acceptance Test Matrix

Branch `stage1-stabilize` · rollback tag `checkpoint-real-mic-20261028` ·
backend: `python backend.py` (GGUF_GPU_LAYERS=0) · http://localhost:8200

Legend: PASS (actually run, evidence below) · FAIL · BLOCKED (cannot run —
reason given). "Automated" = scripted over the real backend/model/socket.
"Real-mic" = human speaking in Chrome. Sources: `backend_run.log`,
`turns_out.log`, `echo_out.log`, `/api/metrics`, user screenshots/reports.

| ID | Test | Type | Result | Evidence |
|----|------|------|--------|----------|
| S1-01 | 10 consecutive user turns | Automated | **PASS** | `tests/ws_turns.py`: 10/10 PASS, first-audio p50 2283 ms, max 2543 ms (2026-10-29) |
| S1-01m | 10 consecutive real-mic turns | Real-mic | **BLOCKED** | Needs user voice pass; partial: user drove 12 turns in one session (log: 12 turns, 0 cancelled) |
| S1-02 | Idle 30 s — no unsolicited speech | Automated | **PASS** | Echo guard verified in `ws_echo.py` ("own-voice transcript produced no turn"); greeting fires once only |
| S1-02m | Idle silence, real mic | Real-mic | **PARTIAL** | No phantom turns reported in user sessions; no dedicated 30 s silent window logged |
| S1-03 | No duplicate turns/transcripts | Automated | **PASS** | `ws_echo.py` dedup PASS; Chrome re-fire bug found live + fixed (F14 — drop while turn in flight) |
| S1-04 | No assistant-audio feedback | Automated | **PASS** | `ws_echo.py` echo-guard PASS; speaking gate + `_is_echo` + `ECHO_TAIL_S` |
| S1-04m | No feedback on speakers | Real-mic | **PARTIAL** | Works on your runs so far; speaker-echo suppression is heuristic (no AEC in Web Speech) |
| S1-05 | Consecutive questions, no reload | Real-mic | **PASS** | Server log 2026-10-29: turns 6–12 all spawned and answered in one session |
| S1-06 | Interruption / barge-in | Automated | **PASS** | `interrupted` frame + `_cancel_turn` verified in earlier E2E; interim ≥4-word gate |
| S1-06m | Barge-in while Infy speaks | Real-mic | **BLOCKED** | Needs user to talk over a live answer; not yet demonstrated in this round |
| S1-07 | `infy stop` latches | Real-mic | **PASS** | Server log: `cmd=stop` (interim) → `state=stopped`; UI showed "paused — mic still on" |
| S1-08 | `infy start` resumes | Real-mic | **PARTIAL** | Resume verified via `cmd=start` from `continue`; `infy start` itself often arrives mangled ("in face start", "and fish") — widened variants + bare-word rescue applied, further confirmation needed |
| S1-09 | Silence recovery | Real-mic | **PARTIAL** | `no-speech` restarts handled client-side; watch live pass |
| S1-10 | Reconnect recovery | Real-mic | **BLOCKED** | Needs deliberate WS drop/reconnect test (kill backend mid-session); session-resume store exists (F9 fix) |
| S1-11 | Hindi ↔ English switching | Real-mic | **PARTIAL** | हिं toggle + Hindi greeting verified earlier; Devanagari transcripts not yet logged this round |
| S1-12 | Audio playback | Real-mic | **PASS** | WebAudio device error → `<audio>` fallback (F16); user confirmed audible answers |
| S1-13 | Turn never wedges | Automated | **PASS** | `_LLM_STALL_S=40` watchdog observed firing on a live wedged turn → error state → listening |
| S1-14 | Commands don't false-trigger | Automated | **PASS** | `test_commands.py` 8/8 — embedded "bus stop" etc. never fire; bare verbs ≤3 words do |

## Open items for the user's live pass
1. 10 real-mic turns in a row (say 10 short chapter questions back-to-back).
2. Talk over Infy mid-answer (barge-in).
3. "infy stop" → "infy start" ×2 cycles.
4. Kill `python.exe` mid-session, restart, reload page — check resume.
5. हिं mode: ask "हवा कैसे बनती है", then switch back to EN question.
