"""make_utterances.py — synthesize the spoken test prompts (edge-tts mp3)
used by loopback.py. Uses voices different from Infy's own so transcripts of
the "student" are distinguishable from Infy echo.

    python tests/e2e/make_utterances.py
"""
import asyncio, json
from pathlib import Path

import edge_tts

OUT = Path(__file__).resolve().parent / "utterances"
EN, HI = "en-IN-PrabhatNeural", "hi-IN-MadhurNeural"

PROMPTS = {
    # 10 consecutive English questions
    **{f"q{i:02d}": (EN, q) for i, q in enumerate([
        "How are winds formed?",
        "What is a storm surge?",
        "What is high pressure?",
        "What causes cyclones?",
        "Why do sea breezes happen?",
        "What is low pressure?",
        "How do storms start?",
        "What is wind speed?",
        "What is air pressure?",
        "How do cyclones get their names?",
    ], 1)},
    "barge": (EN, "Wait, wait. Tell me about thunderstorms instead."),
    "long": (EN, "Explain in detail how a cyclone forms over the ocean."),
    "stop": (EN, "Infy stop."),
    "while_stopped": (EN, "What is a tornado?"),
    "start": (EN, "Infy start."),
    "after_start": (EN, "What is a breeze?"),
    "after_silence": (EN, "What is a monsoon?"),
    "after_reconnect": (EN, "Can you say that again more simply?"),
    "hi_q1": (HI, "हवा का दबाव क्या होता है?"),
    "hi_q2": (HI, "चक्रवात कैसे बनते हैं?"),
    "hi_stop": (HI, "इन्फी रुको।"),
    "en_back": (EN, "What is a thunderstorm?"),
}


async def main():
    OUT.mkdir(exist_ok=True)
    manifest = {}
    for name, (voice, text) in PROMPTS.items():
        path = OUT / f"{name}.mp3"
        if not path.exists():
            await edge_tts.Communicate(text, voice).save(str(path))
        manifest[name] = {"text": text, "voice": voice, "file": path.name}
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(manifest)} utterances in {OUT}")


asyncio.run(main())
