/* ============================================================================
 * live.ts — REAL socket voice pipeline (mini replica of the production Infy
 * stack: be/app.py /api/tutor/live → src/common/live/{live-session,pcm})
 *
 *   1. POST {API}/api/tutor/live → ephemeral token + ws_url
 *   2. WS → Google BidiGenerateContentConstrained?access_token=<token>
 *   3. Mic → 16 kHz mono PCM16 → base64 realtime_input.media_chunks
 *   4. Server binary frames → 24 kHz PCM → queued AudioContext playback
 * ==========================================================================*/

import type { MintResponse } from "./types";

/* >>> EXPERIMENT: where the mint endpoint lives ("" = same origin — the vite
 *   dev proxy or the production FastAPI static mount both work). */
export const LIVE_API = "";

export interface LiveSetup {
  vad?: Record<string, unknown>;
  activityHandling?: string;
  resumption?: Record<string, unknown>;
  transcription?: Record<string, unknown>;
}

/* >>> EXPERIMENT: setup knobs (override what the mint returns) */
export const SETUP: LiveSetup = {
  vad: {
    start_of_speech_sensitivity: "START_SENSITIVITY_HIGH",
    end_of_speech_sensitivity: "END_SENSITIVITY_HIGH",
    prefix_padding_ms: 120,
    silence_duration_ms: 300,
  },
  // activityHandling: "NO_INTERRUPTION",   // uncomment to disable barge-in
  resumption: {},
  transcription: {},
};

/* ---------------------------------------------------------------------------
 * 1. Mint — same request the real client makes.
 * -------------------------------------------------------------------------*/
export async function mintTutor(apiBase: string, chapterId?: number, lang?: string): Promise<MintResponse> {
  const r = await fetch(`${apiBase}/api/tutor/live`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...(chapterId ? { chapter_id: chapterId } : {}), ...(lang ? { lang } : {}) }),
  });
  if (!r.ok) throw new Error(`mint ${r.status}: ${(await r.text()).slice(0, 160)}`);
  const j = await r.json();
  if (!j.token || !j.ws_url) throw new Error("mint response missing token/ws_url");
  return j as MintResponse;
}

/* ---------------------------------------------------------------------------
 * 2. LiveSession — WS transport. Mirrors src/common/live/live-session.ts.
 * -------------------------------------------------------------------------*/
interface ServerContent {
  interrupted?: boolean;
  modelTurn?: { parts?: Part[] };
  model_turn?: { parts?: Part[] };
  inputTranscription?: { text?: string; finished?: boolean };
  input_transcription?: { text?: string; finished?: boolean };
  outputTranscription?: { text?: string };
  output_transcription?: { text?: string };
  turnComplete?: boolean;
  turn_complete?: boolean;
}
interface Part {
  inlineData?: { data?: string };
  inline_data?: { data?: string };
  text?: string;
  thought?: boolean;
}

export interface LiveHandlers {
  onReady?: () => void;
  onAudio?: (b64: string) => void;
  onInterrupted?: () => void;
  onInputText?: (t: string) => void;
  onUserSpeechDone?: () => void;
  onOutputText?: (t: string) => void;
  onTurnComplete?: () => void;
  onResumption?: (handle: string) => void;
  onError?: (e: string) => void;
  onClose?: (code: number, reason: string) => void;
}

export class LiveSession {
  private h: LiveHandlers;
  private ws: WebSocket | null = null;
  private pending: string[] = [];
  open = false;

  constructor(handlers: LiveHandlers) {
    this.h = handlers;
  }

  connect(wsUrl: string, setup: {
    model: string; voice?: string; systemInstruction?: string;
    vad?: Record<string, unknown>; activityHandling?: string;
    resumption?: Record<string, unknown>; transcription?: Record<string, unknown>;
  }) {
    this.ws = new WebSocket(wsUrl);
    this.ws.onopen = () => {
      this.open = true;
      this._send({
        setup: {
          model: setup.model.startsWith("models/") ? setup.model : `models/${setup.model}`,
          generation_config: {
            response_modalities: ["AUDIO"],
            speech_config: setup.voice
              ? { voice_config: { prebuilt_voice_config: { voice_name: setup.voice } } }
              : undefined,
          },
          system_instruction: setup.systemInstruction
            ? { parts: [{ text: setup.systemInstruction }] } : undefined,
          realtime_input_config: (setup.vad || setup.activityHandling)
            ? { automatic_activity_detection: setup.vad, activity_handling: setup.activityHandling }
            : undefined,
          input_audio_transcription: setup.transcription ?? {},
          output_audio_transcription: setup.transcription ?? {},
          session_resumption: setup.resumption
            ? (setup.resumption.handle ? { handle: setup.resumption.handle } : {})
            : undefined,
        },
      });
    };
    // Server frames arrive BINARY — decode or every message drops silently.
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (ev: MessageEvent) => {
      const d = ev.data;
      if (typeof d === "string") this._handle(d);
      else if (d instanceof Blob) d.text().then((t) => this._handle(t)).catch(() => {});
      else this._handle(new TextDecoder().decode(d));
    };
    this.ws.onerror = () => this.h.onError?.("live connection error");
    this.ws.onclose = (ev) => { this.open = false; this.h.onClose?.(ev.code, ev.reason); };
  }

  private _send(msg: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else this.pending.push(JSON.stringify(msg));
  }

  private _handle(raw: string) {
    let msg: Record<string, unknown>;
    try { msg = JSON.parse(raw); } catch { return; }
    try { this._dispatch(msg); } catch (e) { console.error("[live] bad frame:", e); }
  }

  private _dispatch(msg: Record<string, unknown>) {
    if (msg.setupComplete !== undefined) {
      this.h.onReady?.();
      for (const m of this.pending.splice(0)) this.ws?.send(m);
      return;
    }
    const ru = (msg.sessionResumptionUpdate ?? msg.session_resumption_update) as
      { newHandle?: string; new_handle?: string } | undefined;
    if (ru) this.h.onResumption?.(String(ru.newHandle ?? ru.new_handle ?? ""));
    const sc = (msg.serverContent ?? msg.server_content) as ServerContent | undefined;
    if (!sc) return;
    if (sc.interrupted) { this.h.onInterrupted?.(); return; }
    const parts = sc.modelTurn?.parts ?? sc.model_turn?.parts;
    if (Array.isArray(parts)) {
      for (const p of parts) {
        const inline = p.inlineData ?? p.inline_data;
        if (inline?.data) this.h.onAudio?.(inline.data);      // base64 PCM @24kHz
        if (p.text && !p.thought) this.h.onOutputText?.(p.text); // thoughts filtered
      }
    }
    const inTr = sc.inputTranscription ?? sc.input_transcription;
    if (inTr?.text) this.h.onInputText?.(inTr.text);
    if (inTr?.finished) this.h.onUserSpeechDone?.();
    const outTr = sc.outputTranscription ?? sc.output_transcription;
    if (outTr?.text) this.h.onOutputText?.(outTr.text);
    if (sc.turnComplete ?? sc.turn_complete) this.h.onTurnComplete?.();
  }

  /** Mic chunk: base64 16-bit LE PCM @16kHz mono. */
  sendAudio(b64: string) {
    this._send({ realtime_input: { media_chunks: [{ mime_type: "audio/pcm;rate=16000", data: b64 }] } });
  }
  /** Typed question joins the same conversation. */
  sendText(text: string) {
    this._send({ client_content: { turns: [{ role: "user", parts: [{ text }] }], turn_complete: true } });
  }
  close() { this.open = false; try { this.ws?.close(); } catch { /* noop */ } this.ws = null; }
}

/* ---------------------------------------------------------------------------
 * 3. capturePcm — mic → 16kHz mono PCM16 base64 chunks (~100ms).
 * -------------------------------------------------------------------------*/
export interface MicHandle { stop(): void }

export async function capturePcm(
  onChunk: (b64: string) => void,
  onActivity?: (rms: number) => void,   // ~256ms RMS blocks → local VAD
): Promise<MicHandle> {
  // echoCancellation is mandatory: the laptop speaker plays Infy's voice and
  // an unfiltered mic feeds it straight back to Gemini as "user speech" —
  // the model then answers its own words in a loop (repeated sentences)
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true,
             autoGainControl: true },
  });
  const ctx = new AudioContext({ sampleRate: 16000 }); // browser resamples
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  proc.onaudioprocess = (e) => {
    const f = e.inputBuffer.getChannelData(0);       // float32 [-1,1]
    const pcm = new Int16Array(f.length);
    let sum = 0;
    for (let i = 0; i < f.length; i++) {
      pcm[i] = Math.max(-1, Math.min(1, f[i])) * 0x7fff;
      sum += f[i] * f[i];
    }
    onActivity?.(Math.sqrt(sum / f.length));
    onChunk(b64encode(pcm.buffer));
  };
  src.connect(proc); proc.connect(ctx.destination);
  return {
    stop() {
      proc.disconnect(); src.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
    },
  };
}

/* ---------------------------------------------------------------------------
 * 4. PcmPlayer — 24kHz PCM chunks → scheduled AudioBuffers. interrupt() =
 *    barge-in: kill every queued source.
 * -------------------------------------------------------------------------*/
export class PcmPlayer {
  readonly ctx: AudioContext;
  private readonly gain: GainNode;
  private next = 0;
  private srcs: AudioBufferSourceNode[] = [];

  constructor(rate = 24000) {
    this.ctx = new AudioContext({ sampleRate: rate });
    // master gain → instant duck on speech onset (barge-in feel) without
    // killing the stream — ~20 ms fade, far below the ~300ms STT floor
    this.gain = this.ctx.createGain();
    this.gain.connect(this.ctx.destination);
  }
  push(b64: string) {
    const raw = b64decode(b64);
    const pcm = new Int16Array(raw.buffer, raw.byteOffset, raw.byteLength >> 1);
    const f = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 0x8000;
    const buf = this.ctx.createBuffer(1, f.length, this.ctx.sampleRate);
    buf.copyToChannel(f, 0);
    const s = this.ctx.createBufferSource();
    s.buffer = buf; s.connect(this.gain);
    const t = Math.max(this.ctx.currentTime, this.next);
    s.start(t); this.next = t + buf.duration;
    this.srcs.push(s);
    s.onended = () => this.srcs.splice(this.srcs.indexOf(s), 1);
  }
  /** drop the master gain — Infy keeps "speaking" but is (near-)silent */
  duck(level = 0.06) {
    this.gain.gain.setTargetAtTime(level, this.ctx.currentTime, 0.02);
  }
  unduck() {
    this.gain.gain.setTargetAtTime(1, this.ctx.currentTime, 0.03);
  }
  interrupt() { for (const s of this.srcs) { try { s.stop(); } catch { /* noop */ } } this.srcs = []; this.next = 0; this.unduck(); }
  /** seconds still scheduled — UI "speaking" indicator */
  queued() { return Math.max(0, this.next - this.ctx.currentTime); }
  close() { this.interrupt(); this.ctx.close(); }
}

/* base64 — browser atob/btoa on byte arrays (chunks are small). */
function b64encode(buf: ArrayBufferLike): string {
  const b = new Uint8Array(buf); let s = "";
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s);
}
function b64decode(b64: string): Uint8Array {
  const s = atob(b64); const b = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
  return b;
}
