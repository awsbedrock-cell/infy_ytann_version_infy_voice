"""mic_probe.py — does speaker output reach the real mic? Opens Chrome,
measures mic RMS (raw and echo-cancelled) while a prompt plays out of the
speakers, and runs Web Speech on it. Diagnostic for loopback.py."""
import asyncio, json, subprocess, sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import loopback as L  # noqa: E402
import websockets  # noqa: E402

JS = r"""
(async () => {
  window.__probe = {raw: 0, aec: 0, sr: [], err: []};
  const mk = async (aec) => {
    const s = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: aec, noiseSuppression: aec, autoGainControl: aec}});
    const ctx = new AudioContext(); const an = ctx.createAnalyser(); an.fftSize = 1024;
    ctx.createMediaStreamSource(s).connect(an); const b = new Uint8Array(1024);
    const k = aec ? 'aec' : 'raw';
    setInterval(() => { an.getByteTimeDomainData(b); let q = 0;
      for (const v of b) { const d = (v - 128) / 128; q += d * d; }
      window.__probe[k] = Math.max(window.__probe[k], Math.sqrt(q / b.length)); }, 30);
  };
  await mk(false); await mk(true);
  const sr = new webkitSpeechRecognition(); sr.lang = 'en-IN'; sr.continuous = true; sr.interimResults = false;
  sr.onresult = e => { for (let i = e.resultIndex; i < e.results.length; i++) window.__probe.sr.push(e.results[i][0].transcript); };
  sr.onerror = e => window.__probe.err.push(e.error);
  sr.onend = () => { try { sr.start(); } catch (e) {} };
  sr.start();
  return navigator.mediaDevices.enumerateDevices().then(d => d.filter(x => x.kind === 'audioinput').map(x => x.label));
})()
"""


async def main():
    prof = L.ROOT / "chrome-profile-probe"
    p = subprocess.Popen([L.CHROME, f"--remote-debugging-port={L.PORT}", f"--user-data-dir={prof}",
                          "--no-first-run", "--use-fake-ui-for-media-stream", "http://localhost:8200"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with websockets.connect(L.page_ws()) as ws:
            c = L.CDP(ws)
            await asyncio.sleep(3)
            r = await c.cmd("Runtime.evaluate", expression=JS, awaitPromise=True, returnByValue=True)
            print("inputs:", r.get("result", {}).get("value"))
            await asyncio.sleep(2)
            print("silence:", await c.eval("JSON.stringify(window.__probe)"))
            await c.eval("window.__probe.raw = 0; window.__probe.aec = 0")
            run = L.Run(c)
            await run.say("q01")
            await asyncio.sleep(4)
            print("during prompt:", await c.eval("JSON.stringify(window.__probe)"))
    finally:
        p.terminate()


asyncio.run(main())
