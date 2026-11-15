# Infy Voice — Architecture Comparison (Stage 2)

Status: **desk study + partial local measurements**. Prices were read from the
official pricing pages on 2026-10-29; latency numbers marked *measured* come
from this repo's scripts on the dev laptop; everything else is vendor-stated
or an estimate and is labelled so. No hosted speech-to-speech option has been
benchmarked yet.

Dev laptop (measured): Intel Core Ultra 7 155H, Intel Arc iGPU (no NVIDIA),
15.5 GB RAM, Windows; Ollama with `qwen3:8b`/`qwen3:14b`; no llama.cpp/GGUF.

## Options

### A. Current cascade: Web Speech STT → `/ws/tutor` → FTS5 RAG → LLM → TTS
LLM is switchable (`LLM_PROVIDER=ollama|llamacpp|gemini`), TTS is edge-tts or
browser speech.

| | A-local (Ollama qwen3:8b) | A-hosted LLM (Gemini 3.5 Flash text) |
|---|---|---|
| LLM first sentence, *measured* (`tests/bench_ttft.py`, 5 prompts) | p50 8.4 s (2.3–14.7 s; Hindi slowest) | p50 1.15 s (1.08–1.22 s) |
| First audio over WebSocket, *measured* (`tests/ws_turns.py`, 10 turns) | p50 12.8 s, max 22.6 s (grows with history) | p50 3.6 s, max 5.1 s (9/10 turns; 1 empty reply) |
| Hindi | Answers in Devanagari; slow; quality not yet scored | Answers in Devanagari; quality not yet scored |
| Interruption | Client VAD on an echo-cancelled mic + turn ids (Stage 1); not verified with a real mic yet | same |
| Privacy | LLM local, **but** Chrome Web Speech sends mic audio to Google and edge-tts sends answer text to Microsoft | plus prompt/RAG text to Google |
| Cost | ₹0 marginal (electricity) | Gemini 3.5 Flash-Lite: $0.30 / $2.50 per 1M in/out tokens (official); a ~1.5k-token prompt + 150-token answer ≈ $0.0008/turn. 3.5 Flash price not checked yet |
| Lock-in | none | low (text LLM behind `providers.py`) |
| Weakness | 3–23 s latency on this iGPU laptop is not conversational; edge-tts is an unofficial endpoint with no SLA | half-duplex; three network hops; Web Speech quirks (restarts, no AEC on its own stream) |

Hardware for A-local to be conversational (<1.5 s first audio) needs a GPU
that runs a 3–8B model at ≥40 tok/s (e.g. a discrete NVIDIA card with 8 GB+
VRAM). That is an estimate, not measured here.

### B. Hosted speech-to-speech (audio in → audio out)
Browser streams 16 kHz PCM; the model does VAD, turn-taking, interruption and
voice natively.

- **Gemini Live API** — `gemini-3.8-live` (default Live model), `-extended-thinking`,
  legacy `gemini-3.1-flash-live-preview`. Official price: audio in $3.00/1M
  tokens ≈ **$0.005/min**, audio out $12.00/1M ≈ **$0.018/min**, text in $0.75,
  text out $4.50 per 1M. Free tier exists (data used to improve products).
  Docs: 70 languages, stateful WSS, tool use, ephemeral tokens, session
  management. <https://ai.google.dev/gemini-api/docs/live>,
  <https://ai.google.dev/gemini-api/docs/pricing>
- **OpenAI** — `gpt-live-1` billed **$0.05/min** per second of session
  (backend model/tool usage charged separately); `gpt-realtime-2.1` audio
  $32/$64 per 1M in/out, `gpt-realtime-2.1-mini` audio $10/$20 per 1M.
  WebRTC/WebSocket/SIP, server VAD, tools/MCP.
  <https://platform.openai.com/docs/pricing>,
  <https://platform.openai.com/docs/guides/realtime>
- First-audio latency: vendors describe these as low-latency; **not measured
  by us**. Interruption: native server-side VAD — strongest option on paper.
- Hindi: both list Hindi among supported languages; voice quality unscored.
- Privacy: raw student audio leaves the device to the vendor.
- Lock-in: high (session protocol, voices, VAD behaviour are vendor-specific).
- Grounding: B alone does **not** use our chapter RAG or citations.

### C. Hybrid: realtime voice front-end + Infy's own RAG/tools (recommended to prototype)
Gemini Live (or OpenAI) handles listening/speaking/barge-in; the model calls a
`retrieve(question, chapter)` tool served by our backend (FTS5 over
`varad.db`), answers from the returned passages, and we forward `sources` to
the UI. Commands (stop/start) and chapter scope stay ours.

- Keeps: course content, FTS index, grounding prompt, citations, command layer.
- Cost ≈ B + tool tokens. Estimate for Gemini 3.8 Live, 1 min of tutoring
  (mic streamed all minute, Infy speaking ~50%, ~2 retrievals of ~1.5k text
  tokens): 0.005 + 0.009 + ~0.002 ≈ **$0.016/min ≈ $1/hour** before context
  growth; budget **$0.02–0.04/min** until measured. `gpt-live-1` would be
  ≥ $0.05/min + backend model.
- Risks: tool-call round trip adds latency to grounded answers; vendor VAD
  may cut in on classroom noise; needs ephemeral tokens so keys never reach
  the browser.

## Comparison summary

| | A-local | A-hosted LLM | B S2S | C Hybrid |
|---|---|---|---|---|
| First audio | 12.8 s p50 (Ollama iGPU laptop) → **2.28 s p50 llamacpp CPU on i5-12450H** *(measured 2026-10-29, 10/10 turns; 3.4 s p50 in 20-turn run)* | 3.6 s p50 *(measured)* | **~5 s first call, real audio verified** *(measured 2026-10-29 from this network)* | same as B (implemented in this repo) |
| Naturalness | TTS sentences, half-duplex | same | native voice, full-duplex | native voice, full-duplex |
| Hindi | works, slow | works | supported (unscored) | supported (unscored) |
| Hinglish | Web Speech hi-IN/en-IN picks one script; RAG glossary covers Devanagari only *(observed)* | same | vendor claims code-switching; *untested* | same as B for voice; RAG side as A |
| Barge-in stop time | by design ≥250 ms sustained speech, then local cut; *real-mic unmeasured* | same | native server VAD — **wired in `use-infy-live.ts`** | same |
| Interruption | client VAD + WS cancel (5/5 cancels verified) | same | native `interrupted` event | native |
| Reliability | no network for LLM; Web Speech still online | +1 vendor; quota 429s seen on free tier | 1 vendor session; resumption-handle reconnect implemented | vendor + our backend; auto-fallback to A wired |
| Privacy | audio→Google STT, text→MS TTS | + text→Google | audio→vendor | audio→vendor, content stays ours |
| Running cost | ₹0 | ~$0.001/turn | **verified pricing: ~$0.023/min** ($3/1M audio-in + $12/1M audio-out; free dev tier) | ≈ $0.02–0.04/min |
| Lock-in | none | low | high | medium (provider adapter) |
| Hardware | this laptop: llamacpp CPU = 2.3–3.4 s first audio *(measured)* | any browser | any browser | any browser + our server |

Legend: *measured* = run on this laptop in this repo; *vendor* = official docs/pricing pages; *estimate/unmeasured* = not tested.

## Recommendation (updated 2026-10-29 — C is now implemented)
1. Keep A (cascade) as the always-available free fallback — `VOICE_MODE=cascade`
   or automatic fallback when the live path fails.
2. **Hybrid C is built**: `VOICE_MODE=live|cascade|auto` selects it; the mint
   (`/api/tutor/live`) issues single-use ephemeral tokens server-side, the
   browser drives the session in `use-infy-live.ts`/`live.ts` (PCM 16 kHz in,
   24 kHz out, native VAD barge-in, resumption-handle reconnect, session-time
   spending guard). Chapter context goes into the system prompt at mint time —
   answers stay chapter-grounded without extra tool calls.
3. Verified live from this machine 2026-10-29: mint → setup → question →
   real audio stream + transcription + turnComplete. First-call audio ~5 s
   (includes mint+setup; steady-state turns are faster).
4. Still to decide with the user's ear: real-mic A/B — same questions on both
   modes, compare perceived latency + Hindi naturalness + wake-word (live
   mode removes the Web Speech "infy" problem entirely).

## Open measurements (not done)
- Real-mic live-mode latency + barge-in from the user's actual mic (browser
  test now possible — key is configured).
- Hindi naturalness rating (blind, ≥2 Hindi speakers).
- OpenAI realtime adapter (second live provider) — not built; Gemini only.
