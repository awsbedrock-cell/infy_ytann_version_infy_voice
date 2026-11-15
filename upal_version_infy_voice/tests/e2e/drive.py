"""drive.py — real-browser voice E2E via Chrome DevTools Protocol.

Launches real Chrome with fake-audio-capture fed by a synthesized WAV, so the
actual Web Speech -> /ws/tutor -> LLM -> edge-tts -> decode/playback pipeline
runs end-to-end. Records an in-page event timeline via injected hooks and
asserts start/stop/answers/latency.

    python tests\e2e\drive.py --wav tests\e2e\audio\test1.wav [--name test1]
"""
import argparse, asyncio, json, subprocess, sys, time, urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APP = "http://localhost:8200"
ROOT = Path(__file__).resolve().parent

HOOK = r"""
window.__e2e = {events: [], t0: performance.now()};
window.__ev = (k, d) => window.__e2e.events.push({t: performance.now(), k, d: String(d ?? '')});
const _send = WebSocket.prototype.send;
WebSocket.prototype.send = function(data) {
  try {
    if (typeof data === 'string') {
      const m = JSON.parse(data);
      if (m.type === 'transcript' && m.final) window.__ev('stt_final', m.text);
      else if (m.type === 'control') window.__ev('control', m.action || m.control);
      else if (m.type === 'ask') window.__ev('ask', m.text);
      else window.__ev('ws_out', m.type);
    }
  } catch (e) {}
  return _send.apply(this, arguments);
};
const _dec = AudioContext.prototype.decodeAudioData;
AudioContext.prototype.decodeAudioData = function(buf, ...rest) {
  window.__ev('audio_decode', buf.byteLength);
  return _dec.call(this, buf, ...rest);
};
window.addEventListener('DOMContentLoaded', () => {
  let lastStatus = '', lastN = 0;
  const mo = new MutationObserver(() => {
    const s = document.querySelector('.infy-status');
    if (s && s.textContent !== lastStatus) { lastStatus = s.textContent; window.__ev('status', lastStatus); }
    const msgs = document.querySelectorAll('.infy-msg');
    if (msgs.length !== lastN) { lastN = msgs.length; window.__ev('msg', msgs[msgs.length-1].textContent.slice(0, 100)); }
  });
  mo.observe(document.body, {subtree: true, childList: true, characterData: true});
});
"""


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def cmd(self, method, **params):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    async def eval(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr,
                           returnByValue=True, awaitPromise=False)
        return (r.get("result") or {}).get("value")


def page_target(port=9222, tries=30):
    for _ in range(tries):
        try:
            tabs = json.load(urllib.request.urlopen(
                f"http://localhost:{port}/json", timeout=2))
            for t in tabs:
                if t.get("type") == "page" and "localhost:8200" in t.get("url", ""):
                    return t["webSocketDebuggerUrl"]
            for t in tabs:
                if t.get("type") == "page":
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("no debuggable page found")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--name", default="run")
    ap.add_argument("--chapter", type=int, default=1)
    ap.add_argument("--secs", type=int, default=75)
    ap.add_argument("--orb", action="store_true",
                    help="click the orb button (starts capture/STT)")
    ap.add_argument("--lang", default=None, help="click lang button (e.g. 'hi')")
    args = ap.parse_args()

    profile = ROOT / f"chrome-profile-{args.name}"
    cmd = [
        CHROME,
        "--remote-debugging-port=9222",
        f"--user-data-dir={profile}",
        "--no-first-run", "--disable-session-crashed-bubble",
        "--use-fake-ui-for-media-stream",
        f"--use-file-for-fake-audio-capture={Path(args.wav).resolve()}",
        "--autoplay-policy=no-user-gesture-required",
        "--mute-audio",
        APP,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = page_target()
        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.cmd("Page.enable")
            await cdp.cmd("Runtime.enable")
            await cdp.cmd("Page.addScriptToEvaluateOnNewDocument", source=HOOK)
            await cdp.cmd("Page.navigate", url=APP)
            await asyncio.sleep(3)
            # pick chapter if a select exists
            await cdp.eval(f"""(() => {{
              const sel = document.querySelector('select');
              if (sel) {{ sel.value = '{args.chapter}';
                sel.dispatchEvent(new Event('change', {{bubbles: true}})); }}
              return !!sel; }})()""")
            await asyncio.sleep(1)
            if args.lang:
                await cdp.eval(f"""[...document.querySelectorAll('.infy-lang button, .infy-lang *')]
                  .find(b => /{args.lang}/i.test(b.textContent))?.click()""")
                await asyncio.sleep(1)
            if args.orb:
                await cdp.eval("document.querySelector('.infy-orb')?.click()")

            t_end = time.time() + args.secs
            events = []
            while time.time() < t_end:
                try:
                    chunk = await cdp.eval("window.__e2e ? window.__e2e.events.splice(0) : []")
                except Exception:
                    break
                if chunk:
                    events.extend(chunk)
                    for e in chunk:
                        print(f"  {e['t']:9.0f}ms  {e['k']:14s} {e['d'][:90]}")
                await asyncio.sleep(1)
            # final sweep
            chunk = await cdp.eval("window.__e2e ? window.__e2e.events.splice(0) : []")
            events.extend(chunk or [])
            status = await cdp.eval("document.querySelector('.infy-status')?.textContent || ''")
            msgs = await cdp.eval(
                "[...document.querySelectorAll('.infy-msg')].map(m => m.textContent.slice(0,120))")

        out = {"name": args.name, "events": events,
               "final_status": status, "messages": msgs}
        (ROOT / f"result_{args.name}.json").write_text(
            json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFINAL STATUS: {status}")
        print(f"MESSAGES: {len(msgs)}")
        for m in msgs:
            print("   ", m[:100])
    finally:
        proc.terminate()


asyncio.run(main())
