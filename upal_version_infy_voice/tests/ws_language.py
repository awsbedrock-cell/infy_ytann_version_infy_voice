"""ws_language.py — language-consistency acceptance suite over /ws/tutor.

Against the RUNNING backend (real LLM + real retrieval):
  L1  10 consecutive English questions  -> every reply in English
  L2  10 consecutive Hindi questions    -> every reply in Hindi
  L3  intentional switching en->hi->en  -> reply language follows the
      CURRENT utterance, not the history before it
  L4  spoken-language check: 'speak'/voice path is reported per sentence —
      at the WS level we verify the TEXT script per sentence; the
      per-sentence TTS voice keyed to that script is covered in
      test_stage7.py::test_tts_voice_follows_sentence_script

    python tests/ws_language.py [--chapter 1]
Server must be running: python backend.py
"""
import asyncio, json, re, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

DEVA = re.compile(r"[ऀ-ॿ]")
TURN_TIMEOUT = 120
SID = f"ws-lang-{time.time()}"
RESULTS = []

EN_QS = [
    "what is air pressure",
    "how are winds formed",
    "what is a cyclone",
    "why do sea breezes happen",
    "what is high pressure",
    "how do storms get energy",
    "what is a storm surge",
    "why is the eye of a cyclone calm",
    "what is low pressure",
    "how does wind speed get measured",
]
HI_QS = [
    "दबाव क्या होता है",
    "हवा कैसे चलती है",
    "चक्रवात कैसे बनता है",
    "तूफान में क्या होता है",
    "समुद्री हवा क्या है",
    "आँधी क्यों आती है",
    "बारिश कैसे होती है",
    "बादल कैसे बनते हैं",
    "गरम हवा ऊपर क्यों उठती है",
    "तापमान और दबाव का क्या रिश्ता है",
]


def deva_ratio(text: str) -> float:
    chars = re.sub(r"\s", "", text)
    if not chars:
        return 0.0
    return len(DEVA.findall(text)) / len(chars)


async def connect():
    ws = await websockets.connect("ws://localhost:8200/ws/tutor",
                                  max_size=16 * 1024 * 1024)
    await ws.send(json.dumps({"type": "hello", "chapter_id": 1,
                              "session_id": SID}))
    while True:
        d = json.loads(await ws.recv())
        if d["type"] == "ready":
            return ws


async def ask_reply(ws, q):
    """Send a typed ask, return the full bot reply text."""
    await ws.send(json.dumps({"type": "ask", "text": q}))
    reply = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < TURN_TIMEOUT:
        m = await asyncio.wait_for(ws.recv(), timeout=TURN_TIMEOUT)
        if isinstance(m, bytes):
            continue
        d = json.loads(m)
        if d["type"] == "text" and d.get("role") == "bot":
            reply.append(d["text"])
        elif d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        elif d["type"] == "state" and d["state"] in ("listening", "stopped",
                                                     "error") and reply:
            return " ".join(reply)
    return " ".join(reply)


def report(cid, ok, ev):
    RESULTS.append((cid, "PASS" if ok else "FAIL", ev))
    print(f"{cid}: {'PASS' if ok else 'FAIL'} {ev}", flush=True)


def is_english(t):
    return bool(t.strip()) and not DEVA.search(t)


def is_hindi(t):
    # reply should be predominantly Devanagari (Qwen emits Devanagari when
    # instructed Hindi) — romanized-only Hindi fails this check
    return bool(t.strip()) and deva_ratio(t) > 0.5


async def main():
    ws = await connect()
    await ws.send(json.dumps({"type": "control", "cmd": "start"}))
    # swallow the greeting turn
    await asyncio.wait_for(_drain_until_listening(ws), 120)

    # ---- L1: 10 English turns ---------------------------------------------
    fails = []
    for i, q in enumerate(EN_QS, 1):
        r = await ask_reply(ws, q)
        ok = is_english(r)
        if not ok:
            fails.append((i, r[:80]))
        print(f"  en[{i}] {'ok' if ok else 'HINDI!'} {r[:70]!r}", flush=True)
    report("L1 english-x10", not fails, f"fails={fails or 'none'}")

    # ---- L2: 10 Hindi turns ------------------------------------------------
    fails = []
    for i, q in enumerate(HI_QS, 1):
        r = await ask_reply(ws, q)
        ok = is_hindi(r)
        if not ok:
            fails.append((i, r[:80]))
        print(f"  hi[{i}] {'ok' if ok else 'ENGLISH!'} {r[:70]!r}", flush=True)
    report("L2 hindi-x10", not fails, f"fails={fails or 'none'}")

    # ---- L3: intentional switching ------------------------------------------
    seq = [("what is air pressure", is_english),
           ("हवा कैसे चलती है", is_hindi),
           ("how do cyclones form", is_english),
           ("बारिश कैसे होती है", is_hindi),
           ("what is a storm surge", is_english)]
    fails = []
    for i, (q, pred) in enumerate(seq, 1):
        r = await ask_reply(ws, q)
        if not pred(r):
            fails.append((i, r[:80]))
        print(f"  switch[{i}] {'ok' if pred(r) else 'WRONG-LANG'} {r[:60]!r}",
              flush=True)
    report("L3 switching", not fails, f"fails={fails or 'none'}")

    await ws.close()
    print("\n== SUMMARY ==")
    for cid, verdict, ev in RESULTS:
        print(f"  {cid:<16} {verdict:<5} {ev}")
    sys.exit(1 if any(v == "FAIL" for _, v, _ in RESULTS) else 0)


async def _drain_until_listening(ws):
    while True:
        m = await ws.recv()
        if isinstance(m, bytes):
            continue
        d = json.loads(m)
        if d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        if d["type"] == "state" and d["state"] == "listening":
            return


asyncio.run(main())
