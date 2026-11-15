"""bench_tts_spike.py — generate the Infy TTS audition reels + long-run
consistency test, so a human can LISTEN and pick the model.

Usage:
    python bench_tts_spike.py --model {edge|piper|melotts|f5|xtts} --label A [--device cuda|cpu]

Produces in spike_out/:
    reel_<label>.wav/.mp3          — ~45-60s audition reel (the "listen & pick")
    long_whole_<label>.wav         — long passage, one call
    long_chunked_<label>.wav       — long passage, sentence-by-sentence (what pipeline.py does)
    <label>_timing.json            — per-line synth ms
    <label>_prosody.json           — per-sentence duration/RMS/F0 (drift detector)
    reel_key.txt                   — label -> model mapping (open AFTER listening)
"""
import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

OUT = Path(__file__).parent / "spike_out"
OUT.mkdir(exist_ok=True)

# --- fixed corpus -----------------------------------------------------------
REEL = [
    ("E1", "en", "Hi! I'm Infy. I'll be your science tutor today."),
    ("E2", "en",
     "Air pressure is the force air pushes on everything around us. When warm "
     "air rises, cooler air rushes in to take its place — and that movement "
     "is what we feel as wind."),
    ("E3", "en", "That's exactly right! Winds form when air moves from high "
                 "pressure to low pressure."),
    ("E4", "en", "Not quite. Think again — what happens to the pressure when "
                 "the wind speed rises?"),
    ("H1", "hi", "हवा का दबाव क्या होता है? चक्रवात कैसे बनते हैं?"),
    ("C1", "en", "Pressure basically air ka force hota hai, right?"),
]

LONG = [
    ("en", "Air pressure is the force that air pushes on everything around us."),
    ("en", "When warm air rises, cooler air rushes in to take its place."),
    ("en", "That movement of air is what we feel as wind."),
    ("en", "Wind blows from high pressure areas toward low pressure areas."),
    ("en", "Near the equator, the air is warm, so the pressure is lower."),
    ("en", "Cyclones form over warm ocean water when the air rises quickly."),
    ("en", "As the warm air rises, more air swirls in to replace it."),
    ("en", "This spinning column of air is called a cyclone."),
    ("en", "On land, a cyclone loses its source of warm, moist air, and so it "
           "weakens."),
    ("hi", "हम दबाव को बैरोमीटर से नापते हैं।"),
    ("hi", "दबाव अचानक गिरे तो तूफ़ान आने की आशंका होती है।"),
]

MODELS_DIR = Path("/tmp/opencode/tts_models")
REF_WAV = MODELS_DIR / "ref.wav"
REF_TEXT = ("Hi, I'm Infy, your friendly science tutor. "
            "Let's learn something new together today.")


def make_ref():
    """Seed reference voice from edge-tts (no human recording)."""
    if REF_WAV.exists():
        return str(REF_WAV)
    import edge_tts

    async def go():
        comm = edge_tts.Communicate(REF_TEXT, "en-IN-NeerjaExpressiveNeural")
        buf = b""
        async for ch in comm.stream():
            if ch["type"] == "audio":
                buf += ch["data"]
        return buf

    mp3 = asyncio.run(go())
    mp3p = tempfile.mktemp(suffix=".mp3")
    Path(mp3p).write_bytes(mp3)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3p,
                    "-ac", "1", "-ar", "24000", str(REF_WAV)], check=True)
    return str(REF_WAV)


def _mp3_to_wav(mp3: bytes, sr=24000) -> str:
    mp3p = tempfile.mktemp(suffix=".mp3")
    Path(mp3p).write_bytes(mp3)
    wavp = mp3p + ".wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3p,
                    "-ac", "1", "-ar", str(sr), wavp], check=True)
    return wavp


# --- backends ---------------------------------------------------------------
# Each returns a path to a mono WAV at its native sample rate.

class EdgeBackend:
    name = "edge"
    voices = {"en": "en-IN-NeerjaExpressiveNeural", "hi": "hi-IN-SwaraNeural"}

    def synth(self, text, lang):
        import edge_tts
        voice = self.voices.get(lang, self.voices["en"])

        async def go():
            buf = b""
            async for ch in edge_tts.Communicate(text, voice).stream():
                if ch["type"] == "audio":
                    buf += ch["data"]
            return buf
        return _mp3_to_wav(asyncio.run(go()))


class PiperBackend:
    name = "piper"

    def __init__(self):
        from piper import PiperVoice
        b = MODELS_DIR / "piper"
        self.voices = {
            "en": PiperVoice.load(
                str(b / "en/en_US/lessac/medium/en_US-lessac-medium.onnx"),
                str(b / "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json")),
            "hi": PiperVoice.load(
                str(b / "hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx"),
                str(b / "hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx.json")),
        }

    def synth(self, text, lang):
        import wave
        wavp = tempfile.mktemp(suffix=".wav")
        with wave.open(wavp, "wb") as wf:
            self.voices.get(lang, self.voices["en"]).synthesize_wav(text, wf)
        return wavp


class MeloBackend:
    name = "melotts"

    def __init__(self, device):
        from melo.api import TTS
        self.tts = {"en": TTS(language="EN", device=device)}
        try:
            self.tts["hi"] = TTS(language="HI", device=device)
        except Exception as e:
            print(f"[melotts] HI unavailable: {e}")

    def synth(self, text, lang):
        t = self.tts.get(lang) or self.tts["en"]
        wavp = tempfile.mktemp(suffix=".wav")
        t.tts_to_file(text, t.hps.data.spk2id["EN-Default"]
                      if lang == "en" else t.hps.data.spk2id[list(t.hps.data.spk2id)[0]],
                      wavp, speed=1.0)
        return wavp


class F5Backend:
    name = "f5"

    def __init__(self, device):
        from f5_tts.api import F5TTS
        self.tts = F5TTS(device=device)
        self.ref = make_ref()

    def synth(self, text, lang):
        wav, sr, _ = self.tts.infer(ref_file=self.ref, ref_text=REF_TEXT,
                                    gen_text=text, seed=0)
        wavp = tempfile.mktemp(suffix=".wav")
        sf.write(wavp, np.asarray(wav).squeeze(), sr)
        return wavp


class XTTSBackend:
    name = "xtts"

    def __init__(self, device):
        from TTS.api import TTS
        self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2",
                       gpu=(device == "cuda"))
        self.ref = make_ref()

    def synth(self, text, lang):
        wav = self.tts.tts(text=text, speaker_wav=self.ref,
                           language=lang if lang in ("en", "hi") else "en")
        wavp = tempfile.mktemp(suffix=".wav")
        sf.write(wavp, np.asarray(wav).squeeze(), 24000)
        return wavp


BACKENDS = {"edge": EdgeBackend, "piper": PiperBackend, "melotts": MeloBackend,
            "f5": F5Backend, "xtts": XTTSBackend}


# --- prosody (dependency-free drift detector) -------------------------------
def _f0(x, sr):
    """Crude autocorrelation pitch (Hz) of the loudest voiced frame."""
    if len(x) < sr // 40:
        return None
    # voice the segment: use a 40ms window around the max-energy point
    win = sr // 25
    i = int(np.argmax(x ** 2))
    a, b = max(0, i - win // 2), min(len(x), i + win // 2)
    seg = x[a:b]
    if len(seg) < win // 2 or np.sqrt(np.mean(seg ** 2)) < 1e-3:
        return None
    seg = seg - seg.mean()
    ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
    ac /= (ac[0] + 1e-9)
    lo, hi = int(sr / 400), int(sr / 60)   # 60-400 Hz
    if hi >= len(ac):
        return None
    lag = int(np.argmax(ac[lo:hi]) + lo)
    if ac[lag] < 0.3:
        return None
    return round(sr / lag, 1)


def prosody(wavp):
    x, sr = sf.read(wavp, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    rms = float(np.sqrt(np.mean(x ** 2)))
    dur = round(len(x) / sr, 3)
    return {"dur_s": dur, "rms": round(rms, 5), "f0_hz": _f0(x, sr)}


# --- run --------------------------------------------------------------------
def run(model, label, device):
    key = (label, model)
    cls = BACKENDS[model]
    if model in ("melotts", "f5", "xtts"):
        be = cls(device)
    else:
        be = cls()

    timing, prosody_rows = {}, []
    print(f"\n=== {model} (label {label}) ===")

    # reel
    segs = []
    for sid, lang, text in REEL:
        t0 = time.perf_counter()
        wavp = be.synth(text, lang)
        data, sr = sf.read(wavp, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        segs.append(data)
        segs.append(np.zeros(int(sr * 0.4), dtype="float32"))
        timing[sid] = round((time.perf_counter() - t0) * 1000, 1)
        print(f"  reel {sid} ({lang}): {timing[sid]} ms")
    reel = np.concatenate(segs)
    sf.write(OUT / f"reel_{label}.wav", reel, sr)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(OUT / f"reel_{label}.wav"),
                    str(OUT / f"reel_{label}.mp3")], check=True)

    # long chunked (the one that matters) + prosody per sentence
    csegs, ctiming = [], []
    for lang, text in LONG:
        t0 = time.perf_counter()
        wavp = be.synth(text, lang)
        data, sr = sf.read(wavp, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        csegs.append(data)
        csegs.append(np.zeros(int(sr * 0.4), dtype="float32"))
        ctiming.append(round((time.perf_counter() - t0) * 1000, 1))
        prosody_rows.append(prosody(wavp))
    sf.write(OUT / f"long_chunked_{label}.wav", np.concatenate(csegs), sr)
    print(f"  long chunked: {len(LONG)} sentences, "
          f"{sum(ctiming)} ms total")

    # long whole (one call, if the backend treats it as one utterance)
    t0 = time.perf_counter()
    whole = be.synth(" ".join(t for _, t in LONG), LONG[0][0])
    sf.write(OUT / f"long_whole_{label}.wav",
             *sf.read(whole, dtype="float32"), )
    print(f"  long whole: {round((time.perf_counter()-t0)*1000,1)} ms")

    (OUT / f"{label}_timing.json").write_text(json.dumps(timing, indent=2))
    (OUT / f"{label}_prosody.json").write_text(json.dumps(prosody_rows, indent=2))

    keyf = OUT / "reel_key.txt"
    lines = keyf.read_text().splitlines() if keyf.exists() else []
    lines = [l for l in lines if not l.startswith(f"{label}:")]
    lines.append(f"{label}: {model}")
    keyf.write_text("\n".join(sorted(lines)) + "\n")
    print(f"  -> reel_{label}.mp3 written; key: {label} = {model}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=list(BACKENDS))
    ap.add_argument("--label", required=True)
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args()
    run(a.model, a.label, a.device)
