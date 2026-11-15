"""verify_tts_audio.py — REAL spoken-audio language verification.

Runs the real pipeline (llama worker + REAL edge-tts over the network),
records WHICH edge voice each sentence used, and saves the actual mp3
bytes to tests/e2e/audio/verify_*.mp3 so a human can play them back.

Checks:
  - English question -> every sentence voiced en-IN-* AND produces mp3
  - Devanagari question -> every sentence voiced hi-IN-* AND produces mp3
  - a mixed-script answer (forced) -> the Devanagari sentence goes to the
    hi voice while the English sentence goes to the en voice

    python tests/verify_tts_audio.py
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pipeline  # noqa: E402
import providers  # noqa: E402

DEVA = re.compile(r"[ऀ-ॿ]")
OUT = Path(__file__).parent / "e2e" / "audio"

# capture (text, language, voice, nbytes) per speak() call
CALLS = []


class SpyEdgeTTS:
    """Wraps the real EdgeTTS: records voice + collects real mp3 bytes."""
    name = "edge"

    def __init__(self):
        self._inner = providers.EdgeTTS()

    async def speak(self, text, language="en"):
        voice = providers._EDGE_VOICES.get((language or "en")[:2])
        buf = b""
        async for chunk in self._inner.speak(text, language):
            buf += chunk
            yield chunk
        CALLS.append({"text": text[:60], "lang": language,
                      "voice": voice, "mp3_bytes": len(buf),
                      "mp3": buf, "deva": bool(DEVA.search(text))})


class Sink:
    def __init__(self):
        self.msgs = []

    async def send(self, m):
        self.msgs.append(m)

    async def audio(self, b):
        pass


async def run_turn(s, q):
    await s.ask(q, typed=True)
    for _ in range(600):
        if any(m["type"] == "turn_end" and m["turn"] == s._turn_id
               for m in s._io.msgs):
            break
        await asyncio.sleep(0.1)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sink = Sink()
    s = pipeline.TutorSession(1, 8, "Pressure, Winds", "science",
                              sink.send, sink.audio,
                              tts=SpyEdgeTTS())
    s._io = sink
    await s.start(say_hello=False)

    results = []

    # --- English turn -----------------------------------------------------
    await run_turn(s, "what is air pressure")
    en_calls = CALLS[-len([c for c in CALLS]) or None:] if CALLS else []
    en_ok = all(c["voice"].startswith("en-IN") and c["mp3_bytes"] > 1000
                for c in en_calls) and bool(en_calls)
    for i, c in enumerate(en_calls):
        (OUT / f"verify_en_{i}.mp3").write_bytes(c["mp3"])
    print(f"EN voices: {[c['voice'] for c in en_calls]}")
    results.append(("EN all-en-voice+real-mp3", en_ok))

    # --- Hindi turn ---------------------------------------------------------
    CALLS.clear()
    await run_turn(s, "हवा कैसे चलती है")
    hi_calls = list(CALLS)
    hi_ok = all(c["voice"].startswith("hi-IN") and c["mp3_bytes"] > 1000
                for c in hi_calls) and bool(hi_calls)
    for i, c in enumerate(hi_calls):
        (OUT / f"verify_hi_{i}.mp3").write_bytes(c["mp3"])
    print(f"HI voices: {[c['voice'] for c in hi_calls]}")
    results.append(("HI all-hi-voice+real-mp3", hi_ok))

    # --- mixed-script: Devanagari sentence inside an English reply ------------
    CALLS.clear()

    class MixedLLM:
        name = "mixed"

        async def stream(self, system, history, question):
            yield "Here is the answer. "
            yield "हवा ऊपर उठती है. "
            yield "That is all."

    s.llm = MixedLLM()
    s.language = "English"
    await run_turn(s, "explain in one line")
    mix = list(CALLS)
    mix_ok = all(
        (c["voice"].startswith("hi-IN") if c["deva"]
         else c["voice"].startswith("en-IN")) and c["mp3_bytes"] > 200
        for c in mix) and len(mix) >= 2
    for i, c in enumerate(mix):
        (OUT / f"verify_mix_{i}.mp3").write_bytes(c["mp3"])
    print(f"MIX voices: {[(c['voice'], c['deva']) for c in mix]}")
    results.append(("MIX script->voice match", mix_ok))

    print("\n== SUMMARY ==")
    bad = 0
    for name, ok in results:
        print(f"  {name:<30} {'PASS' if ok else 'FAIL'}")
        bad += not ok
    print(f"\nsaved {len(list(OUT.glob('verify_*.mp3')))} mp3 files "
          f"under {OUT} — play them to confirm audible language")
    sys.exit(1 if bad else 0)


asyncio.run(main())
