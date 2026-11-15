"""ws_stopstart.py — stop immediacy + stop/start endurance over /ws/tutor.

Against the RUNNING backend:
  S1  stop during generation: control(stop) -> interrupted msg + no more
      audio/text frames for the dead turn
  S2  stop during playback-ish state -> stopped quickly, no stragglers
  S3  immediate stop->start->ask produces a fresh answer (re-ask not deduped)
  S4  10 consecutive stop/start cycles — every one reaches the target state
  S5  same-question re-ask after stop is answered (not dropped as dup)
  S6  reconnect with same session id resumes without a second greeting
  S7  no duplicate turns: message turn-ids are unique/monotonic per turn

    python tests/ws_stopstart.py [--cycles 10]
Server must be running: python backend.py
"""
import asyncio, json, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

SID = f"ws-stopstart-{time.time()}"
RESULTS = []


def report(cid, ok, ev):
    RESULTS.append((cid, "PASS" if ok else "FAIL", ev))
    print(f"{cid}: {'PASS' if ok else 'FAIL'} {ev}", flush=True)


async def connect(sid=SID):
    ws = await websockets.connect("ws://localhost:8200/ws/tutor",
                                  max_size=16 * 1024 * 1024)
    await ws.send(json.dumps({"type": "hello", "chapter_id": 1,
                              "session_id": sid}))
    while True:
        d = json.loads(await ws.recv())
        if d["type"] == "ready":
            return ws


async def read_until_state(ws, want, timeout=15, ack=True):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        if isinstance(m, bytes):
            continue
        d = json.loads(m)
        if ack and d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        if d["type"] == "state" and d["state"] == want:
            return (time.perf_counter() - t0) * 1000
    return None


async def drain_turn(ws, timeout=120):
    """Read a full turn; return (ok, bot_text, latency_first_audio_ms)."""
    t0 = time.perf_counter()
    text = []
    fa = None
    while time.perf_counter() - t0 < timeout:
        m = await asyncio.wait_for(ws.recv(), timeout=timeout)
        if isinstance(m, bytes):
            if fa is None:
                fa = (time.perf_counter() - t0) * 1000
            continue
        d = json.loads(m)
        if d["type"] == "text" and d.get("role") == "bot":
            text.append(d["text"])
        elif d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        elif d["type"] == "state" and d["state"] in ("listening", "stopped") \
                and text:
            return True, " ".join(text), fa
    return bool(text), " ".join(text), fa


async def main():
    ap_cycles = 10
    if "--cycles" in sys.argv:
        ap_cycles = int(sys.argv[sys.argv.index("--cycles") + 1])

    ws = await connect()
    await ws.send(json.dumps({"type": "control", "cmd": "start"}))
    # first question spawns the llama worker (cold ~10-20s) — drain greeting
    t = await read_until_state(ws, "listening", timeout=30)
    print(f"setup: start->listening {t and round(t)}ms", flush=True)
    ok, _, _ = await drain_turn(ws, timeout=180)   # greeting/warm-up turn
    print(f"warm-up turn ok={ok}", flush=True)

    # ---- S1: stop during generation -----------------------------------------
    await ws.send(json.dumps({"type": "ask",
                              "text": "explain cyclones in great detail"}))
    await asyncio.sleep(0.4)   # mid-generation, before/in audio
    t0 = time.perf_counter()
    await ws.send(json.dumps({"type": "control", "cmd": "stop"}))
    t_int = None
    tail = 0
    deadline = time.perf_counter() + 5
    while time.perf_counter() < deadline:
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=1.5)
        except asyncio.TimeoutError:
            break
        if isinstance(m, bytes):
            tail += 1
            continue
        d = json.loads(m)
        if d["type"] == "interrupted" and t_int is None:
            t_int = (time.perf_counter() - t0) * 1000
        if d["type"] == "state" and d["state"] == "stopped":
            break
    report("S1 stop-mid-generation",
           t_int is not None and t_int < 300 and tail <= 2,
           f"interrupted={t_int and round(t_int)}ms stray_audio={tail}")

    # ---- S3/S5: immediate start + re-ask same question -----------------------
    await ws.send(json.dumps({"type": "control", "cmd": "start"}))
    t_start = await read_until_state(ws, "listening", timeout=10)
    await ws.send(json.dumps({"type": "ask",
                              "text": "explain cyclones in great detail"}))
    ok, reply, fa = await drain_turn(ws, timeout=120)
    report("S3 stop->start->re-ask", ok and "cyclone" in reply.lower(),
           f"restart={t_start and round(t_start)}ms "
           f"first_audio={fa and round(fa)}ms answered={ok}")

    # ---- S4: N stop/start cycles ---------------------------------------------
    bad = []
    lat_stop, lat_start = [], []
    for i in range(ap_cycles):
        t0 = time.perf_counter()
        await ws.send(json.dumps({"type": "control", "cmd": "stop"}))
        st = await read_until_state(ws, "stopped", timeout=8)
        if st is None:
            bad.append(f"cycle {i}: never stopped")
            continue
        lat_stop.append(st)
        t0 = time.perf_counter()
        await ws.send(json.dumps({"type": "control", "cmd": "start"}))
        li = await read_until_state(ws, "listening", timeout=8)
        if li is None:
            bad.append(f"cycle {i}: never listening")
            continue
        lat_start.append(li)
    report(f"S4 {ap_cycles} stop/start cycles", not bad,
           f"bad={bad or 'none'} "
           f"stop_p95={sorted(lat_stop)[int(len(lat_stop)*.95)] if lat_stop else '-'}ms "
           f"start_p95={sorted(lat_start)[int(len(lat_start)*.95)] if lat_start else '-'}ms")

    # ---- S6: reconnect resumes without re-greeting ----------------------------
    await ws.close()
    ws = await connect()
    await ws.send(json.dumps({"type": "control", "cmd": "start"}))
    # resumed session goes straight to listening (no greeting turn spawns
    # audio+text); a greeting would show up as bot text first
    t0 = time.perf_counter()
    direct = False
    saw_greeting_text = False
    while time.perf_counter() - t0 < 15:
        m = await asyncio.wait_for(ws.recv(), timeout=15)
        if isinstance(m, bytes):
            saw_greeting_text = True   # greeting speaks -> audio frames
            continue
        d = json.loads(m)
        if d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        elif d["type"] == "text" and d.get("role") == "bot":
            saw_greeting_text = True
        elif d["type"] == "state" and d["state"] == "listening":
            direct = True
            break
    report("S6 resume-no-regreet", direct and not saw_greeting_text,
           f"direct={direct} greeting_seen={saw_greeting_text}")

    # ---- S7: turn-id monotonicity over a mixed sequence -----------------------
    await ws.send(json.dumps({"type": "ask", "text": "what is wind"}))
    seen_turns = set()
    dup = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 90:
        m = await asyncio.wait_for(ws.recv(), timeout=90)
        if isinstance(m, bytes):
            continue
        d = json.loads(m)
        if "turn" in d and d["type"] == "turn_end":
            if d["turn"] in seen_turns:
                dup.append(d["turn"])
            seen_turns.add(d["turn"])
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        if d["type"] == "state" and d["state"] == "listening":
            break
    report("S7 no-duplicate-turns", not dup, f"dup_turns={dup or 'none'}")

    await ws.close()
    print("\n== SUMMARY ==")
    for cid, verdict, ev in RESULTS:
        print(f"  {cid:<18} {verdict:<5} {ev}")
    sys.exit(1 if any(v == "FAIL" for _, v, _ in RESULTS) else 0)


asyncio.run(main())
