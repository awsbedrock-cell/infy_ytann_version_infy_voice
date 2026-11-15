# ============================================================================
# backend.py — STANDALONE mini-backend for the voice demo.
# Hardcoded replica of the real Varad stack's relevant slice:
#   be/app.py  →  /api/configuration, /api/course, /api/chapter,
#                 /api/tutor/live (the Gemini-Live ephemeral-token mint)
#   be/sources.py → SQLite reads (varad.db, seeded by seed_db.py)
# No shared code, no imports from the main repo. GEMINI_API_KEY comes from
# .env in THIS folder — never hardcode it here.
#
#   pip install -r requirements.txt
#   python seed_db.py        # once, builds varad.db
#   python backend.py        # serves API + frontend on http://localhost:8200
# ============================================================================
import json, os, sqlite3, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

import logging

import metrics
import pipeline

log = logging.getLogger("infy")
logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

app = FastAPI(title="Varad Learn — standalone voice demo")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# --- hardcoded live-tutor config (copy of be/client/varad/configuration.json
#     tutor.live — the fields the mint actually uses) -------------------------
# VOICE_MODE: which voice path the frontend should prefer.
#   auto    — live when GEMINI_API_KEY is set, else cascade (default)
#   live    — Gemini realtime speech-to-speech (needs GEMINI_API_KEY)
#   cascade — local pipeline: Web Speech STT -> RAG -> LLM -> TTS (always works)
VOICE_MODE = os.getenv("VOICE_MODE", "auto").strip().lower()
if VOICE_MODE not in ("auto", "live", "cascade"):
    log.warning("unknown VOICE_MODE=%r — falling back to 'auto'", VOICE_MODE)
    VOICE_MODE = "auto"

LIVE_CFG = {
    "enabled": True,
    "model": "gemini-2.5-flash-native-audio-preview-12-2025",
    "voice": "Puck",
    # spending guard: token dies after this many minutes; the client enforces
    # the same cap so a forgotten tab can't burn usage indefinitely
    "max_session_minutes": int(os.getenv("LIVE_MAX_SESSION_MINUTES", "10")),
    "ws_url": "wss://generativelanguage.googleapis.com/ws/"
              "google.ai.generativelanguage.v1beta.GenerativeService."
              "BidiGenerateContentConstrained",
    "greeting": "Say hi in one short, plain sentence — then stop and wait. "
                "Do not list topics, do not suggest questions, do not show off.",
    "vad": {
        "start_of_speech_sensitivity": "START_SENSITIVITY_HIGH",
        "end_of_speech_sensitivity": "END_SENSITIVITY_HIGH",
        "prefix_padding_ms": 120,
        "silence_duration_ms": 300,
    },
    "session_resumption": True,
    "languages": ["English", "Hindi"],
    "default_language": "English",
}

_LIVE_SYS_DEFAULT = (
    "You are a warm, playful voice tutor for a Grade {grade} student, "
    "speaking out loud in a live conversation.\n"
    "Rules:\n"
    "- Answer the question directly and briefly first — 2-4 spoken "
    "sentences, the way a favorite teacher would explain it out loud.\n"
    "- Plain spoken words only — no markdown, bullet lists, symbols, or "
    "anything a voice cannot say.\n"
    "- Say only your final answer out loud — never narrate your "
    "thinking, planning, or what you are about to say.\n"
    "- Reply in the same language the student just used. If they "
    "switch languages, switch with them — never translate.\n"
    "- Answer from the chapter context when it is there. If the "
    "question is outside the chapter but a fair general question, "
    "answer it briefly and correctly and say it is beyond this "
    "chapter — never invent facts about the chapter.\n"
    "- If the student says 'infy stop', 'stop', 'ruk jao' or 'bas', "
    "reply with one short word like 'Paused' and then stay silent — "
    "do not answer or comment until they say 'infy start'.\n"
    "- Commands start with 'infy': 'infy repeat' or 'infy dobara bolo' "
    "means say your previous answer again; 'infy hindi' / 'infy english' "
    "means reply only in that language from now on — confirm in one short "
    "sentence.\n"
    "- Keep the conversation natural — greet only once at the start "
    "and do not end every reply with a question.")


def db() -> sqlite3.Connection:
    c = sqlite3.connect(BASE / "varad.db")
    c.row_factory = sqlite3.Row
    return c


def _doc(row) -> dict:
    return json.loads(row["doc"])


def _chapter_context(ch: dict) -> str:
    """Same grounding recipe the real mint uses: title/summary/key_points/
    ask_qa + page text, trimmed for the system prompt."""
    parts = [f"Chapter: {ch.get('title','')}", f"Summary: {ch.get('summary','')}"]
    kps = [k if isinstance(k, str) else k.get("text", "")
           for k in (ch.get("key_points") or [])]
    if kps:
        parts.append("Key points: " + " | ".join(kps))
    for p in (ch.get("pages") or [])[:18]:
        for b in p.get("blocks") or []:
            t = (b.get("text") or "").strip()
            if t:
                parts.append(t)
    ctx = "\n".join(parts)
    return ctx[:12000]


@app.get("/api/configuration")
def configuration():
    return {"tutor": {"enabled": True, "live": {"enabled": True}},
            "name": "Varad Learn (standalone demo)"}


@app.get("/api/course")
def courses():
    with db() as c:
        return {"items": [_doc(r) for r in c.execute("SELECT * FROM course")],
                "total": 2}


@app.get("/api/chapter")
def chapters():
    with db() as c:
        rows = c.execute("SELECT id,title,subject,grade,chapter_number,slug,"
                         "summary,doc FROM chapter").fetchall()
    return {"items": [_doc(r) for r in rows], "total": len(rows)}


@app.get("/api/chapter/{cid}")
def chapter(cid: int):
    with db() as c:
        r = c.execute("SELECT doc FROM chapter WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "no chapter")
    return _doc(r)


@app.post("/api/tutor/live")
@app.post("/api/tutor/live/")
async def tutor_live(body: dict | None = None):
    """The real mint, copied: POST /v1beta/auth_tokens → ephemeral token the
    browser uses straight against Google's WS. Key never leaves this file's
    process."""
    if not LIVE_CFG["enabled"]:
        raise HTTPException(404, "live tutor disabled")
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "set GEMINI_API_KEY in for_deepti/.env")

    body = body or {}
    chapter = None
    if body.get("chapter_id"):
        with db() as c:
            r = c.execute("SELECT doc FROM chapter WHERE id=?",
                          (body["chapter_id"],)).fetchone()
        chapter = _doc(r) if r else None

    grade = body.get("grade") or (chapter or {}).get("grade") or 8
    system = _LIVE_SYS_DEFAULT.replace("{grade}", str(grade))
    langs = LIVE_CFG["languages"]
    if langs:
        system += ("\n\nThe student may speak " + " or ".join(langs) +
                   ". Always reply in the language they just used; default "
                   f"to {LIVE_CFG['default_language']} only if unclear.")
    # Language anchor: the client tells us the student's selected language.
    # Native-audio models drift toward the accent they hear (Indian English
    # gets read as Hindi) — pin the reply language so only a REAL switch
    # (a whole sentence in the other language, or an explicit request)
    # changes it, and the session snaps back on the next utterance.
    pref = (body.get("lang") or "").strip().lower()
    if pref.startswith("en"):
        system += (
            "\n\nLANGUAGE LOCK: The student selected English. ALWAYS reply "
            "in English — an Indian accent is still English, never treat it "
            "as Hindi. Switch to Hindi only if the student speaks a complete "
            "sentence in Hindi or explicitly asks for Hindi, and return to "
            "English on their very next English utterance.")
    elif pref.startswith("hi"):
        system += (
            "\n\nLANGUAGE LOCK: The student selected Hindi. ALWAYS reply "
            "in Hindi — switch to English only if the student speaks a "
            "complete English sentence or explicitly asks for English, and "
            "return to Hindi on their next Hindi utterance.")
    if chapter:
        system += "\n\nCONTEXT:\n" + _chapter_context(chapter)

    now = datetime.now(timezone.utc)
    model = LIVE_CFG["model"]
    # key in a header, never the URL (URLs end up in exception text/logs)
    url = "https://generativelanguage.googleapis.com/v1beta/auth_tokens"
    payload = json.dumps({
        "uses": 1,
        "expire_time": (now + timedelta(
            minutes=LIVE_CFG["max_session_minutes"])).isoformat(),
        "new_session_expire_time": (now + timedelta(minutes=1)).isoformat(),
        # model pinned in the token (the constrained endpoint ignores
        # setup.model) — same trick as the real backend.
        "bidiGenerateContentSetup": {"model": "models/" + model},
        "fieldMask": "model",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json",
                         "x-goog-api-key": key}),
                timeout=20) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        log.warning("live token HTTP %s", e.code)
        raise HTTPException(502, f"live token error: HTTP {e.code}")
    except Exception as e:
        log.warning("live token failed: %s", type(e).__name__)
        raise HTTPException(502, "live token error")

    metrics.record_live_mint()
    return {
        "token": tok.get("name"),
        "ws_url": LIVE_CFG["ws_url"],
        "model": model,
        "voice": LIVE_CFG["voice"],
        "system_instruction": system,
        "max_session_minutes": LIVE_CFG["max_session_minutes"],
        "vad": LIVE_CFG["vad"],
        "greeting": LIVE_CFG["greeting"],
        "session_resumption": LIVE_CFG["session_resumption"],
    }


@app.get("/api/health")
def health():
    import providers
    return {"ok": True, "client": "varad-standalone",
            "voice_mode": VOICE_MODE,
            "live_max_minutes": LIVE_CFG["max_session_minutes"],
            "providers": providers.describe()}


# --- /ws/tutor — the provider-agnostic voice pipeline ----------------------
# STT happens in the browser (Web Speech), transcripts stream in here; each
# turn runs RAG → LLM stream → per-sentence TTS → mp3 frames back. Providers
# are chosen by .env: LLM_PROVIDER=ollama|gemini, TTS_PROVIDER=edge|browser.
_CHAPTER_DOCS: dict[int, dict] = {}


def _chapter_docs() -> dict[int, dict]:
    if not _CHAPTER_DOCS:
        with db() as c:
            for r in c.execute("SELECT doc FROM chapter"):
                d = _doc(r)
                if d.get("id"):
                    _CHAPTER_DOCS[d["id"]] = d
    return _CHAPTER_DOCS


@app.websocket("/ws/tutor")
async def tutor_ws(ws: WebSocket):
    await pipeline.handle_ws(ws, _chapter_docs())


@app.get("/api/metrics")
def get_metrics():
    return metrics.report()


@app.post("/api/metrics/reset")
def reset_metrics():
    metrics.reset()
    return {"ok": True}


# --- frontend: serve this folder so opening localhost:8200 runs the demo ----
# frontend: serve the built React app (frontend/dist) when present —
# fall back to the plain index.html so the folder still works before
# `npm run build` has ever been run.
DIST = BASE / "frontend" / "dist"
app.mount("/clients", StaticFiles(directory=BASE / "clients"), name="clients")
app.mount("/", StaticFiles(directory=DIST if DIST.is_dir() else BASE, html=True), name="static")

@app.on_event("startup")
def _warm():
    # parse chapter docs at boot, not on the first websocket hello —
    # a cold _chapter_docs() cost ~0.8s of connect latency
    _chapter_docs()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.getenv("PORT", "8200")))
