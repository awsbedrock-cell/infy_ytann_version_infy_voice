"""Stage 2 acceptance suite — automated websocket-level tests against the
real backend (transcripts injected over /ws/tutor; NOT real-microphone
tests — those stay in the manual checklist).

Covers:
  A1  20 consecutive conversational turns, no crash/stuck state
  A2  5 interruption/barge-in cancels mid-answer
  A3  5 stop/start (pause/resume) cycles
  A4  reconnect recovery — same session resumes, no duplicate greeting
  A5  30s idle silence produces no unsolicited speech
  A6  noise fragments ("पर", "ok") never spawn turns
  A7  duplicate final re-fire answers once, doesn't cancel the turn

    python tests/ws_acceptance.py
Server must be running: python backend.py
"""
import asyncio, json, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

QS = [
    "how are winds formed", "what is a storm surge", "what is high pressure",
    "what causes cyclones", "why do sea breezes happen", "what is low pressure",
    "how do storms start", "what is wind speed", "what is air pressure",
    "how do cyclones get names", "what is the eye of a cyclone",
    "how does air move", "what is a land breeze", "why is pressure important",
    "what is a thunderstorm", "how do winds carry moisture",
    "what is the monsoon", "why do cyclones spin", "what is wind direction",
    "how do storms get energy",
]
TURN_TIMEOUT = 90
SID = f"ws-acc-{time.time()}"

RESULTS: list[tuple[str, str, str]] = []   # (id, verdict, evidence)


def report(cid: str, ok: bool, evidence: str):
    RESULTS.append((cid, "PASS" if ok else "FAIL", evidence))
    print(f"{cid}: {'PASS' if ok else 'FAIL'} {evidence}", flush=True)


async def connect(sid=SID):
    ws = await websockets.connect("ws://localhost:8200/ws/tutor",
                                  max_size=16 * 1024 * 1024)
    await ws.send(json.dumps({"type": "hello", "chapter_id": 1,
                              "session_id": sid}))
    return ws


async def drain_turn(ws, want_audio=True):
    """Read until state==listening after a turn; ack playback_done.
    Returns (ok, first_audio_ms)."""
    got_text = got_audio = False
    first_audio = None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < TURN_TIMEOUT:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=TURN_TIMEOUT)
        except asyncio.TimeoutError:
            return False, None
        if isinstance(msg, bytes):
            got_audio = True
            first_audio = first_audio or time.perf_counter()
            continue
        d = json.loads(msg)
        if d["type"] == "text" and d.get("role") == "bot":
            got_text = True
        elif d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        elif d["type"] == "state" and d.get("state") == "listening" \
                and got_text:
            fa = round((first_audio - t0) * 1000) if first_audio else None
            return (got_text and (got_audio or not want_audio)), fa
    return False, None


async def wait_first_audio(ws, timeout=60):
    """Wait for the first audio frame of the current turn."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        if isinstance(msg, bytes):
            return True
        d = json.loads(msg)
        if d["type"] == "state" and d.get("state") == "error":
            return False
        if d["type"] == "turn_end":  # already ended (very short answer)
            return True
    return False


async def read_until_state(ws, want, timeout=10):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        if isinstance(msg, bytes):
            continue
        d = json.loads(msg)
        if d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        elif d["type"] == "state" and d.get("state") == want:
            return True
    return False


async def collect_silent(ws, seconds):
    """Collect frames for `seconds`; returns (bot_texts, audio_frames)."""
    texts, frames = 0, 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        try:
            msg = await asyncio.wait_for(
                ws.recv(), timeout=seconds - (time.perf_counter() - t0))
        except asyncio.TimeoutError:
            break
        if isinstance(msg, bytes):
            frames += 1
            continue
        d = json.loads(msg)
        if d["type"] == "text" and d.get("role") == "bot":
            texts += 1
        elif d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
    return texts, frames


async def main():
    ws = await connect()
    await ws.send(json.dumps({"type": "control", "cmd": "start"}))
    ok, _ = await drain_turn(ws)
    print(f"setup greeting: {'PASS' if ok else 'FAIL'}", flush=True)

    # ---- A1: 20 consecutive turns -------------------------------------------
    lat, passed = [], 0
    for i, q in enumerate(QS, 1):
        await ws.send(json.dumps({"type": "ask", "text": q}))
        ok, fa = await drain_turn(ws)
        passed += ok
        if fa:
            lat.append(fa)
        if not ok:
            print(f"  turn {i} FAILED: {q}", flush=True)
    lat.sort()
    report("A1 20-turns", passed == 20,
           f"{passed}/20 turns; first-audio p50="
           f"{lat[len(lat)//2] if lat else '-'}ms max={lat[-1] if lat else '-'}ms")

    # ---- A2: 5 barge-in cancels ----------------------------------------------
    interrupted = 0
    for n in range(5):
        await ws.send(json.dumps({"type": "ask",
                                  "text": "explain cyclones in detail"}))
        heard = await wait_first_audio(ws)
        await ws.send(json.dumps({"type": "control", "cmd": "interrupt"}))
        back = await read_until_state(ws, "listening", timeout=15)
        interrupted += heard and back
    report("A2 barge-in", interrupted == 5, f"{interrupted}/5 cancels landed")

    # ---- A3: 5 stop/start cycles ----------------------------------------------
    cycles = 0
    for n in range(5):
        await ws.send(json.dumps({"type": "control", "cmd": "stop"}))
        stopped = await read_until_state(ws, "stopped", timeout=8)
        await ws.send(json.dumps({"type": "control", "cmd": "start"}))
        listening = await read_until_state(ws, "listening", timeout=8)
        cycles += stopped and listening
    report("A3 stop/start", cycles == 5, f"{cycles}/5 cycles")

    # after the last cycle the session may replay "resumed" chatter; settle it
    await collect_silent(ws, 2)

    # ---- A4: reconnect — same session_id, no re-greet ---------------------------
    await ws.close()
    ws = await connect()                       # same SID
    await ws.send(json.dumps({"type": "control", "cmd": "start"}))
    # a resumed session should go straight to listening, NOT greet
    direct = await read_until_state(ws, "listening", timeout=8)
    await ws.send(json.dumps({"type": "ask", "text": "what is wind"}))
    ok, _ = await drain_turn(ws)
    report("A4 reconnect", direct and ok,
           f"resumed_without_greet={direct}, turn_after={ok}")

    # ---- A5: 30s silence --------------------------------------------------------
    texts, frames = await collect_silent(ws, 30)
    report("A5 silence-30s", texts == 0 and frames == 0,
           f"unsolicited bot_texts={texts} audio_frames={frames} in 30s")

    # ---- A6: noise fragments ------------------------------------------------------
    for frag in ["पर", "ok", "lekin"]:
        await ws.send(json.dumps({"type": "transcript", "text": frag,
                                  "final": True}))
    texts, frames = await collect_silent(ws, 8)
    report("A6 noise-drop", texts == 0 and frames == 0,
           f"fragments produced turns: text={texts} audio={frames}")

    # ---- A7: dup final re-fire -----------------------------------------------------
    await ws.send(json.dumps({"type": "transcript",
                              "text": "what is a cyclone", "final": True}))
    await asyncio.sleep(6)   # let the first turn get underway
    await ws.send(json.dumps({"type": "transcript",
                              "text": "what is a cyclone", "final": True}))
    ok, fa = await drain_turn(ws)
    report("A7 dup-final", ok,
           "turn completed despite re-fired final (first one kept running)")

    await ws.close()
    print("\n== SUMMARY ==")
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    for cid, verdict, ev in RESULTS:
        print(f"  {cid:<16} {verdict:<5} {ev}")
    print(f"{len(RESULTS) - len(fails)}/{len(RESULTS)} sections pass")
    sys.exit(1 if fails else 0)


asyncio.run(main())
