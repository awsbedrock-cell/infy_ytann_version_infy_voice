"""WS smoke test — real socket against the running backend. No mocks."""
import asyncio, json, sys
sys.path.insert(0, '.')
try:
    import websockets  # type: ignore
except ImportError:
    websockets = None

import urllib.request


async def main():
    if websockets is None:
        # fall back: raw asyncio? simplest is httpx ws — not available.
        print("websockets package missing — install with: pip install websockets")
        sys.exit(2)
    async with websockets.connect("ws://localhost:8200/ws/tutor") as ws:
        await ws.send(json.dumps({"type": "chapter", "chapter_id": 1}))
        ready = json.loads(await ws.recv())
        assert ready["type"] == "ready", ready
        print("READY:", ready["title"])
        # stop command latches
        await ws.send(json.dumps({"type": "transcript", "text": "infy stop", "final": True}))
        msgs = [json.loads(await ws.recv()), json.loads(await ws.recv())]
        states = [m.get("state") for m in msgs if m["type"] == "state"]
        assert "stopped" in states, msgs
        print("STOP latch ok:", states)
        # ignored while stopped
        await ws.send(json.dumps({"type": "transcript", "text": "what is pressure", "final": True}))
        await asyncio.sleep(0.3)
        # start again
        await ws.send(json.dumps({"type": "transcript", "text": "infy start", "final": True}))
        m = json.loads(await ws.recv())
        assert m.get("state") == "listening", m
        print("START ok:", m["state"])
        # ask — will hit the LLM provider (error expected until ollama up, that's fine: error state proves wiring)
        await ws.send(json.dumps({"type": "ask", "text": "how are winds formed"}))
        seen, audio_bytes = [], 0
        for _ in range(120):
            frame = await asyncio.wait_for(ws.recv(), timeout=60)
            if isinstance(frame, bytes):
                audio_bytes += len(frame)
                continue
            m = json.loads(frame)
            seen.append((m["type"], m.get("state") or m.get("detail") or m.get("role") or ""))
            if m["type"] == "state" and m["state"] in ("listening", "error", "stopped"):
                break
        print("TURN:", seen)
        print("AUDIO BYTES:", audio_bytes)
        assert audio_bytes > 0, "no audio frames received"


asyncio.run(main())
