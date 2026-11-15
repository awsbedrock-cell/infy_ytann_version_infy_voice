# TTS Spike — listen & pick the "Infy" persona voice

Timebox: **~1 day**. Deliverable: 4 audition reels + 4 consistency reels + a
latency table. You listen, you pick. No production code — this is an A/B spike.

## Goal

Pick the cascade-mode TTS model for Infy's voice. Ranked criteria:

1. **Tonality / naturalness** (THE point — sounds human, warm, tutor-like,
   not mechanical). Decided by **your ear** on the audition reels.
2. **Consistency over long runs** — the voice must NOT drift in timbre,
   pitch, or pace across a long answer or a long session (the real pipeline
   synthesizes sentence-by-sentence; that's exactly where models wobble).
3. **Bilingual** — Hindi + English + Hinglish in one consistent "Infy".
4. **Latency** — per-sentence synth keeps first-audio conversational.
5. **Integration robustness** — deps, streaming, determinism, VRAM fit.
6. **License** — flagged; the team makes the commercial decision.

## Candidates

| Model | License | Bilingual | Reference audio needed | Notes |
|---|---|---|---|---|
| Piper | MIT | 2 voices (en + hi_IN) | no | bulletproof, onnx, real-time on CPU |
| MeloTTS | MIT | 1 model (en + hi + Indian accents) | no | single "Infy" across languages |
| F5-TTS | MIT | 1 model | yes (ships a default) | highest quality, diffusion |
| XTTS-v2 | CPML (non-commercial; Coqui commercial license available) | 1 model (en+hi code-switch) | yes | best natural/persona, clone-capable |

Baseline: current `edge-tts` (`en-IN-NeerjaExpressiveNeural`,
`hi-IN-SwaraNeural`) — the known "good enough" reference.

**Persona-without-recording trick:** F5/XTTS need a reference speaker wav; we
have no human recording, so seed the reference from a clean edge-tts clip.
That gives a fixed, consistent persona voice without a human voice, and tests
whether the model makes the seed sound *more* natural than the seed.

## Deliverable 1 — Audition reels (the "listen & pick")

One continuous file per model, **~45–60 s**, a coherent mini-session so you
judge "does this feel like a real tutor?", not "does one word sound nice":

`reel_A.mp3` … `reel_D.mp3` (blind-labeled; a `reel_key.txt` maps A–D → model
after you've listened).

Reel script (same for every model):
1. *greeting* — "Hi! I'm Infy. I'll be your science tutor today."
2. *explain* — "Air pressure is the force air pushes on everything around us. When warm air rises, cooler air rushes in to take its place — and that movement is what we feel as wind."
3. *praise* — "That's exactly right! Winds form when air moves from high pressure to low pressure."
4. *gentle correction* — "Not quite. Think again — what happens to the pressure when the wind speed rises?"
5. *Hindi* — "हवा का दबाव क्या होता है? चक्रवात कैसे बनते हैं?"
6. *Hinglish* — "Pressure basically air ka force hota hai, right?"

How you pick: play A–D back to back, note the 1–5 naturalness + "feels like
one person" per reel, then open `reel_key.txt`.

## Deliverable 2 — Long-run consistency

One longer passage (~60 s, 11 sentences, a coherent chapter explanation) per
model, synthesized **two ways**:

- `long_whole_<model>.mp3` — the passage fed as one call (if supported).
- `long_chunked_<model>.mp3` — the passage fed **sentence-by-sentence**, which
  is exactly what `pipeline.py` does (`speak(sent, lang)` per chunk). **This is
  the one that matters.**

For the chunked version, the harness also reports per-sentence prosody so
drift is measurable, not just felt: fundamental frequency (F0 mean/std),
speaking rate (syllables/s), and RMS energy per sentence → min/mean/max across
the passage. High spread = the voice "wobbles" between sentences = fails
long-run consistency.

Consistency pass/fail: the chunked audio sounds like **one continuous person**
(no per-sentence re-record feel), and F0/rate spread stays low. For F5/XTTS,
the harness pins a **fixed seed + the same reference wav** across all
sentences — the implementation detail that guarantees long-run stability.

## Method

Harness `bench_tts_spike.py` (extend the existing `bench_tts.py` pattern):

```bash
python bench_tts_spike.py --model {piper|melotts|f5|xtts|edge} [--device cuda|cpu]
```

Produces, per model: `reel_*.mp3`, `long_whole_*.mp3`, `long_chunked_*.mp3`,
`timing.json`, `prosody.json` (F0/rate/energy per sentence). All audio in
`spike_out/`.

## Scoring sheet (you fill in after listening)

| reel | naturalness 1–5 | "one person" y/n | notes |
|---|---|---|---|
| A | | | |
| B | | | |
| C | | | |
| D | | | |

Plus per model (from `timing.json`/`prosody.json`): CPU ms/sentence, GPU
ms/sentence, VRAM, F0 spread (consistency), license flag.

## Exit criteria

- Chosen model scores **≥ 4/5 naturalness on both English and Hindi**, and
  sounds like one person across the reel AND the chunked long passage.
- Per-sentence synth within budget on the target 16 GB GPU (< ~2 s, ideally
  < 1 s — see ARCHITECTURE.md).
- If two models tie, a second pass with a longer reel breaks the tie.
- Output: decision section here filled in (primary + fallback + license flag).

## Out of scope

- Streaming integration into `providers.py` (implementation plan, after pick).
- Cloning a *human* voice (no recording exists).
- Persona *behavior* polish (`repeat`/`quiz`/proactive questions) — separate,
  LLM-side, already mostly built.
