"""loopback.py — Stage 1 acceptance in real Chrome with the REAL microphone.

Spoken test prompts are played out of the laptop speakers by a separate
Windows player process, so Chrome's echo canceller does not subtract them —
to the mic they are a person talking. Infy's own answers play out loud from
Chrome at the same time, so assistant-audio feedback is exercised for real
(this is the failure the user saw: Infy hearing itself).

This is NOT a substitute for a human voice pass — the "student" is a TTS
voice through the speakers. It is the reproducible automated version.

    python tests/e2e/make_utterances.py      # once
    python backend.py                        # in another shell
    python tests/e2e/loopback.py [--only ten,barge,stopstart,silence,reconnect,hindi]
"""
import argparse, asyncio, json, re, shutil, subprocess, sys, time, urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import websockets  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APP = "http://localhost:8200"
PORT = 9223
ROOT = Path(__file__).resolve().parent
UTT = ROOT / "utterances"
MANIFEST = json.loads((UTT / "manifest.json").read_text(encoding="utf-8"))

HOOK = r"""
(() => {
  const _WS = window.WebSocket;
  window.__sockets = [];
  function W(...a) { const s = new _WS(...a); window.__sockets.push(s); return s; }
  W.prototype = _WS.prototype;
  W.CONNECTING = 0; W.OPEN = 1; W.CLOSING = 2; W.CLOSED = 3;
  window.WebSocket = W;
})();
"""


def words(t):
    return re.findall(r"[a-z0-9\u0900-\u097f]+", (t or "").lower())


def similar(a, b):
    wa, wb = set(words(a)), set(words(b))
    return len(wa & wb) / max(1, min(len(wa), len(wb))) if wa and wb else 0.0


class CDP:
    def __init__(self, ws):
        self.ws, self._id = ws, 0

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
        r = await self.cmd("Runtime.evaluate", expression=expr, returnByValue=True)
        return (r.get("result") or {}).get("value")


class Run:
    def __init__(self, cdp):
        self.cdp, self.ev, self.checks = cdp, [], []
        self.played = []          # (t_page_ms, name, text)

    async def pull(self):
        chunk = await self.cdp.eval("(window.__events || []).splice(0)") or []
        for e in chunk:
            self.ev.append(e)
            print(f"   {e['t']:9.0f}  {e['ev']:16s} {(e.get('data') or '')[:80]}", flush=True)
        return chunk

    async def now(self):
        return await self.cdp.eval("performance.now()")

    async def wait(self, pred, timeout, since=0):
        t_end = time.time() + timeout
        while time.time() < t_end:
            await self.pull()
            hit = next((e for e in self.ev[since:] if pred(e)), None)
            if hit:
                return hit
            await asyncio.sleep(0.2)
        return None

    def last_state(self):
        return next((e["data"] for e in reversed(self.ev) if e["ev"] == "state"), None)

    async def idle(self, timeout=60, settle=1.0):
        t_end = time.time() + timeout
        while time.time() < t_end:
            await self.pull()
            if self.last_state() in ("listening", "stopped"):
                await asyncio.sleep(settle)
                await self.pull()
                if self.last_state() in ("listening", "stopped"):
                    return True
            await asyncio.sleep(0.2)
        return False

    async def say(self, name, wait=True):
        f = (UTT / MANIFEST[name]["file"]).resolve()
        self.played.append((await self.now(), name, MANIFEST[name]["text"]))
        print(f"  >> speaker: {MANIFEST[name]['text']}", flush=True)
        ps = ("Add-Type -AssemblyName PresentationCore;"
              "$p=New-Object System.Windows.Media.MediaPlayer;$p.Volume=1;"
              f"$p.Open([uri]'{f}');$n=0;"
              "while(-not $p.NaturalDuration.HasTimeSpan -and $n -lt 100){Start-Sleep -m 50;$n++};"
              "$p.Play();Start-Sleep -m ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds+250);$p.Close()")
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command", ps,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait:
            while proc.returncode is None:
                await self.pull()
                try:
                    await asyncio.wait_for(proc.wait(), 0.3)
                except asyncio.TimeoutError:
                    pass
        return proc

    async def ask_turn(self, name, timeout=60):
        """Speak a prompt while Infy is listening; expect exactly that
        question as a turn, an answer, and a return to listening."""
        await self.idle()
        i0 = len(self.ev)
        await self.say(name)
        want = MANIFEST[name]["text"]
        you = await self.wait(lambda e: e["ev"] == "text" and e["data"].startswith("you:"),
                              15, since=i0)
        if not you:
            return {"prompt": name, "ok": False, "why": "no transcript reached server"}
        j = self.ev.index(you)
        audio = await self.wait(lambda e: e["ev"] == "audio_chunk", timeout, since=j)
        done = await self.wait(lambda e: e["ev"] == "state" and e["data"] == "listening",
                               timeout, since=j)
        heard = you["data"][4:]
        lat = round(audio["t"] - you["t"]) if audio else None
        ok = bool(audio and done) and similar(heard, want) >= 0.5
        return {"prompt": name, "ok": ok, "heard": heard, "server_to_audio_ms": lat,
                "why": "" if ok else f"audio={bool(audio)} listening={bool(done)} "
                                     f"sim={similar(heard, want):.2f}"}

    def check(self, name, ok, **detail):
        self.checks.append({"check": name, "ok": bool(ok), **detail})
        print(f"\n[{'PASS' if ok else 'FAIL'}] {name} {json.dumps(detail, ensure_ascii=False)[:300]}\n",
              flush=True)

    def phantoms(self, since=0):
        """'you' turns that don't match any prompt we played = Infy (or noise)
        triggered a turn nobody asked for."""
        texts = [p[2] for p in self.played]
        return [e["data"][4:] for e in self.ev[since:]
                if e["ev"] == "text" and e["data"].startswith("you:")
                and max([similar(e["data"][4:], t) for t in texts] or [0]) < 0.5]


async def scenario_ten(r):
    i0 = len(r.ev)
    res = [await r.ask_turn(f"q{i:02d}") for i in range(1, 11)]
    for x in res:
        print("   ", x)
    lat = sorted(x["server_to_audio_ms"] for x in res if x.get("server_to_audio_ms"))
    r.check("10 consecutive real-mic turns", all(x["ok"] for x in res) and not r.phantoms(i0),
            passed=sum(x["ok"] for x in res), phantoms=r.phantoms(i0),
            gated=sum(1 for e in r.ev[i0:] if e["ev"] in ("gated", "echo")),
            first_audio_p50_ms=lat[len(lat) // 2] if lat else None, turns=res)


async def scenario_barge(r):
    await r.idle()
    i0 = len(r.ev)
    await r.say("long")
    sp = await r.wait(lambda e: e["ev"] == "audio_chunk", 60, since=i0)
    await asyncio.sleep(2.5)
    i1 = len(r.ev)
    await r.say("barge")
    vad = await r.wait(lambda e: e["ev"] == "vad_interrupt", 3, since=i1)
    you = await r.wait(lambda e: e["ev"] == "text" and e["data"].startswith("you:")
                       and "thunder" in e["data"].lower(), 15, since=i1)
    await r.idle()
    # stale audio: chunks of the interrupted turn arriving after the cut
    old_turn = next((e["data"] for e in reversed(r.ev[:i1]) if e["ev"] == "audio_chunk"), None)
    stale = [e for e in r.ev[(r.ev.index(vad) if vad else i1):]
             if e["ev"] == "audio_chunk" and e["data"] == old_turn]
    r.check("barge-in interrupts and answers the new question",
            bool(sp and vad and you) and not stale,
            speaking=bool(sp), vad_interrupt=bool(vad), new_question=you and you["data"],
            stale_chunks_after_cut=len(stale))


async def scenario_stopstart(r):
    await r.idle()
    i0 = len(r.ev)
    await r.say("stop")
    st = await r.wait(lambda e: e["ev"] == "state" and e["data"] == "stopped", 8, since=i0)
    i1 = len(r.ev)
    await r.say("while_stopped")
    await asyncio.sleep(6)
    await r.pull()
    leaked = [e for e in r.ev[i1:] if e["ev"] == "text"]
    i2 = len(r.ev)
    await r.say("start")
    lis = await r.wait(lambda e: e["ev"] == "state" and e["data"] == "listening", 10, since=i2)
    regreet = [e for e in r.ev[i2:] if e["ev"] == "text" and e["data"].startswith("bot:")]
    after = await r.ask_turn("after_start")
    r.check("voice stop/start cycle",
            bool(st and lis) and not leaked and not regreet and after["ok"],
            stopped=bool(st), ignored_while_stopped=not leaked, resumed=bool(lis),
            regreeted=bool(regreet), after_start=after)


async def scenario_silence(r, secs):
    await r.idle()
    i0 = len(r.ev)
    print(f"  .. {secs}s of silence")
    t_end = time.time() + secs
    while time.time() < t_end:
        await r.pull()
        await asyncio.sleep(1)
    restarts = sum(1 for e in r.ev[i0:] if e["ev"] == "stt_start")
    unsolicited = [e["data"] for e in r.ev[i0:] if e["ev"] == "text"]
    after = await r.ask_turn("after_silence")
    r.check(f"silence recovery ({secs}s) + no unsolicited speech",
            after["ok"] and not unsolicited, stt_restarts=restarts,
            unsolicited=unsolicited, after_silence=after)


async def scenario_reconnect(r):
    await r.idle()
    i0 = len(r.ev)
    await r.cdp.eval("window.__sockets.at(-1).close()")
    rec = await r.wait(lambda e: e["ev"] == "ready", 15, since=i0)
    await r.idle(20)
    bot_after = [e for e in r.ev[i0:] if e["ev"] == "text"]
    after = await r.ask_turn("after_reconnect")
    r.check("websocket drop → auto-reconnect, history kept, no re-greet",
            bool(rec) and rec["data"] == "resumed" and not bot_after and after["ok"],
            ready=rec and rec["data"], regreeted=bool(bot_after), after=after)


async def scenario_hindi(r):
    await r.idle()
    await r.cdp.eval("[...document.querySelectorAll('.infy-lang button')].find(b => b.textContent.includes('हिं'))?.click()")
    await asyncio.sleep(2)
    res = [await r.ask_turn("hi_q1"), await r.ask_turn("hi_q2")]
    deva = lambda s: bool(re.search(r"[\u0900-\u097f]", s or ""))
    hi_answers = [e["data"] for e in r.ev if e["ev"] == "text" and e["data"].startswith("bot:")][-4:]
    i0 = len(r.ev)
    await r.say("hi_stop")
    st = await r.wait(lambda e: e["ev"] == "state" and e["data"] == "stopped", 8, since=i0)
    await r.cdp.eval("[...document.querySelectorAll('.infy-lang button')].find(b => b.textContent.trim() === 'EN')?.click()")
    await asyncio.sleep(2)
    await r.say("start")
    await r.wait(lambda e: e["ev"] == "state" and e["data"] == "listening", 10, since=i0)
    en = await r.ask_turn("en_back")
    en_ans = [e["data"] for e in r.ev if e["ev"] == "text" and e["data"].startswith("bot:")][-1:]
    r.check("Hindi ↔ English switching",
            all(x["ok"] and deva(x["heard"]) for x in res) and any(deva(a) for a in hi_answers)
            and bool(st) and en["ok"] and not any(deva(a) for a in en_ans),
            hindi_turns=res, hindi_answer_sample=hi_answers[-1:], hindi_stop=bool(st),
            english_after=en, english_answer=en_ans)


def page_ws():
    for _ in range(40):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json", timeout=2))
            for t in tabs:
                if t.get("type") == "page":
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("chrome devtools not reachable")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="ten,barge,stopstart,silence,reconnect,hindi")
    ap.add_argument("--silence", type=int, default=75)
    ap.add_argument("--name", default=time.strftime("%Y%m%d-%H%M%S"))
    args = ap.parse_args()
    only = args.only.split(",")
    profile = ROOT / "chrome-profile-loopback"
    shutil.rmtree(profile, ignore_errors=True)
    proc = subprocess.Popen([
        CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--use-fake-ui-for-media-stream",          # auto-allow the REAL mic
        "--autoplay-policy=no-user-gesture-required",
        "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    r = None
    try:
        async with websockets.connect(page_ws(), max_size=32 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.cmd("Page.enable")
            await cdp.cmd("Runtime.enable")
            await cdp.cmd("Page.addScriptToEvaluateOnNewDocument", source=HOOK)
            await cdp.cmd("Page.navigate", url=APP)
            await asyncio.sleep(3)
            r = Run(cdp)
            await cdp.eval("document.querySelector('.infy-orb')?.click()")
            greet = await r.wait(lambda e: e["ev"] == "text" and e["data"].startswith("bot:"), 30)
            await r.idle()
            greets = sum(1 for e in r.ev if e["ev"] == "text" and e["data"].startswith("bot:"))
            r.check("mic start → one greeting → listening", bool(greet) and r.last_state() == "listening",
                    greeting=greet and greet["data"], bot_lines=greets)
            for name, fn in [("ten", scenario_ten), ("barge", scenario_barge),
                             ("stopstart", scenario_stopstart),
                             ("silence", lambda r: scenario_silence(r, args.silence)),
                             ("reconnect", scenario_reconnect), ("hindi", scenario_hindi)]:
                if name in only:
                    print(f"\n=== {name} ===", flush=True)
                    try:
                        await fn(r)
                    except Exception as e:  # keep going, record the failure
                        r.check(name, False, error=f"{type(e).__name__}: {e}")
            r.check("no phantom turns over the whole run", not r.phantoms(), phantoms=r.phantoms())
    finally:
        proc.terminate()
        if r:
            res = {"name": args.name, "checks": r.checks, "played": r.played, "events": r.ev}
            p = out / f"loopback_{args.name}.json"
            p.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
            print("\n==== SUMMARY ====")
            for c in r.checks:
                print(f"  {'PASS' if c['ok'] else 'FAIL'}  {c['check']}")
            print(f"results: {p}")


if __name__ == "__main__":
    asyncio.run(main())
