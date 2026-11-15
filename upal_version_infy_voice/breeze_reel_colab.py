# ============================================================================
# breeze_reel_colab.py — generate the "Infy" Breeze-TTS-2 reel in Google Colab.
#
# HOW TO RUN (5 minutes, free)
#   1. Go to https://colab.research.google.com  ->  New notebook
#   2. Runtime -> Change runtime type -> GPU  (T4, 16 GB — free)
#   3. Upload this file to the Files panel (drag & drop)
#   4. In the first cell, run:   %run breeze_reel_colab.py
#   5. Download reel_breeze.mp3 from the Files panel when it prints DONE.
#
# WHY COLAB: Breeze needs ~7.7 GB VRAM (12 GB min per the model card).
# The free T4 has 16 GB. It will NOT fit on an 8 GB GPU.
#
# NOTE: English ONLY (that's Breeze). Hindi stays on Piper.
# NOTE: non-commercial weights — audition only, license before production use.
# ============================================================================
import subprocess
import sys
from pathlib import Path

CONTENT = Path("/content")

# --- 1. deps (same pins as the repo's requirements.txt; torch 2.9.1) --------
subprocess.run(
    [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
     "torch==2.9.1", "torchaudio==2.9.1", "qwen-tts==0.1.1",
     "transformers==4.57.3", "huggingface_hub", "soundfile"],
    check=True,
)

# --- 2. clone inference code (or reuse from a previous run) ------------------
REPO = CONTENT / "breeze-tts"
if not REPO.is_dir():
    subprocess.run(
        ["git", "clone", "--depth", "1",
         "https://github.com/breezeblue-ai/breeze-tts.git", str(REPO)],
        check=True,
    )

# --- 3. checkpoint (~7.6 GB, ~2-4 min download on Colab) ---------------------
from huggingface_hub import snapshot_download  # noqa: E402

CKPT = snapshot_download(
    "BreezeBlue/Breeze-TTS-2", local_dir=str(CONTENT / "breeze-tts-2")
)

# --- 4. persona (Voice Design: plain-English description, no ref audio) ------
PERSONA = (
    "A warm, encouraging young Indian woman with a clear, friendly voice, "
    "a patient teacher's tone, and a calm, supportive delivery."
)

# Same English lines as the Piper/edge reels so the comparison is fair.
REEL = (
    "Hi! I'm Infy. I'll be your science tutor today. "
    "Air pressure is the force air pushes on everything around us. When warm "
    "air rises, cooler air rushes in to take its place, and that movement is "
    "what we feel as wind. "
    "That's exactly right! Winds form when air moves from high pressure to "
    "low pressure. "
    "Not quite. Think again, what happens to the pressure when the wind "
    "speed rises?"
)

# --- 5. synthesize (~1-2 min on T4) ------------------------------------------
subprocess.run(
    [sys.executable, str(REPO / "infer.py"), CKPT,
     "--text", REEL,
     "--instruction", PERSONA,
     "--cfg-scale", "4",
     "--seed", "42",
     "--output", str(CONTENT / "reel_breeze.wav")],
    check=True,
)

# --- 6. mp3 for easy listening/sharing --------------------------------------
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error",
     "-i", str(CONTENT / "reel_breeze.wav"),
     "-b:a", "192k", str(CONTENT / "reel_breeze.mp3")],
    check=True,
)

print("\nDONE -> reel_breeze.wav + reel_breeze.mp3 in the Files panel.")
print("Download reel_breeze.mp3 and listen. (Hindi leg is unchanged: Piper.)")
