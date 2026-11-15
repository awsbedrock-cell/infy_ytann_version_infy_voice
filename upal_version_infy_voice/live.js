/* ============================================================================
 * live.js — REAL socket voice pipeline (mini replica of the production Infy
 * stack: be/app.py /api/tutor/live  →  src/common/live/{live-session,pcm})
 *
 * Flow (identical to prod):
 *   1. POST {API}/api/tutor/live  → ephemeral token + ws_url (key stays on
 *      the backend — this file NEVER holds a Gemini key)
 *   2. WS → Google BidiGenerateContentConstrained?access_token=<token>
 *   3. Mic → 16 kHz mono PCM16 → base64 realtime_input.media_chunks
 *   4. Server → binary frames: serverContent.modelTurn.parts[].inlineData
 *      = 24 kHz PCM → queued into AudioContext (interrupt() = barge-in)
 *
 * EXPERIMENT knobs marked >>> EXPERIMENT.
 * ==========================================================================*/

/* >>> EXPERIMENT: where the mint endpoint lives ----------------------------
 * this folder's own backend.py on :8200 — or any compatible mint.         */
const LIVE_API = "http://localhost:8200";
// const LIVE_API = "http://localhost:8100";            // real varad backend
// const LIVE_API = "https://varad-core.onrender.com";  // production

/* >>> EXPERIMENT: setup knobs (override what the mint returns) ------------- */
const SETUP = {
  vad: {
    start_of_speech_sensitivity: "START_SENSITIVITY_HIGH",
    end_of_speech_sensitivity: "END_SENSITIVITY_HIGH",
    prefix_padding_ms: 120,
    silence_duration_ms: 300,
  },
  // activityHandling: "NO_INTERRUPTION",   // uncomment to disable barge-in
  resumption: {},                            // session_resumption: {}
  transcription: {},                         // input+output transcription
};

/* --------------------------------------------------------------------------
 * 1. Mint — same request the real client makes.
 * ------------------------------------------------------------------------*/
async function mintTutor(apiBase, chapterId) {
  const r = await fetch(`${apiBase}/api/tutor/live`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(chapterId ? { chapter_id: chapterId } : {}),
  });
  if (!r.ok) throw new Error(`mint ${r.status}: ${(await r.text()).slice(0, 160)}`);
  const j = await r.json();
  if (!j.token || !j.ws_url) throw new Error("mint response missing token/ws_url");
  return j; // {token, ws_url, model, voice, system_instruction, greeting, vad, ...}
}

/* --------------------------------------------------------------------------
 * 2. LiveSession — the WS transport. Mirrors src/common/live/live-session.ts:
 *    binary frame decode, pending-flush after setupComplete, event dispatch.
 * ------------------------------------------------------------------------*/
class LiveSession {
  constructor(handlers) { this.h = handlers || {}; this.ws = null; this.open = false; this.pending = []; }

  connect(wsUrl, setup) {
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
            ? { automatic_activity_detection: setup.vad,
                activity_handling: setup.activityHandling } : undefined,
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
    this.ws.onmessage = (ev) => {
      const d = ev.data;
      if (typeof d === "string") this._handle(d);
      else if (d instanceof Blob) d.text().then((t) => this._handle(t)).catch(() => {});
      else this._handle(new TextDecoder().decode(d));
    };
    this.ws.onerror = () => this.h.onError?.("live connection error");
    this.ws.onclose = (ev) => { this.open = false; this.h.onClose?.(ev.code, ev.reason); };
  }

  _send(msg) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else this.pending.push(JSON.stringify(msg));
  }

  _handle(raw) {
    let msg; try { msg = JSON.parse(raw); } catch { return; }
    try { this._dispatch(msg); } catch (e) { console.error("[live] bad frame:", e); }
  }

  _dispatch(msg) {
    if (msg.setupComplete !== undefined) {
      this.h.onReady?.();
      for (const m of this.pending.splice(0)) this.ws?.send(m);
      return;
    }
    const ru = msg.sessionResumptionUpdate ?? msg.session_resumption_update;
    if (ru) this.h.onResumption?.(String(ru.newHandle ?? ru.new_handle ?? ""));
    const sc = msg.serverContent ?? msg.server_content;
    if (!sc) return;
    if (sc.interrupted) { this.h.onInterrupted?.(); return; }
    const parts = sc.modelTurn?.parts ?? sc.model_turn?.parts;
    if (Array.isArray(parts)) {
      for (const p of parts) {
        const inline = p.inlineData ?? p.inline_data;
        if (inline?.data) this.h.onAudio?.(inline.data);   // base64 PCM @24kHz
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
  sendAudio(b64) { this._send({ realtime_input: { media_chunks: [{ mime_type: "audio/pcm;rate=16000", data: b64 }] } }); }
  /** Typed question joins the same conversation. */
  sendText(text) { this._send({ client_content: { turns: [{ role: "user", parts: [{ text }] }], turn_complete: true } }); }
  close() { this.open = false; try { this.ws?.close(); } catch {} this.ws = null; }
}

/* --------------------------------------------------------------------------
 * 3. capturePcm — mic → 16kHz mono PCM16 base64 chunks (~100ms).
 *    (Prod uses AudioWorklet+resample; ScriptProcessor keeps this readable.)
 * ------------------------------------------------------------------------*/
async function capturePcm(onChunk) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const ctx = new AudioContext({ sampleRate: 16000 }); // browser resamples
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  proc.onaudioprocess = (e) => {
    const f = e.inputBuffer.getChannelData(0);        // float32 [-1,1]
    const pcm = new Int16Array(f.length);
    for (let i = 0; i < f.length; i++) pcm[i] = Math.max(-1, Math.min(1, f[i])) * 0x7fff;
    onChunk(b64encode(pcm.buffer));
  };
  src.connect(proc); proc.connect(ctx.destination);
  return {
    stop() { proc.disconnect(); src.disconnect(); stream.getTracks().forEach(t => t.stop()); ctx.close(); },
  };
}

/* --------------------------------------------------------------------------
 * 4. PcmPlayer — 24kHz PCM chunks → scheduled AudioBuffers. interrupt() =
 *    barge-in: kill every queued source, reset scheduling (server sends
 *    serverContent.interrupted when the user talks over the model).
 * ------------------------------------------------------------------------*/
class PcmPlayer {
  constructor(rate = 24000) { this.ctx = new AudioContext({ sampleRate: rate }); this.next = 0; this.srcs = []; }
  push(b64) {
    const raw = b64decode(b64);
    const pcm = new Int16Array(raw.buffer, raw.byteOffset, raw.byteLength >> 1);
    const f = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 0x8000;
    const buf = this.ctx.createBuffer(1, f.length, this.ctx.sampleRate);
    buf.copyToChannel(f, 0);
    const s = this.ctx.createBufferSource();
    s.buffer = buf; s.connect(this.ctx.destination);
    const t = Math.max(this.ctx.currentTime, this.next);
    s.start(t); this.next = t + buf.duration;
    this.srcs.push(s); s.onended = () => this.srcs.splice(this.srcs.indexOf(s), 1);
  }
  interrupt() { for (const s of this.srcs) { try { s.stop(); } catch {} } this.srcs = []; this.next = 0; }
  /** seconds still scheduled — UI "speaking" indicator */
  queued() { return Math.max(0, this.next - this.ctx.currentTime); }
  close() { this.interrupt(); this.ctx.close(); }
}

/* base64 — browser atob/btoa on byte arrays (chunks are small). */
function b64encode(buf) {
  const b = new Uint8Array(buf); let s = "";
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s);
}
function b64decode(b64) {
  const s = atob(b64); const b = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
  return b;
}

window.LiveKit = { mintTutor, LiveSession, capturePcm, PcmPlayer, LIVE_API, SETUP };
