"""bench_ttft.py — measure LLM time-to-first-token / first-sentence / total
with the real tutor prompt + retrieval, per provider. Output: JSON lines.

    python tests/bench_ttft.py ollama:qwen3:8b gemini:gemini-2.5-flash
"""
import asyncio, json, statistics, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
import pipeline, providers, rag  # noqa: E402

QS = [("how are winds formed", "English"),
      ("why do cyclones lose strength on land", "English"),
      ("what is the capital of France", "English"),
      ("हवा का दबाव क्या होता है", "Hindi"),
      ("चक्रवात कैसे बनते हैं", "Hindi")]


def system(hits, language):
    s = pipeline.TutorSession.__new__(pipeline.TutorSession)
    s.grade, s.title, s.subject, s.language = 7, "Pressure, Winds, Storms, and Cyclones", "Science", language
    return pipeline.TutorSession._system(s, hits)


async def bench(spec):
    name, _, model = spec.partition(":")
    llm = providers.make_llm(name, model or None)
    if hasattr(llm, "warm"):
        await llm.warm()
    rows = []
    for q, lang in QS:
        hits = rag.retrieve(q, 1, k=3)
        t0 = time.perf_counter()
        first_tok = first_sent = None
        text = ""
        try:
            async def toks():
                nonlocal first_tok, text
                async for t in llm.stream(system(hits, lang), [], q):
                    first_tok = first_tok or time.perf_counter()
                    text += t
                    yield t
            async for _s in providers.sentences(toks()):
                first_sent = first_sent or time.perf_counter()
            err = None
        except Exception as e:  # report, keep going
            err = f"{type(e).__name__}: {e}"
        t1 = time.perf_counter()
        row = {"provider": spec, "q": q, "lang": lang, "error": err,
               "ttft_ms": round((first_tok - t0) * 1000) if first_tok else None,
               "first_sentence_ms": round((first_sent - t0) * 1000) if first_sent else None,
               "total_ms": round((t1 - t0) * 1000), "chars": len(text),
               "answer": text[:300]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    ok = [r["first_sentence_ms"] for r in rows if r["first_sentence_ms"]]
    if ok:
        print(json.dumps({"provider": spec, "summary": True,
                          "first_sentence_p50_ms": statistics.median(ok),
                          "n": len(ok)}), flush=True)


async def main():
    for spec in sys.argv[1:]:
        await bench(spec)


asyncio.run(main())
