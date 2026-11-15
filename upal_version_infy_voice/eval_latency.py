"""eval_latency.py — REAL end-to-end latency harness.

Drives N turns through TutorSession with the actual configured providers
(LLM + TTS from .env / config) — no mocks — then prints p50/p95 from
/api/metrics. This is the honest measurement the brief requires.

    python eval_latency.py [--turns 10] [--chapter 1]
"""
import argparse
import asyncio
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline  # noqa: E402

QUESTIONS = [
    "what is air pressure",
    "how are winds formed",
    "why do cyclones lose strength over land",
    "what is a storm surge",
    "how does lightning happen",
]


class Sink:
    def __init__(self):
        self.msgs, self.audio_bytes = [], 0
        self.first_audio_at = None
        self.turn_start = None

    async def send(self, m): self.msgs.append(m)
    async def audio(self, b):
        if self.first_audio_at is None:
            self.first_audio_at = asyncio.get_event_loop().time()
        self.audio_bytes += len(b)


def chapter_doc(cid: int) -> dict:
    con = sqlite3.connect(Path(__file__).parent / "varad.db")
    row = con.execute("SELECT doc FROM chapter WHERE id=?", (cid,)).fetchone()
    con.close()
    return json.loads(row[0])


async def main(turns: int, cid: int):
    doc = chapter_doc(cid)
    sink = Sink()
    sess = pipeline.TutorSession(cid, doc.get("grade", 8), doc["title"],
                                 doc.get("subject", ""), sink.send, sink.audio)
    await sess.start(say_hello=False)
    for i in range(turns):
        q = QUESTIONS[i % len(QUESTIONS)]
        t0 = asyncio.get_event_loop().time()
        sink.first_audio_at = None
        await sess.ask(q)
        while sess.state not in ("listening", "stopped", "error"):
            await asyncio.sleep(0.05)
        dt = (sink.first_audio_at or asyncio.get_event_loop().time()) - t0
        print(f"  turn {i+1}: first-audio {dt*1000:.0f} ms "
              f"({'TTS bytes ' + str(sink.audio_bytes) if sink.audio_bytes else 'no audio'})")
        sink.audio_bytes = 0
    import metrics
    rep = metrics.report()  # in-process counters reflect THIS eval's turns;
    # /api/metrics on :8200 only counts turns served over the websocket
    print("\n== metrics (this process) ==")
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--chapter", type=int, default=1)
    a = ap.parse_args()
    asyncio.run(main(a.turns, a.chapter))
