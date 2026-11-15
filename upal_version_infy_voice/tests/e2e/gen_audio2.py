"""gen_audio2.py — build 16kHz mono PCM WAVs from edge-tts voices (Indian
accent -> matches the app's en-IN/hi-IN recognizers).
Each test file = list of (text, voice, gap_after_seconds)."""
import asyncio, io, struct, sys, wave
from pathlib import Path

import edge_tts
import miniaudio

OUT = Path(__file__).parent / "audio"
OUT.mkdir(exist_ok=True)


async def synth(text: str, voice: str) -> bytes:
    buf = io.BytesIO()
    async for ch in edge_tts.Communicate(text, voice).stream():
        if ch["type"] == "audio":
            buf.write(ch["data"])
    return buf.getvalue()


def to_pcm16k(mp3: bytes) -> bytes:
    d = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16,
                         nchannels=1, sample_rate=16000)
    return d.samples.tobytes()


def silence(secs: float) -> bytes:
    return b"\x00" * int(16000 * secs) * 2


def write_wav(path: Path, pcm: bytes):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    print("wrote", path, f"{len(pcm)/32000:.1f}s")


EN = "en-IN-NeerjaExpressiveNeural"
HI = "hi-IN-SwaraNeural"

TESTS = {
    "test1": [
        ("in fee start", EN, 7),
        ("how are winds formed", EN, 9),
        ("in fee stop", EN, 10),
    ],
    "test2": [
        ("in fee start", EN, 7),
        ("what is a storm surge", EN, 12),
        ("what is the capital of france", EN, 10),
        ("in fee stop", EN, 4),
    ],
    "test3": [
        ("in fee start", EN, 7),
        ("हवा कैसे बनती है", HI, 14),
        ("in fee stop", EN, 4),
    ],
    "test4": [
        ("in fee start", EN, 7),
        ("how are winds formed", EN, 5),
        ("in fee stop", EN, 6),
        ("in fee start", EN, 5),
        ("what is a storm surge", EN, 10),
        ("in fee stop", EN, 4),
    ],
}


async def main():
    for name, segs in TESTS.items():
        pcm = b""
        for text, voice, gap in segs:
            pcm += to_pcm16k(await synth(text, voice)) + silence(gap)
        write_wav(OUT / f"{name}.wav", pcm)


asyncio.run(main())
