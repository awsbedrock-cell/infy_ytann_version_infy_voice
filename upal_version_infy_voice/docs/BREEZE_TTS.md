# Breeze-TTS-2 — the "Infy" English persona leg

Candidate for the **English** voice in the cascade pipeline. Not a Hindi
solution — Hindi stays on Piper.

## What it is

Breeze TTS 2 (BreezeBlue, 2026) — an open-weight 3B text-to-speech model that
ranks **#1 among open-weight models** on the Artificial Analysis TTS
leaderboard, beating several frontier proprietary systems. Built for
real-time interaction: < 40 ms time-to-first-audio and ~3.1× real-time
generation on an H100 (warmed-up fast path).

Three capabilities, all relevant to the "persona" requirement:

| Mode | What it does | Needs reference audio? |
|---|---|---|
| **Voice Design** | Create a voice from a natural-language description ("a warm, encouraging young Indian woman…") | no |
| **Voice Clone** | Clone a speaker from reference audio + its exact transcript | yes |
| **Voice Direction** | Clone a voice while steering tone/emotion/pace/delivery | yes |

Voice Design is the fit for "Infy": a fixed, consistent persona described in
one sentence, no recorded voice required.

## Key facts

- **Languages:** English + Chinese only. **No Hindi/Hinglish.**
- **License:** source Apache-2.0, but **weights = `breezeblue-research-and-non-commercial`**. Commercial use needs BreezeBlue's paid license (same "license later if we like it" bucket as XTTS-v2, but unlike XTTS it's actually fast).
- **Size / VRAM:** 3B params, ~7.6 GB checkpoint download, ~7.7 GB VRAM eager (12 GB GPU minimum, 16 GB recommended).
- **Deps:** pins `torch==2.9.1`, `qwen-tts==0.1.1` (audio tokenizer), `transformers==4.57.3` — must run in a **fresh venv**.

## Run

```bash
chmod +x breeze_tts_reel.sh && ./breeze_tts_reel.sh
```

Output: `/tmp/opencode/breeze_reel/reel_breeze.wav` (the Infy English reel,
Voice Design, `--cfg-scale 4`, `--seed 42`).

The script is self-contained: clones `breezeblue-ai/breeze-tts`, makes a venv,
downloads the checkpoint, synthesizes, and prints the clone/direction/streaming
variants.

## How it fits the architecture

```
cascade pipeline: LLM → chunker → per-sentence TTS
  ├─ English span → Breeze-TTS-2 (Voice Design "Infy")   ← new
  ├─ Hindi span   → Piper hi_IN-priyamvada                ← unchanged
  └─ Hinglish     → word-level segmentation routes each span to the right voice
```

License and VRAM are the two gates: non-commercial (team decides), and it
needs the 16 GB machine (won't run on the 8 GB dev laptop).

## What to listen for in the sample

1. **Tonality** — does the Voice Design description produce a warm, natural
   tutor, or does it read?
2. **Persona consistency** — the whole reel is one generation, so the voice
   should hold across sentences.
3. **Latency** — per-sentence synth time on the target GPU (Breeze's claim is
   low-latency streaming; confirm it feels responsive at tutor pace).
4. **English-only** — confirm the Hindi/Hinglish gap is real, so Piper keeps
   that half of the job.

## Decision

If the English tonality clears the bar, the stack becomes **Breeze (en) +
Piper (hi) + Hinglish routing** — with a non-commercial license flag for the
team. If it doesn't, Piper (en+hi) + Hinglish routing remains the MIT-safe
default.

## Local attempt — blocked (do NOT retry on the 8 GB dev box)

Tried to run it on the 8 GB RTX 3070 dev box. Two hard blockers, both
confirmed empirically:

1. **VRAM:** the model needs ~7.7 GB; the box has ~6.3 GB free after the
   desktop → `CUDA error: out of memory` during `model.to(device)`. Needs the
   12 GB minimum / 16 GB recommended. (The checkpoint, torch 2.9.1, and env
   are already downloaded/installed on this box if the team can mount it.)
2. **NVML driver mismatch:** torch 2.9.1's CUDA allocator hard-asserts on
   `nvmlInit_v2()` — this box's kernel driver is 595.84 but the user-space
   `libnvidia-ml.so` is 595.99 (driver updated without a reboot). Workaround
   `PYTORCH_NO_CUDA_MEMORY_CACHING=1` bypasses NVML but then hits the OOM.
   **Fix on any box that shows this: reboot to reload the kernel driver.**

Neither is a model problem — both are this box. The 16 GB machine should run
the script cleanly; if it hits the NVML assert, reboot first.

### Colab path (no local GPU needed)

`breeze_reel_colab.py` in the repo root does the same job on a **free Colab
T4 (16 GB)**: paste into a notebook, set Runtime → GPU, `%run
breeze_reel_colab.py`, download `reel_breeze.mp3`. Watchouts: free T4
availability varies; if torch 2.9.1 cu128 reports "no kernel image" on the
T4 (sm_75), fall back to the Colab preinstalled torch (skip the torch pin in
step 1). Job is ~10 min total, well inside session limits.
