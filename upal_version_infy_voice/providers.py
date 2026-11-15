"""providers.py — pluggable LLM + TTS backends. Never hardcode a provider in
the pipeline: everything goes through make_llm() / make_tts(), configured by
.env or config defaults below. Adding a provider = one new class here.

LLM providers
    llamacpp — in-process GGUF via llama-cpp-python (default; fully local)
    ollama  — local http://localhost:11434 chat API (if Ollama installed)
    gemini  — Google generateContent REST (needs GEMINI_API_KEY in .env)

TTS providers
    edge    — edge-tts neural voices, streams mp3 (default; no key needed)
    browser — no-op: client uses speechSynthesis instead (offline fallback)
    gemini_live — handled by the legacy /api/tutor/live path, not this file
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import AsyncIterator, Protocol

import httpx

# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------

class LLM(Protocol):
    name: str

    async def stream(self, system: str, history: list[dict],
                     question: str) -> AsyncIterator[str]:
        """Yield text tokens for the model's reply."""
        ...


_THINKING_MODELS = ("qwen3", "deepseek-r1", "gpt-oss", "magistral")


class OllamaLLM:
    name = "ollama"

    def __init__(self, model: str, base: str = "http://localhost:11434"):
        self.model, self.base = model, base.rstrip("/")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "180"))

    def _body(self, msgs: list[dict]) -> dict:
        body = {"model": self.model, "messages": msgs, "stream": True,
                "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "30m"),
                "options": {"num_predict": self.max_tokens,
                            "temperature": 0.4}}
        # reasoning models would otherwise spend seconds "thinking" before
        # the first speakable token
        if self.model.lower().startswith(_THINKING_MODELS):
            body["think"] = False
        return body

    async def warm(self):
        """Load the model into memory before the first real question."""
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                await c.post(f"{self.base}/api/chat", json={
                    "model": self.model, "messages": [],
                    "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "30m")})
        except httpx.HTTPError:
            pass

    async def stream(self, system, history, question):
        msgs = [{"role": "system", "content": system}]
        msgs += history[-12:]
        msgs.append({"role": "user", "content": question})
        in_think = False
        async with httpx.AsyncClient(timeout=120) as c:
            async with c.stream("POST", f"{self.base}/api/chat",
                                json=self._body(msgs)) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"ollama HTTP {r.status_code}")
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = (chunk.get("message") or {}).get("content") or ""
                    # defensive: never speak inline <think> blocks
                    if "<think>" in token:
                        in_think = True
                    if in_think:
                        if "</think>" in token:
                            in_think = False
                            token = token.split("</think>", 1)[1]
                        else:
                            token = ""
                    if token:
                        yield token
                    if chunk.get("done"):
                        return


class LlamaCppLLM:
    """GGUF model isolated in llama_worker.py — no server, no key.

    Why a subprocess: llama.cpp can abort the whole process on a C++
    GGML_ASSERT (observed killing the FastAPI server mid-answer). Isolated,
    a crash costs one turn — the next stream() spawns a fresh worker.
    One worker is shared; streams are serialized by _busy because the
    worker generates one request at a time."""
    name = "llamacpp"
    _proc = None
    _sock = None
    _rfile = None
    _wfile = None
    _lock = __import__("threading").Lock()
    _busy = __import__("threading").Event()   # a stream is reading the socket

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path

    async def warm(self):
        """Spawn the worker + load the GGUF at session start (background,
        errors swallowed) so the first real question doesn't pay the ~10s
        cold model load."""
        try:
            await asyncio.get_event_loop().run_in_executor(None, self._spawn)
        except Exception as e:
            logging.getLogger("infy").warning(
                "llamacpp warm failed: %s", type(e).__name__)

    def _spawn(self):
        """Start the worker if dead; return once it reports ready."""
        import subprocess, sys, socket, time
        from pathlib import Path
        with LlamaCppLLM._lock:
            cls = LlamaCppLLM
            fresh = cls._proc is None or cls._proc.poll() is not None
            if fresh:
                # pick a free port, hand it to the worker
                s = socket.socket(); s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]; s.close()
                env = dict(os.environ)
                env["LLAMA_WORKER_PORT"] = str(port)
                if self.model_path:
                    env["GGUF_MODEL"] = self.model_path
                # share ONE worker across all connections: a second
                # instance reuses this socket instead of holding another
                # 2.3GB model copy
                cls._proc = subprocess.Popen(
                    [sys.executable,
                     str(Path(__file__).parent / "llama_worker.py")],
                    env=env, cwd=str(Path(__file__).parent))
                for f in (cls._rfile, cls._wfile, cls._sock):
                    try: f and f.close()
                    except Exception: pass
                cls._rfile = cls._wfile = cls._sock = None
                # worker binds before loading the model — connect fast
                deadline = time.time() + 15
                while True:
                    try:
                        cls._sock = socket.create_connection(
                            ("127.0.0.1", port), timeout=5)
                        break
                    except OSError:
                        if time.time() > deadline or \
                                cls._proc.poll() is not None:
                            raise RuntimeError("llm worker exited on startup")
                        time.sleep(0.3)
                cls._sock.settimeout(None)   # reads block (model load ~20s)
                cls._rfile = cls._sock.makefile("r", encoding="utf-8")
                cls._wfile = cls._sock.makefile("w", encoding="utf-8")
            if not fresh:
                return   # live worker sends no banner
            line = cls._rfile.readline()   # {"ready":true} or {"error"}
            if not line:
                raise RuntimeError("llm worker exited on startup")
            msg = json.loads(line)
            if "error" in msg:
                raise RuntimeError(f"llm worker: {msg['error']}")
            return

    async def stream(self, system, history, question):
        msgs = [{"role": "system", "content": system}]
        msgs += history[-12:]
        msgs.append({"role": "user", "content": question})
        loop = asyncio.get_event_loop()
        # wait for any previous turn's drain (normally instant)
        t_deadline = loop.time() + 30
        while LlamaCppLLM._busy.is_set():
            if loop.time() > t_deadline:
                break
            await asyncio.sleep(0.05)
        LlamaCppLLM._busy.set()
        # request id scopes the cancel: a stale cancel from an old turn's
        # finally must not kill the next turn's already-queued request
        cls = LlamaCppLLM
        rid = cls._req_seq = getattr(cls, "_req_seq", 0) + 1
        q: asyncio.Queue = asyncio.Queue()
        completed = reading = False

        def _reader():
            """ONE thread owns the socket for this stream — and after a
            cancel it keeps reading as the drain, so no second reader can
            ever race it for the terminal frame (the old readline-in-
            executor + separate drain-thread pair deadlocked ~30s when the
            abandoned read swallowed the 'done')."""
            try:
                while True:
                    line = LlamaCppLLM._rfile.readline()
                    if not line:
                        loop.call_soon_threadsafe(
                            q.put_nowait, {"error": "llm worker died"})
                        return
                    try:
                        m = json.loads(line)
                    except Exception:
                        continue
                    loop.call_soon_threadsafe(q.put_nowait, m)
                    if m.get("done") or "error" in m:
                        return      # tail fully drained — next turn is clean
            except Exception:
                try:
                    loop.call_soon_threadsafe(
                        q.put_nowait, {"error": "socket read failed"})
                except Exception:
                    pass
            finally:
                LlamaCppLLM._busy.clear()

        try:
            await loop.run_in_executor(None, self._spawn)

            def _send():
                cls._wfile.write(json.dumps({
                    "messages": msgs, "id": rid, "max_tokens": 220,
                    "temperature": 0.4}) + "\n")
                cls._wfile.flush()

            import threading
            threading.Thread(target=_reader, daemon=True).start()
            reading = True
            await loop.run_in_executor(None, _send)
            while True:
                msg = await q.get()
                if "token" in msg:
                    yield msg["token"]
                elif msg.get("done"):
                    completed = True
                    return
                elif "error" in msg:
                    raise RuntimeError(f"llm worker: {msg['error']}")
        finally:
            if not completed:
                if reading:
                    # cancelled/errored mid-stream: tell the worker to stop
                    # THIS request only; the same reader thread drains the
                    # tail and clears _busy on the terminal frame
                    try:
                        cls._wfile.write(
                            json.dumps({"cmd": "cancel", "id": rid}) + "\n")
                        cls._wfile.flush()
                    except Exception:
                        pass
                else:
                    LlamaCppLLM._busy.clear()


class GeminiLLM:
    name = "gemini"

    def __init__(self, model: str = "gemini-3.5-flash"):
        self.model = model
        self.key = os.getenv("GEMINI_API_KEY", "").strip()
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY not set")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "180"))

    def _gen_config(self) -> dict:
        cfg = {"maxOutputTokens": self.max_tokens, "temperature": 0.4}
        # 2.5 models think by default (seconds before the first token);
        # Gemini 3.x rejects thinkingBudget=0 with HTTP 400
        if self.model.startswith("gemini-2.5"):
            cfg["thinkingConfig"] = {"thinkingBudget": 0}
        return cfg

    async def stream(self, system, history, question):
        contents = [{"role": "user" if m["role"] == "user" else "model",
                     "parts": [{"text": m["content"]}]}
                    for m in history[-12:]]
        contents.append({"role": "user", "parts": [{"text": question}]})
        # key goes in a header, never the URL: httpx errors embed the URL,
        # and error text can reach logs or the client status line
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:streamGenerateContent?alt=sse")
        body = {"system_instruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": self._gen_config()}
        async with httpx.AsyncClient(timeout=60) as c:
            async with c.stream("POST", url, json=body,
                                headers={"x-goog-api-key": self.key}) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"gemini HTTP {r.status_code}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        chunk = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    for cand in chunk.get("candidates") or []:
                        for part in (cand.get("content") or {}).get("parts") or []:
                            if part.get("text"):
                                yield part["text"]


class FallbackLLM:
    """Try `primary`; if it fails or stays silent before its first token
    (quota/429, outage, timeout) answer the same turn with `fallback`."""

    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback
        self.name = f"{primary.name}+{fallback.name}"

    async def warm(self):
        for llm in (self.primary, self.fallback):
            w = getattr(llm, "warm", None)
            if w:
                await w()

    async def stream(self, system, history, question):
        got = False
        try:
            async for t in self.primary.stream(system, history, question):
                got = True
                yield t
            if got:
                return
        except Exception as e:
            if got:
                raise
            logging.getLogger("infy").warning(
                "LLM %s failed (%s) — answering with %s",
                self.primary.name, type(e).__name__, self.fallback.name)
        async for t in self.fallback.stream(system, history, question):
            yield t


def make_llm(name: str | None = None, model: str | None = None) -> LLM:
    """LLM_PROVIDER picks the primary; optional LLM_FALLBACK (e.g. ollama)
    takes over a turn when the primary errors before answering."""
    llm = _make_one(name, model)
    fb = os.getenv("LLM_FALLBACK", "").strip().lower()
    if name is None and fb and fb != llm.name:
        try:
            return FallbackLLM(llm, _make_one(fb))
        except Exception as e:
            logging.getLogger("infy").warning(
                "LLM_FALLBACK=%s unavailable (%s)", fb, type(e).__name__)
    return llm


def _make_one(name: str | None = None, model: str | None = None) -> LLM:
    name = (name or os.getenv("LLM_PROVIDER") or "llamacpp").lower()
    if name == "llamacpp":
        return LlamaCppLLM(os.getenv("GGUF_MODEL"))
    if name == "ollama":
        return OllamaLLM(model or os.getenv("OLLAMA_MODEL") or "gemma3:4b",
                         os.getenv("OLLAMA_BASE") or "http://localhost:11434")
    if name == "gemini":
        return GeminiLLM(model or os.getenv("GEMINI_MODEL") or "gemini-3.5-flash")
    if name == "mock":
        return MockLLM()
    raise ValueError(f"unknown LLM_PROVIDER {name!r}")


class MockLLM:
    """Deterministic offline LLM for development and tests: no network, no
    key, no model download."""
    name = "mock"

    def __init__(self, reply: str | None = None):
        self.reply = reply or os.getenv("MOCK_LLM_REPLY") or (
            "This is a mock answer about the chapter. Ask me another question.")
        self.calls: list[str] = []

    async def stream(self, system, history, question):
        self.calls.append(question)
        for w in self.reply.split(" "):
            yield w + " "


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------

class TTS(Protocol):
    name: str

    async def speak(self, text: str, language: str) -> AsyncIterator[bytes]:
        """Yield audio bytes (mp3) for one spoken chunk."""
        ...


_EDGE_VOICES = {
    "en": "en-IN-NeerjaExpressiveNeural",
    "hi": "hi-IN-SwaraNeural",
}


class EdgeTTS:
    """Microsoft Edge neural TTS — free, natural, en-IN + hi-IN voices.
    Streams mp3 fragments; callers send one sentence/clause per call."""
    name = "edge"

    def __init__(self):
        import edge_tts  # noqa: F401  (fail fast if not installed)

    async def speak(self, text: str, language: str = "en"):
        import edge_tts
        voice = _EDGE_VOICES.get((language or "en")[:2], _EDGE_VOICES["en"])
        comm = edge_tts.Communicate(text, voice)
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]


class BrowserTTS:
    """Signals the client to speak with window.speechSynthesis — full offline
    fallback when no TTS service is reachable."""
    name = "browser"

    async def speak(self, text: str, language: str = "en"):
        return
        yield  # pragma: no cover


def make_tts(name: str | None = None) -> TTS:
    name = (name or os.getenv("TTS_PROVIDER") or "edge").lower()
    if name == "edge":
        return EdgeTTS()
    if name == "browser":
        return BrowserTTS()
    if name == "mock":
        return MockTTS()
    raise ValueError(f"unknown TTS_PROVIDER {name!r}")


class MockTTS(BrowserTTS):
    """No server audio (client falls back to speechSynthesis); for tests and
    keyless development."""
    name = "mock"


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

class Retriever(Protocol):
    name: str

    def retrieve(self, question: str, chapter_id: int,
                 k: int = 6) -> list[dict]:
        """Passages: dicts with text, kind, page, source, score."""
        ...


class FtsRetriever:
    """SQLite FTS5 over the chapter index in varad.db (rag.py)."""
    name = "fts"

    def retrieve(self, question, chapter_id, k=6):
        import rag
        return rag.retrieve(question, chapter_id, k=k)


class NullRetriever:
    """No evidence — to A/B the value of retrieval against prompt-only."""
    name = "none"

    def retrieve(self, question, chapter_id, k=6):
        return []


def make_retriever(name: str | None = None) -> Retriever:
    name = (name or os.getenv("RETRIEVAL_PROVIDER") or "fts").lower()
    if name == "fts":
        return FtsRetriever()
    if name == "none":
        return NullRetriever()
    raise ValueError(f"unknown RETRIEVAL_PROVIDER {name!r}")


# Speech input runs in the browser (Web Speech API); the server only sees
# transcripts. STT_PROVIDER is reported to the client; "webspeech" is the
# only implemented value until a server-side/live STT adapter exists.
STT_PROVIDERS = ("webspeech",)


def describe() -> dict:
    """Active provider names for health/config endpoints. Reports whether a
    hosted key is present, never its value."""
    stt = (os.getenv("STT_PROVIDER") or "webspeech").lower()
    return {
        "llm": (os.getenv("LLM_PROVIDER") or "llamacpp").lower(),
        "llm_fallback": (os.getenv("LLM_FALLBACK") or "").lower() or None,
        "tts": (os.getenv("TTS_PROVIDER") or "edge").lower(),
        "retrieval": (os.getenv("RETRIEVAL_PROVIDER") or "fts").lower(),
        "stt": stt if stt in STT_PROVIDERS else "webspeech",
        "hosted_keys": {"gemini": bool(os.getenv("GEMINI_API_KEY", "").strip())},
    }


# --------------------------------------------------------------------------
# sentence chunker — turns an LLM token stream into speakable clauses so TTS
# (and thus first audible audio) starts as early as possible.
# --------------------------------------------------------------------------

_BOUNDARY = re2 = __import__("re").compile(r"(?<=[.!?।,;:\u0964])\s+")


async def sentences(tokens: AsyncIterator[str]) -> AsyncIterator[str]:
    """Coalesce a token stream into speakable chunks: yields at punctuation
    boundaries. The FIRST chunk is emitted aggressively — at the first
    punctuation after ~35 chars, or a word cut at ~55 chars — so TTS (and
    thus first audible audio) starts in ~1-2s instead of waiting for a whole
    sentence. Later chunks use normal sentence sizing."""
    buf = ""
    first = True
    async for tok in tokens:
        buf += tok
        parts = _BOUNDARY.split(buf)
        consumed = 0
        for i, p in enumerate(parts[:-1]):
            if not p.strip():
                consumed = i + 1
                continue
            if first and len(p.strip()) < 25:
                break  # too short to be worth a TTS call — keep buffering
            yield p.strip()
            first = False
            consumed = i + 1
        buf = "".join(parts[consumed:])
        # early first-chunk: cut at a comma/clause boundary once we have ~35+
        # chars, or at a word boundary once we pass ~55 chars
        if first:
            if len(buf) >= 35:
                for i in range(35, len(buf)):
                    if buf[i] in ",;:":
                        yield buf[:i].strip()
                        buf = buf[i + 1:].lstrip()
                        first = False
                        break
            if first and len(buf) > 55:
                cut = buf.rfind(" ", 0, 55)
                if cut > 30:
                    yield buf[:cut].strip()
                    buf = buf[cut + 1:]
                    first = False
        # long clause without terminal punctuation → cut at next boundary
        if len(buf) > 140:
            cut = buf.rfind(" ", 0, 140)
            if cut > 60:
                yield buf[:cut].strip()
                buf = buf[cut + 1:]
                first = False
    if buf.strip():
        yield buf.strip()
