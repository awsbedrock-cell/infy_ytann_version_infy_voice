"""eval_followup.py — multi-turn context eval (real LLM, silent TTS).

Asks a base question, then a follow-up that only makes sense WITH the
previous turn ("give an example", "why does that happen"). Checks the
follow-up answers stay on the established topic — proof that
history[-12:] is actually used, not just stored.

    python eval_followup.py [--chapter 1]
"""
import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import pipeline  # noqa: E402

# (base question, follow-up, [topic keywords the follow-up should mention])
CHAINS = [
    ("what is air pressure",
     "give me an example of it",
     ["pressure", "air", "balloon", "straw", "mountain", "ear",
      "bottle", "can", "suck"]),
    ("how are winds formed",
     "why does that happen",
     ["heat", "sun", "warm", "temperature", "pressure"]),
    ("what is a cyclone",
     "is it dangerous and how",
     ["cyclone", "storm", "wind", "damage", "flood", "danger",
      "coast", "rain", "surge"]),
]


class SilentTTS:
    name = "silent"

    async def speak(self, text, language="en"):
        return
        yield


class Sink:
    def __init__(self):
        self.texts = []

    async def send(self, m):
        if m["type"] == "text" and m["role"] == "bot":
            self.texts.append(m["text"])

    async def audio(self, b):
        pass


def chapter_doc(cid):
    con = sqlite3.connect(Path(__file__).parent / "varad.db")
    row = con.execute("SELECT doc FROM chapter WHERE id=?", (cid,)).fetchone()
    con.close()
    return json.loads(row[0])


async def turn(sess, sink, q):
    sink.texts = []
    await sess.ask(q)
    while sess.state not in ("listening", "stopped", "error"):
        await asyncio.sleep(0.05)
    return " ".join(sink.texts)


async def main(cid: int):
    doc = chapter_doc(cid)
    sink = Sink()
    sess = pipeline.TutorSession(cid, doc.get("grade", 8), doc["title"],
                                 doc.get("subject", ""), sink.send,
                                 sink.audio, tts=SilentTTS())
    await sess.start(say_hello=False)
    results = []
    for base, follow, kws in CHAINS:
        await turn(sess, sink, base)
        ans = await turn(sess, sink, follow)
        ok = any(k in ans.lower() for k in kws)
        results.append((base, follow, ok, ans[:110]))
    for r in results:
        print(f"[{'PASS' if r[2] else 'FAIL'}] {r[0]!r} -> {r[1]!r}")
        print(f"        answer: {r[3]}...")
    print(f"\n{sum(1 for r in results if r[2])}/{len(results)} "
          "follow-ups stayed on-topic")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", type=int, default=1)
    a = ap.parse_args()
    asyncio.run(main(a.chapter))
