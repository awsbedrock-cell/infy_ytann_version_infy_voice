"""bench_perf.py — WS-level latency probe for the voice pipeline.

Measures (server must be running: python backend.py):
  hello->ready, control start->listening, ask->first_audio,
  stop mid-turn->interrupted + last-audio-frame, start->listening (2nd+),
  stop/start cycle timings. Prints per-sample values + p50/p95.

    python tests/bench_perf.py [--cycles 5] [--chapter 1]
"""
import argparse, asyncio, json, statistics, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

SID = f"bench-{time.time()}"


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round((p / 100) * (len(xs) - 1))))
    return round(xs[k])


async def connect():
    ws = await websockets.connect("ws://localhost:8200/ws/tutor",
                                  max_size=16 * 1024 * 1024)
    t0 = time.perf_counter()
    await ws.send(json.dumps({"type": "hello", "chapter_id": 1,
                              "session_id": SID}))
    while True:
        d = json.loads(await ws.recv())
        if d["type"] == "ready":
            return ws, (time.perf_counter() - t0) * 1000


async def until_state(ws, want, timeout=20):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        d = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if d["type"] == "state" and d["state"] == want:
            return (time.perf_counter() - t0) * 1000
    return None


async def ask_and_first_audio(ws, text, timeout=90):
    """Returns (first_audio_ms, first_text_ms, full_reply)."""
    t0 = time.perf_counter()
    await ws.send(json.dumps({"type": "ask", "text": text}))
    fa = ft = None
    reply = []
    while time.perf_counter() - t0 < timeout:
        m = await asyncio.wait_for(ws.recv(), timeout=timeout)
        now = time.perf_counter()
        if isinstance(m, bytes):
            if fa is None:
                fa = (now - t0) * 1000
            continue
        d = json.loads(m)
        if d["type"] == "text" and d.get("role") == "bot":
            if ft is None:
                ft = (now - t0) * 1000
            reply.append(d["text"])
        elif d["type"] == "turn_end":
            await ws.send(json.dumps({"type": "playback_done",
                                      "turn": d["turn"]}))
        elif d["type"] == "state" and d["state"] in ("listening", "stopped"):
            if reply:
                return fa, ft, " ".join(reply)
    return fa, ft, " ".join(reply)


async def stop_latency(ws, timeout=10):
    """Send stop mid-turn; return (ms_to_interrupted, ms_to_last_audio,
    bytes_after_stop). Caller must have a turn in flight."""
    t0 = time.perf_counter()
    await ws.send(json.dumps({"type": "control", "cmd": "stop"}))
    t_int = None
    last_audio = None
    bytes_after = 0
    while time.perf_counter() - t0 < timeout:
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            break
        now = time.perf_counter()
        if isinstance(m, bytes):
            last_audio = (now - t0) * 1000
            bytes_after += len(m)
            continue
        d = json.loads(m)
        if d["type"] == "interrupted" and t_int is None:
            t_int = (now - t0) * 1000
        if d["type"] == "state" and d["state"] == "stopped":
            # keep reading a moment to catch stragglers
            try:
                while True:
                    m = await asyncio.wait_for(ws.recv(), timeout=1.5)
                    if isinstance(m, bytes):
                        last_audio = (time.perf_counter() - t0) * 1000
                        bytes_after += len(m)
            except asyncio.TimeoutError:
                pass
            return t_int, last_audio, bytes_after
    return t_int, last_audio, bytes_after


async def start_latency(ws, timeout=15):
    t0 = time.perf_counter()
    await ws.send(json.dumps({"type": "control", "cmd": "start"}))
    return await until_state(ws, "listening", timeout)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=5)
    ap.add_argument("--questions", type=int, default=3)
    args = ap.parse_args()

    print("== baseline perf probe ==", flush=True)
    ws, hello_ms = await connect()
    print(f"hello->ready: {hello_ms:.0f}ms")

    # first start + first question (cold: spawns llama worker)
    t_start = await start_latency(ws)
    print(f"start->listening (1st, cold worker): "
          f"{t_start if t_start is not None else 'TIMEOUT'}ms"
          + (" (greeting turn may run in parallel)" if t_start else ""))

    lat_fa, lat_ft, lat_stop_int, lat_stop_audio, lat_start = [], [], [], [], []
    qs = ["how are winds formed", "what is a cyclone",
          "what is low pressure", "why do sea breezes happen",
          "what is a storm surge"][:args.questions]

    for q in qs:
        fa, ft, reply = await ask_and_first_audio(ws, q)
        if fa:
            lat_fa.append(fa)
        if ft:
            lat_ft.append(ft)
        print(f"ask {q!r}: first_text={ft and round(ft)}ms "
              f"first_audio={fa and round(fa)}ms | reply: {reply[:70]!r}")

    # stop during speaking
    for _ in range(args.cycles):
        await ws.send(json.dumps({"type": "ask",
                                  "text": "explain cyclones in detail"}))
        # wait for at least one audio frame, then stop
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 60:
            m = await asyncio.wait_for(ws.recv(), timeout=60)
            if isinstance(m, bytes):
                break
        t_int, t_last, b_after = await stop_latency(ws)
        if t_int is not None:
            lat_stop_int.append(t_int)
        if t_last is not None:
            lat_stop_audio.append(t_last)
        print(f"stop mid-speech: interrupted={t_int and round(t_int)}ms "
              f"last_audio={t_last and round(t_last)}ms "
              f"bytes_after_stop={b_after}")
        # immediate restart
        t = await start_latency(ws)
        if t is not None:
            lat_start.append(t)
        print(f"restart start->listening: {t and round(t)}ms")

    def line(name, xs):
        if not xs:
            return f"{name}: no samples"
        return (f"{name}: n={len(xs)} min={min(xs):.0f} "
                f"p50={pct(xs,50)} p95={pct(xs,95)} max={max(xs):.0f}ms")

    print("\n== SUMMARY ==")
    print(line("hello->ready", [hello_ms]))
    print(line("ask->first_text", lat_ft))
    print(line("ask->first_audio", lat_fa))
    print(line("stop->interrupted", lat_stop_int))
    print(line("stop->last_audio", lat_stop_audio))
    print(line("start->listening", lat_start))
    try:
        import urllib.request
        print("server metrics:", urllib.request.urlopen(
            "http://localhost:8200/api/metrics", timeout=5).read().decode())
    except Exception as e:
        print("metrics fetch failed:", e)
    await ws.close()


asyncio.run(main())
