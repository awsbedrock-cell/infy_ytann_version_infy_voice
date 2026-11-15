# Varad Learn — Standalone Voice Demo (for Deepti)

A self-contained Infy voice tutor: React + Vite + TypeScript frontend, FastAPI
backend, SQLite database with real Varad chapter content.

## Voice architecture (provider-agnostic, no Varad credentials needed)

```
browser ──Web Speech──▶ transcripts ──WS──▶ :8200/ws/tutor
                                             │ deterministic commands (commands.py)
                                             │ FTS5 retrieval (rag.py)
                                             │ LLM stream  (providers.py: llamacpp|ollama|gemini)
                                             │ per-sentence TTS (edge-tts|browser)
browser ◀──mp3 sentence frames + text──────┘
```

- **STT**: Chrome Web Speech API (free, streaming, en-IN / hi-IN)
- **LLM**: `LLM_PROVIDER` in `.env` — `llamacpp` (in-process GGUF, default),
  `ollama` (local server), or `gemini` (REST, needs key). Default model:
  `models/Qwen2.5-3B-Instruct-Q4_K_M.gguf` (~2 GB, runs on CPU)
- **TTS**: `TTS_PROVIDER` — `edge` (free neural voices) or `browser` (offline)
- **Commands** (`commands.py` ⇄ `frontend/src/infy/commands.ts`): **“Infy start”**,
  **“Infy stop”**, **“Infy repeat”** (says the last answer again), **“Infy Hindi”** /
  **“Infy English”** (switch reply language), **“quiz me”**. Hindi/Devanagari forms
  work too (“infy ruk jao”, “इन्फी दोबारा बोलो”, “infy hindi mein bolo”). A bare
  whole-utterance “stop”/“start” is also accepted because STT often drops the
  wake word; repeat/Hindi/English need “Infy” and must be the whole utterance,
  so “infy, what is pressure in hindi” is still a question. Stop cancels
  generation + audio for real.
- **Grounding**: per-question FTS5 retrieval over page blocks + curated ask_qa
  (not prompt-stuffing). Answers cite sources; unknown → honest abstention.
- **Latency**: `/api/metrics` reports first-token and first-audio p50/p95.

The legacy Gemini Live native-audio path (`/api/tutor/live`) still works if
`GEMINI_API_KEY` is set — it is optional, not required.

---

## 1. What you need first

- **Python 3.10+** — check: `python --version`
- **Node.js 18+** — check: `node --version` (frontend build only)
- **Local LLM model** — `hf download Qwen/Qwen2.5-3B-Instruct-GGUF
  Qwen2.5-3B-Instruct-Q4_K_M.gguf --local-dir models` (~2 GB, one-time;
  `start.bat` does this automatically). Runs on CPU — no GPU needed.
- **Chrome or Edge** — speech/mic APIs need a Chromium browser
- **A microphone** — laptop mic is fine
- **Internet** — edge-tts voice + one-time model download need it
- No API keys required for the default stack

## 2. Install (one time)

```bash
pip install -r requirements.txt

# Optional but big: GPU acceleration on NVIDIA machines (~4x faster
# first-audio). Replace the CPU wheel with the CUDA build:
pip install llama-cpp-python --force-reinstall --no-deps \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124

python seed_db.py                          # builds varad.db
cd frontend && npm install && npm run build # builds frontend/dist
cd ..
```

## 3. Run

```bash
python backend.py        # serves the built app + API on :8200
```

or just **double-click `start.bat`**. Leave that window open.

**Dev mode (React HMR):**
```bash
# terminal 1: python backend.py        (API + images on :8200)
# terminal 2: cd frontend && npm run dev   → http://localhost:5173
#             (vite proxies /api and /clients to :8200)
```

## 4. Use it

1. Open **http://localhost:8200** in Chrome/Edge.
2. You see one chapter — *"Pressure, Winds, Storms, and Cyclones"*.
3. Pick a chapter in the dropdown (4 real chapters: 3 science, 1 math).
   Tap the purple 🎤 button and **allow microphone access**.
4. Infy greets you out loud. Ask questions by voice:
   - "how are winds formed?"
   - "what is pressure?"
   - "why do cyclones lose strength on land?"
5. **Interrupt it** — just start talking while it speaks.
6. **Commands:** say **"Infy start"** to wake it, **"Infy stop"** to stop
   listening + speaking instantly (also "infy ruk jao", "infy band karo").
   Also **"Infy repeat"**, **"Infy Hindi"**, **"Infy English"**. "stop" inside a
   normal sentence ("what is a bus stop") does NOT stop the tutor.
7. Or type a question in the box and press Ask — it joins the same
   conversation.
8. Tap ⏹ to end the session.

## 5. What's inside

| File | What it is |
|---|---|
| `backend.py` | The server — `/api/chapter`, `/api/course`, `/api/configuration`, `/api/health`, `/api/tutor/live` (the Gemini token mint), and serves the page |
| `seed_db.py` | Builds `varad.db` from `content.json` |
| `varad.db` | SQLite DB — 4 real chapters, 2 real courses |
| `content.json` | Exact copy of the Varad content |
| `frontend/` | **React + Vite + TS app** — `src/App.tsx` (tabs + chapter fetch), `src/views.tsx` (Read/KeyPoints/Q&A/Quiz/Flashcards/FunFacts), `src/infy/` (**independent Infy component** — `useInfy` hook owns mint→WS→mic→playback→commands→state machine; `<Infy chapterId={1} />` drops into any page), `src/live.ts` (socket transport) — `npm run dev` or `npm run build` → served by backend |
| `clients/varad/` | Chapter images — page scans + figures (served at `/clients/varad/…`) |
| `index.html` / `live.js` / `chapter.js` | Old plain-JS single-page version — kept as fallback (backend serves it if `frontend/dist` is missing) |
| `voice.js` | Spare parts — offline voice/commands/matcher (not wired in, kept for experiments) |
| `chapter.js` | Chapter 1 data used by the page |
| `requirements.txt` | fastapi, uvicorn, python-dotenv |
| `start.bat` | Double-click launcher (seeds DB + starts server) |
| `.env` | Gemini API key — **private** |

## 6. How the voice actually works (same as production)

```
browser ──POST──▶ :8200/api/tutor/live     (mints ephemeral token;
                                            Gemini key stays on server)
browser ──WSS──▶ Google Gemini Live        (direct socket, audio never
                                            proxied through the backend)
mic ──16kHz PCM──▶ Gemini ──24kHz PCM──▶ speakers
talk over Infy ──▶ server "interrupted" ──▶ playback clears instantly
```

## 7. Things to experiment with

- `backend.py → LIVE_CFG` — change the bot: `voice` (try other Gemini
  voices), `greeting`, `vad` sensitivity, `languages`,
  `max_session_minutes`. Restart the server to apply.
- `backend.py → _LIVE_SYS_DEFAULT` — the bot's personality/rules.
- `frontend/src/live.ts → LIVE_API` — point at a different mint (e.g.
  `https://varad-core.onrender.com` to compare against production).
  Rebuild or use `npm run dev` to see the change.
- `frontend/src/live.ts → SETUP` — what the client sends in setup: `vad`,
  `activityHandling` (uncomment `NO_INTERRUPTION` to disable barge-in),
  `resumption`, `transcription`.
- `voice.js` — offline-answer matcher + command phrase lists (spare parts).

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `couldn't connect — is backend.py running on :8200?` | Start the server (step 3) |
| Mint error `503 — set GEMINI_API_KEY` | The `.env` file is missing or emptied — recopy the `GEMINI_API_KEY=…` line |
| Mint error `502 — live token error` | Key expired/quota — check the key in `.env` |
| No mic prompt / no voice in | Use Chrome or Edge, and allow the mic permission |
| No sound out | Check speaker volume; the browser tab must not be muted |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| Port already in use | Something else is on 8200 — edit `port=8200` in `backend.py` |

## 9. Limits vs the real product

- No login, no student accounts, no progress saving.
- One chapter shown (the DB has all 4 — add a selector if you want).
- Answers come from the chapter content + model — the offline keyword
  matcher in `voice.js` exists for comparison but isn't wired in.

## 10. Sharing it

Copy the whole folder. Before sharing beyond your own machines, delete
`.env` (it has a real API key) and tell the other person to put their
own `GEMINI_API_KEY=…` line in a new `.env` file there.
