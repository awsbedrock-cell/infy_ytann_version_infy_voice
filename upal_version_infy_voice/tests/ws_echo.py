"""ws_echo.py — verify echo-guard + final-dedup over the REAL websocket.

Feeds the server transcripts that mimic the recognizer hearing Infy's own
TTS output, and duplicate finals; asserts no phantom user turns spawn.
Run: python tests/ws_echo.py   (backend must be running on :8200)
"""
import asyncio, json, sys

import websockets


async def recv_until(ws, want, timeout=30):
    got = []
    async def loop():
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            m = json.loads(raw)
            got.append(m)
            if want(m):
                return m
    try:
        return await asyncio.wait_for(loop(), timeout), got
    except asyncio.TimeoutError:
        return None, got


async def main():
    ok = True
    async with websockets.connect("ws://localhost:8200/ws/tutor") as ws:
        await ws.send(json.dumps({"type": "chapter", "chapter_id": 1}))
        await ws.send(json.dumps({"type": "control", "cmd": "start"}))

        # wait for the greeting's first bot sentence
        m, got = await recv_until(
            ws, lambda m: m.get("type") == "text" and m.get("role") == "bot",
            timeout=60)
        assert m, "no greeting text"
        bot_text = m["text"]
        print("greeting heard:", bot_text[:60])

        # 1) ECHO: transcript that is literally Infy's own words (final)
        await ws.send(json.dumps({"type": "transcript",
                                  "text": bot_text, "final": True}))
        # let the would-be turn settle
        m, got = await recv_until(
            ws, lambda m: (m.get("type") == "text" and m.get("role") == "you")
            or (m.get("type") == "state" and m.get("state") == "listening"),
            timeout=20)
        phantom = [g for g in got
                   if g.get("type") == "text" and g.get("role") == "you"]
        if phantom:
            ok = False
            print("FAIL echo-guard: phantom user turn from", phantom)
        else:
            print("PASS echo-guard: own-voice transcript produced no turn")

        # 2) DUPLICATE FINALS: same final twice back-to-back -> one turn
        q = "how are winds formed"
        await ws.send(json.dumps({"type": "transcript", "text": q,
                                  "final": True}))
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({"type": "transcript", "text": q,
                                  "final": True}))
        you_msgs = []
        async def collect():
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                m = json.loads(raw)
                if m.get("type") == "text" and m.get("role") == "you":
                    you_msgs.append(m["text"])
                if (m.get("type") == "state"
                        and m.get("state") == "listening" and you_msgs):
                    return
        try:
            await asyncio.wait_for(collect(), 90)
        except asyncio.TimeoutError:
            pass
        if len(you_msgs) == 1:
            print("PASS dedup: duplicate final produced exactly one turn")
        else:
            ok = False
            print(f"FAIL dedup: {len(you_msgs)} user turns for one question",
                  you_msgs)

        # 3) SECOND TURN still answered after the first (no stuck session)
        await ws.send(json.dumps({"type": "transcript",
                                  "text": "what is a storm surge",
                                  "final": True}))
        m, _ = await recv_until(
            ws, lambda m: m.get("type") == "text" and m.get("role") == "bot",
            timeout=60)
        if m:
            print("PASS second turn answered:", m["text"][:60])
        else:
            ok = False
            print("FAIL: second question got no response")

        await ws.send(json.dumps({"type": "control", "cmd": "stop"}))
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES ABOVE")
    sys.exit(0 if ok else 1)


asyncio.run(main())
