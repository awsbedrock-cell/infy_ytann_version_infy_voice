"""eval_grounding.py — REAL grounding + abstention harness.

Asks real questions through the pipeline (real LLM, real retrieval — TTS
swapped for a silent stub so no audio hardware is needed) and checks:
  1. in-chapter answers contain chapter facts (grounded, not generic)
  2. out-of-chapter questions get an honest "not covered" — not fabrication
  3. wrong-premises get corrected or flagged, not agreed with
  4. language consistency (Hindi question → Hindi answer)

    python eval_grounding.py [--chapter 1]
"""
import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import pipeline  # noqa: E402
import providers  # noqa: E402

CASES = [
    # (question, expect, keyword evidence that should appear in a good answer)
    ("how are winds formed", "grounded", ["pressure", "air"]),
    ("why do cyclones lose strength over land", "grounded",
     ["moisture", "friction", "warm", "energy", "water"]),
    ("what is the capital of France", "abstain|general",
     ["paris", "not", "chapter"]),
    ("winds blow because the earth rotates and pulls them", "correct",
     ["pressure", "not", "actually"]),
    ("मुझे बताओ हवा कैसे बनती है", "hindi", []),
]

ABSTAIN_MARKERS = re.compile(
    r"(not (in|covered|from|part of)|beyond|outside|doesn't (cover|mention)|"
    r"not.*chapter|isn't.*chapter)", re.I)
HINDI = re.compile(r"[\u0900-\u097F]")


class SilentTTS:
    name = "silent"

    async def speak(self, text, language="en"):
        return
        yield


class Sink:
    def __init__(self):
        self.texts, self.sources = [], []

    async def send(self, m):
        if m["type"] == "text" and m["role"] == "bot":
            self.texts.append(m["text"])
        elif m["type"] == "sources":
            self.sources = [s["source"] for s in m["sources"]]

    async def audio(self, b): pass


def chapter_doc(cid):
    con = sqlite3.connect(Path(__file__).parent / "varad.db")
    row = con.execute("SELECT doc FROM chapter WHERE id=?", (cid,)).fetchone()
    con.close()
    return json.loads(row[0])


async def main(cid: int):
    doc = chapter_doc(cid)
    sink = Sink()
    sess = pipeline.TutorSession(cid, doc.get("grade", 8), doc["title"],
                                 doc.get("subject", ""), sink.send, sink.audio,
                                 tts=SilentTTS())
    await sess.start(say_hello=False)
    results = []
    for q, expect, kw in CASES:
        sink.texts, sink.sources = [], []
        if any(HINDI.findall(q)):
            sess.set_language("hi")
        else:
            sess.set_language("en")
        await sess.ask(q)
        while sess.state not in ("listening", "stopped", "error"):
            await asyncio.sleep(0.05)
        answer = " ".join(sink.texts)
        low = answer.lower()
        grounded = bool(sink.sources)
        abstained = bool(ABSTAIN_MARKERS.search(answer))
        hindi = bool(HINDI.search(answer))
        kws = [k for k in kw if k in low]
        ok = {
            "grounded": grounded and bool(kws),
            "abstain|general": abstained or "paris" in low,
            "correct": "pressure" in low and ("not" in low or "actually" in low),
            "hindi": hindi,
        }.get(expect, False)
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {expect:>15} | {q[:52]}")
        print(f"        answer: {answer[:180]}")
        print(f"        sources: {sink.sources[:3] or '—'}  kw:{kws}")
    print(f"\n{sum(results)}/{len(results)} checks passed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", type=int, default=1)
    a = ap.parse_args()
    asyncio.run(main(a.chapter))
