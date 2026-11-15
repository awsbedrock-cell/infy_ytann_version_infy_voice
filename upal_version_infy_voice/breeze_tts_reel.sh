#!/usr/bin/env bash
# ============================================================================
# breeze_tts_reel.sh — generate the "Infy" persona reel with Breeze-TTS-2.
#
# WHAT THIS DOES
#   Clones the Breeze inference code, installs deps in a FRESH venv, downloads
#   the ~7.6 GB checkpoint, then synthesizes the Infy English reel using
#   *Voice Design* — a natural-language description of the voice, no reference
#   audio needed. Output: /tmp/opencode/breeze_reel/reel_breeze.wav
#
# PREREQUISITES (run this on the 16 GB machine)
#   - Linux, Python 3.10+, NVIDIA GPU >= 12 GB (16 GB recommended)
#   - ~15 GB free disk (checkpoint + torch)
#   - The script makes its own venv, so your existing Python/torch is untouched.
#
# NOTE ON LANGUAGES
#   Breeze-TTS-2 speaks English + Chinese ONLY. Hindi/Hinglish are NOT
#   supported — Hindi stays on Piper in the cascade pipeline.
#
# NOTE ON LICENSE
#   Weights are "BreezeBlue Research and Non-Commercial". Commercial use needs
#   BreezeBlue's paid license. Source code is Apache-2.0.
#
# RUN
#   chmod +x breeze_tts_reel.sh && ./breeze_tts_reel.sh
# ============================================================================
set -euo pipefail

REPO=/tmp/opencode/breeze-tts
CKPT=/tmp/opencode/breeze-tts-2
OUT=/tmp/opencode/breeze_reel
VENV=$REPO/.venv
mkdir -p "$OUT"

# --- 1. clone inference code ------------------------------------------------
if [ ! -d "$REPO" ]; then
  git clone https://github.com/breezeblue-ai/breeze-tts.git "$REPO"
fi
cd "$REPO"

# --- 2. fresh venv + deps (torch 2.9.1 is pinned — must not share an env) ---
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt
"$VENV/bin/pip" install --quiet huggingface_hub

# --- 3. download checkpoint (~7.6 GB, once) ---------------------------------
if [ ! -f "$CKPT/config.json" ]; then
  "$VENV/bin/python" - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("BreezeBlue/Breeze-TTS-2", local_dir="/tmp/opencode/breeze-tts-2")
PY
fi

# --- 4. the Infy persona (Voice Design: plain-English voice description) ----
PERSONA="A warm, encouraging young Indian woman with a clear, friendly voice, a patient teacher's tone, and a calm, supportive delivery."

# The English reel — same lines as the Piper/edge/F5/XTTS reels, minus the
# Hindi/Hinglish (unsupported). One call => consistent persona across all lines.
REEL_TEXT="Hi! I'm Infy. I'll be your science tutor today. \
Air pressure is the force air pushes on everything around us. When warm air \
rises, cooler air rushes in to take its place, and that movement is what we \
feel as wind. \
That's exactly right! Winds form when air moves from high pressure to low \
pressure. \
Not quite. Think again, what happens to the pressure when the wind speed \
rises?"

# --- 5. synthesize ----------------------------------------------------------
"$VENV/bin/python" infer.py "$CKPT" \
  --text "$REEL_TEXT" \
  --instruction "$PERSONA" \
  --cfg-scale 4 \
  --seed 42 \
  --output "$OUT/reel_breeze.wav"

echo ""
echo "Done -> $OUT/reel_breeze.wav"
echo ""
echo "Variants (edit + rerun step 5):"
echo "  Voice Clone   (record a voice): add  --ref-audio ref.wav --ref-text \"<exact transcript>\""
echo "  Voice Direction (clone + steer): add  --ref-audio ref.wav --ref-text \"...\" --instruction \"Speak slowly, warm tone\""
echo "  Production streaming API:         \"$VENV/bin/python\" -m breeze_infer.api $CKPT --port 7860   (streams 24kHz PCM)"
