# Infy Voice — deploy / release / rollback

Local dev & testing deployment only. Not wired into the production Varad
app yet; do not expose port 8200 beyond localhost.

## Run

```bat
cd D:\for_deepti\infy_voice
pip install -r requirements.txt
python backend.py
```

Chrome → `http://localhost:8200` → **Ctrl+F5** (fresh bundle). Mic orb →
talk. `VOICE_MODE=auto` + a valid `GEMINI_API_KEY` → live mode
(Gemini speech-to-speech); otherwise cascade (local pipeline).

## Configuration (.env, gitignored)

| Var | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | live mode; mints single-use ephemeral tokens server-side |
| `VOICE_MODE` | `auto` | `auto` / `live` / `cascade` |
| `LIVE_MAX_SESSION_MINUTES` | `10` | spending guard — token + client timer |
| `LLM_PROVIDER` | `llamacpp` | `llamacpp` / `ollama` / `gemini` / `mock` |
| `LLM_FALLBACK` | — | provider used when primary dies before answering |
| `TTS_PROVIDER` | `edge` | `edge` / `browser` / `mock` |
| `RETRIEVAL_PROVIDER` | `fts` | `fts` / `null` (prompt-only A/B) |
| `GGUF_GPU_LAYERS` | auto | GPU layers for the local model (0 = CPU) |

`GET /api/health` — config sanity. `GET /api/metrics` — latency p50/p95,
cancelled turns, live-session count, cost upper bound, uptime.

## Release checklist

1. `python -m pytest tests -x -q` — all pass (currently 54).
2. `python tests\ws_acceptance.py` (backend running) — 7/7.
3. `python eval_grounding.py`, `eval_followup.py`, `eval_scenarios.py` —
   real-LLM behaviour, expect ≥4/5 each (wrong-premise is flaky on 3B).
4. Frontend bundle rebuild:
   `frontend\node_modules\@esbuild\win32-x64\esbuild.exe frontend\src\main.tsx
    --bundle --minify --format=esm --jsx=automatic
    --outfile=frontend\dist\assets\index-<tag>.js`
   then repoint the `<script>` tag in `frontend\dist\index.html`.
5. Secret check: `test_no_secrets_in_tracked_files_or_frontend_bundle`.
6. Real-mic pass: question, "quiz me", "infy stop", "infy start",
   interrupt, one Hindi question — in BOTH modes if the key is set.

## Rollback

- **Instant:** `VOICE_MODE=cascade` in `.env` + restart — live path fully
  inert, zero vendor calls.
- **Git:** `git checkout checkpoint-real-mic-20261028` — tag at the last
  verified-working commit (pre-Stage-4/5).
- **Frontend only:** repoint `dist\index.html` to a previous
  `index-*.js` bundle (older bundles kept in `dist/assets/`).

## Known operational issues

- **Orphaned llama workers**: if the backend or an eval is killed hard,
  its `llama_worker` child (~2.5 GB) can survive with a dead parent.
  Symptom: `WinError 1455` / "model failed to load — RAM exhausted" on the
  next turn. Fix: Task Manager → kill `python.exe` running
  `llama_worker.py` whose parent is gone (or reboot). Backend PID is safe
  to leave alone — its own workers are respawned per turn.
- **Live-mode key**: `AQ.` API keys are valid but their lifetime is
  unverified; if mints start 401-ing, regenerate at aistudio.google.com
  and prefer the classic `AIza…` key.
