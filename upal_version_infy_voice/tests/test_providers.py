"""Stage 4: provider registry, mock providers, secret hygiene. No hosted key
or network needed."""
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import providers  # noqa: E402
from test_stage1 import make, finish_turn  # noqa: E402

FAKE_KEY = "AIzaFAKEtestKEY"  # intentionally <30 chars so the self-scan ignores it


async def _collect(agen):
    return "".join([t async for t in agen])


def test_mock_llm_selected_by_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    llm = providers.make_llm()
    assert llm.name == "mock"
    out = asyncio.run(_collect(llm.stream("sys", [], "q")))
    assert "mock answer" in out


def test_registry_rejects_unknown_names():
    for fn in (providers.make_llm, providers.make_tts, providers.make_retriever):
        with pytest.raises(ValueError):
            fn("nope")


def test_retriever_swap_reaches_the_session():
    class Stub:
        name = "stub"
        def retrieve(self, q, ch, k=6):
            return [{"text": "x", "kind": "page", "page": 7, "source": "page 7", "score": -9}]
    for retr, want in ((Stub(), ["page 7"]), (providers.NullRetriever(), [])):
        s, io = make()
        s.retriever = retr
        async def run():
            await s.ask("what is air pressure", typed=True)
            await finish_turn(s, io)
        asyncio.run(run())
        src = [m for m in io.msgs if m["type"] == "sources"][-1]["sources"]
        assert [x["source"] for x in src] == want


def test_fts_retriever_is_default(monkeypatch):
    monkeypatch.delenv("RETRIEVAL_PROVIDER", raising=False)
    assert providers.make_retriever().name == "fts"
    assert providers.make_retriever().retrieve("what is air pressure", 1, k=3)


def test_gemini_key_only_in_header_and_never_in_errors(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    seen = {}
    def handler(req):
        seen["url"], seen["hdr"] = str(req.url), req.headers.get("x-goog-api-key")
        return httpx.Response(429, text="quota exceeded")
    real = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    llm = providers.GeminiLLM()
    with pytest.raises(RuntimeError) as e:
        asyncio.run(_collect(llm.stream("sys", [], "q")))
    assert FAKE_KEY not in seen["url"] and seen["hdr"] == FAKE_KEY
    assert FAKE_KEY not in str(e.value)


def test_describe_reports_presence_not_value(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    d = providers.describe()
    assert d["hosted_keys"]["gemini"] is True
    assert FAKE_KEY not in json.dumps(d)


_SECRET = re.compile(r"AIza[0-9A-Za-z_\-]{30,}|sk-[A-Za-z0-9]{20,}")


def test_no_secrets_in_tracked_files_or_frontend_bundle():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    files += [str(p.relative_to(ROOT)) for p in (ROOT / "frontend" / "dist").rglob("*") if p.is_file()]
    leaks = []
    for f in set(files):
        p = ROOT / f
        if not p.is_file() or p.stat().st_size > 5_000_000:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if _SECRET.search(text):
            leaks.append(f)
    assert leaks == []


def test_fallback_llm_takes_over_dead_primary():
    class Dead:
        name = "dead"
        async def stream(self, s, h, q):
            raise RuntimeError("dead")
            yield
    fb = providers.FallbackLLM(Dead(), providers.MockLLM())
    out = asyncio.run(_collect(fb.stream("sys", [], "q")))
    assert "mock answer" in out


def test_fallback_not_used_when_primary_answers():
    calls = []
    class Counter:
        name = "counter"
        async def stream(self, s, h, q):
            calls.append(1)
            yield "fallback text"
    fb = providers.FallbackLLM(providers.MockLLM(), Counter())
    out = asyncio.run(_collect(fb.stream("sys", [], "q")))
    assert "mock answer" in out and not calls


def test_voice_mode_validation(monkeypatch):
    import importlib
    import backend
    monkeypatch.setenv("VOICE_MODE", "bogus")
    importlib.reload(backend)
    assert backend.VOICE_MODE == "auto"
    monkeypatch.setenv("VOICE_MODE", "live")
    importlib.reload(backend)
    assert backend.VOICE_MODE == "live"
    monkeypatch.delenv("VOICE_MODE", raising=False)
    importlib.reload(backend)
    assert backend.VOICE_MODE == "auto"
