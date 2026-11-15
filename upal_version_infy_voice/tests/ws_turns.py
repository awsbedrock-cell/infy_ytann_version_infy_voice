"""10 consecutive questions over the real /ws/tutor socket.

Each turn must produce bot text + audio within the timeout, and the session
must return to 'listening' after the (simulated) client reports playback
done — proves no stuck state across a real conversation run with the real
LLM + TTS providers. Server must be running (python backend.py).

    python tests/ws_turns.py [--voice]   # --voice: send as final transcripts
"""
import asyncio, json, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

QS = [
    "how are winds formed",
    "what is a storm surge",
    "what is high pressure",
    "what causes cyclones",
    "why do sea breezes happen",
    "what is low pressure",
    "how do storms start",
    "what is wind speed",
    "what is air pressure",
    "how do cyclones get names",
]
TURN_TIMEOUT = 90
VOICE = "--voice" in sys.argv


async def drain_turn(ws, want_text=True):
    """Read until the turn ends; ack playback like the browser would."""
    got_text = got_audio = False
    first_audio = None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < TURN_TIMEOUT:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=TURN_TIMEOUT)
        except asyncio.TimeoutError:
            return False, "timeout", None
        if isinstance(msg, bytes):
            got_audio = True
            first_audio = first_audio or time.perf_counter()
            continue
        d = json.loads(msg)
        if d["type"] == "text" and d.get("role") == "bot":
            got_text = True
        elif d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done", "turn": d["turn"]}))
        elif d["type"] == "state" and d.get("state") == "error":
            return False, "error state: " + str(d.get("detail")), None
        elif d["type"] == "state" and d.get("state") == "listening" \
                and (got_text or not want_text):
            fa = round((first_audio - t0) * 1000) if first_audio else None
            ok = got_text and got_audio
            return ok, "" if ok else f"text={got_text} audio={got_audio}", fa
    return False, "no listening state", None


async def main():
    async with websockets.connect("ws://localhost:8200/ws/tutor",
                                  max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "hello", "chapter_id": 1,
                                  "session_id": f"ws-turns-{time.time()}"}))
        await ws.send(json.dumps({"type": "control", "cmd": "start"}))
        ok, detail, _ = await drain_turn(ws)          # greeting
        print(f"greeting: {'PASS' if ok else 'FAIL ' + detail}")
        passed, lat = 0, []
        for i, q in enumerate(QS, 1):
            if VOICE:
                await ws.send(json.dumps({"type": "transcript", "text": q,
                                          "final": True}))
            else:
                await ws.send(json.dumps({"type": "ask", "text": q}))
            ok, detail, fa = await drain_turn(ws)
            print(f"turn {i:2d}: {'PASS' if ok else 'FAIL ' + detail} "
                  f"first_audio={fa}ms :: {q}", flush=True)
            passed += ok
            if fa:
                lat.append(fa)
        lat.sort()
        p50 = lat[len(lat) // 2] if lat else None
        print(f"\nRESULT: {passed}/10 consecutive turns; first-audio p50={p50}ms "
              f"max={lat[-1] if lat else None}ms")
        sys.exit(0 if passed == 10 else 1)


asyncio.run(main())
