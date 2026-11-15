/* ============================================================================
 * pipeline.ts — transport + audio for the provider-agnostic tutor pipeline.
 *
 *   browser ──WS──▶ :8200/ws/tutor
 *     out: hello(session_id), transcripts, asks, controls, language,
 *          playback_done(turn)
 *     in : state / text / sources / audio_start→mp3 chunks→audio_end / speak
 *          / turn_end — every message carries the server turn id
 *
 * Server sends one logical mp3 per sentence: {"type":"audio_start"} then raw
 * binary fragments then {"type":"audio_end"}. Anything belonging to a turn
 * at or below `dropTurn` (interrupted / stopped) is discarded — that is what
 * keeps a cancelled answer's in-flight audio from playing after barge-in.
 * ==========================================================================*/

export interface PipelineHandlers {
  onReady?: (chapterId: number, title: string, resumed: boolean) => void;
  onState?: (state: string, detail: string, turn: number) => void;
  onText?: (role: "you" | "bot", text: string, turn: number) => void;
  onSources?: (sources: { source: string; kind: string }[]) => void;
  onInterrupted?: (turn: number) => void;
  onLanguage?: (lang: string) => void;
  /** browser-TTS fallback: speak this text client-side */
  onSpeak?: (text: string, lang: string, turn: number) => void;
  onTurnEnd?: (turn: number) => void;
  onClose?: (code: number, reason: string) => void;
  onError?: (e: string) => void;
}

export class TutorSocket {
  private h: PipelineHandlers;
  private ws: WebSocket | null = null;
  private audioQueue: Uint8Array[] = [];
  private collecting = false;
  private collectTurn = 0;
  private onAudioSentence?: (mp3: Uint8Array, turn: number) => void;
  /** highest turn id seen from the server */
  turn = 0;
  /** turns <= dropTurn are dead: their audio/text is ignored */
  dropTurn = 0;

  constructor(handlers: PipelineHandlers,
              onAudioSentence: (mp3: Uint8Array, turn: number) => void) {
    this.h = handlers;
    this.onAudioSentence = onAudioSentence;
  }

  connect(chapterId: number, sessionId: string) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/tutor`);
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () =>
      this.send({ type: "hello", chapter_id: chapterId, session_id: sessionId });
    this.ws.onmessage = (ev) => {
      if (typeof ev.data === "string") this._json(ev.data);
      else this._bytes(new Uint8Array(ev.data as ArrayBuffer));
    };
    this.ws.onerror = () => this.h.onError?.("tutor socket error");
    this.ws.onclose = (ev) => this.h.onClose?.(ev.code, ev.reason);
  }

  get open() { return this.ws?.readyState === WebSocket.OPEN; }

  send(msg: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN)
      this.ws.send(JSON.stringify(msg));
  }

  transcript(text: string, final: boolean) {
    this.send({ type: "transcript", text, final, ts: Date.now() });
  }
  ask(text: string) { this.send({ type: "ask", text }); }
  control(cmd: "start" | "stop" | "interrupt") {
    this.send({ type: "control", cmd, turn: this.turn });
  }
  /** cut the current turn locally right now (before the server confirms) */
  dropCurrent() {
    this.dropTurn = Math.max(this.dropTurn, this.turn);
    this.audioQueue = []; this.collecting = false;
  }
  playbackDone(turn: number) { this.send({ type: "playback_done", turn }); }
  setLanguage(lang: "en" | "hi") { this.send({ type: "language", lang }); }
  setChapter(id: number) { this.send({ type: "chapter", chapter_id: id }); }

  close() {
    if (this.ws) { this.ws.onclose = null; this.ws.onmessage = null; }
    try { this.ws?.close(); } catch { /* noop */ }
    this.ws = null;
  }

  private _json(raw: string) {
    let msg: Record<string, unknown>;
    try { msg = JSON.parse(raw); } catch { return; }
    const turn = typeof msg.turn === "number" ? msg.turn : this.turn;
    if (turn > this.turn) this.turn = turn;
    const dead = turn <= this.dropTurn;
    switch (msg.type) {
      case "ready":
        this.h.onReady?.(msg.chapter_id as number, msg.title as string,
                         Boolean(msg.resumed)); break;
      case "state":
        // a dead turn may still report listening/stopped/error, but never
        // "speaking"/"thinking" — that would re-close the mic for nothing
        if (dead && (msg.state === "speaking" || msg.state === "thinking")) break;
        this.h.onState?.(msg.state as string, (msg.detail as string) || "", turn); break;
      case "text":
        if (dead && msg.role === "bot") break;
        this.h.onText?.(msg.role as "you" | "bot", msg.text as string, turn); break;
      case "sources":
        if (!dead) this.h.onSources?.(msg.sources as { source: string; kind: string }[]);
        break;
      case "language":
        this.h.onLanguage?.(String(msg.lang)); break;
      case "interrupted":
        this.dropTurn = Math.max(this.dropTurn, turn);
        this.audioQueue = []; this.collecting = false;
        this.h.onInterrupted?.(turn); break;
      case "speak":
        if (!dead) this.h.onSpeak?.(msg.text as string, msg.lang as string, turn);
        break;
      case "turn_end":
        if (!dead) this.h.onTurnEnd?.(turn); break;
      case "audio_start":
        this.audioQueue = []; this.collecting = !dead; this.collectTurn = turn; break;
      case "audio_end":
        if (this.collecting && this.collectTurn > this.dropTurn) {
          const total = this.audioQueue.reduce((n, c) => n + c.length, 0);
          const mp3 = new Uint8Array(total);
          let off = 0;
          for (const c of this.audioQueue) { mp3.set(c, off); off += c.length; }
          this.onAudioSentence?.(mp3, this.collectTurn);
        }
        this.audioQueue = []; this.collecting = false; break;
    }
  }

  private _bytes(b: Uint8Array) { if (this.collecting) this.audioQueue.push(b); }
}

/* ---------------------------------------------------------------------------
 * SentencePlayer — decode each sentence's complete mp3 and schedule it back
 * to back on one AudioContext, strictly in arrival order (decodes run in
 * parallel, scheduling is chained). interrupt() bumps a generation counter:
 * any decode that finishes afterwards is discarded instead of playing.
 * `onTurnDone(turn)` fires once the server said turn_end AND every buffer of
 * that turn has finished playing — that is when the mic may reopen.
 * -------------------------------------------------------------------------*/
export class SentencePlayer {
  private ctx: AudioContext;
  private next = 0;
  private srcs = new Set<AudioBufferSourceNode>();
  private gen = 0;
  private chain: Promise<void> = Promise.resolve();
  private pending = 0;          // pushed but not yet finished playing
  private endedTurn = -1;       // server sent turn_end for this turn
  private playTurn = -1;
  private utterances = 0;
  /** WebAudio device failure — route playback through <audio> instead */
  private webAudioDead = false;
  private els = new Set<HTMLAudioElement>();
  onTurnDone?: (turn: number) => void;
  /** ms timestamp of the last moment audio was audible */
  lastAudible = 0;

  constructor() {
    this.ctx = new AudioContext();
    // device-level WebAudio failure (seen as "AudioContext encountered an
    // error from the audio device"): decode/schedule can't recover — fall
    // back to HTMLAudioElement playback, which uses the system mixer.
    (this.ctx as AudioContext & { onerror: () => void }).onerror =
      () => { this.webAudioDead = true; };
  }

  get speaking() { return this.pending > 0 || this.utterances > 0; }

  /** browsers keep a fresh AudioContext suspended until a user gesture */
  resume() { if (this.ctx.state === "suspended") void this.ctx.resume(); }

  push(mp3: Uint8Array, turn: number) {
    const gen = this.gen;
    this.playTurn = turn;
    this.pending++;
    if (this.webAudioDead || this.ctx.state === "closed") {
      this._playElement(mp3, gen);
      return;
    }
    const decoded = this.ctx.decodeAudioData(
      mp3.buffer.slice(mp3.byteOffset, mp3.byteOffset + mp3.byteLength) as ArrayBuffer)
      .catch((e) => {
        console.error("[tts] decode failed", e);
        this.webAudioDead = true;
        return null;
      });
    this.chain = this.chain.then(async () => {
      const buf = await decoded;
      if (gen !== this.gen) return;          // interrupted meanwhile
      if (!buf) { this._playElement(mp3, gen); return; }
      const s = this.ctx.createBufferSource();
      s.buffer = buf; s.connect(this.ctx.destination);
      const t = Math.max(this.ctx.currentTime + 0.02, this.next);
      s.start(t); this.next = t + buf.duration;
      this.srcs.add(s);
      s.onended = () => { this.srcs.delete(s); this._finish(gen); };
    });
  }

  /** plain <audio> element playback — survives a dead WebAudio device */
  private _playElement(mp3: Uint8Array, gen: number) {
    const url = URL.createObjectURL(
      new Blob([mp3 as BlobPart], { type: "audio/mpeg" }));
    const a = new Audio(url);
    this.els.add(a);
    const done = () => {
      this.els.delete(a);
      URL.revokeObjectURL(url);
      this._finish(gen);
    };
    a.onended = done;
    a.onerror = done;
    void a.play().catch(done);
  }

  /** browser speechSynthesis fallback, tracked like an audio buffer */
  speakText(text: string, lang: string, turn: number) {
    const gen = this.gen;
    this.playTurn = turn;
    this.utterances++;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "hi" ? "hi-IN" : "en-IN";
    const done = () => {
      if (gen !== this.gen) return;
      this.utterances = Math.max(0, this.utterances - 1);
      this.lastAudible = Date.now();
      this._maybeDone();
    };
    u.onend = done; u.onerror = done;
    speechSynthesis.speak(u);
  }

  turnEnded(turn: number) {
    this.endedTurn = turn;
    if (this.playTurn !== turn) this.playTurn = turn;
    this._maybeDone();
  }

  private _finish(gen: number) {
    if (gen !== this.gen) return;
    this.pending = Math.max(0, this.pending - 1);
    this.lastAudible = Date.now();
    this._maybeDone();
  }

  private _maybeDone() {
    if (!this.speaking && this.endedTurn === this.playTurn && this.endedTurn >= 0) {
      const t = this.endedTurn;
      this.endedTurn = -1;
      this.onTurnDone?.(t);
    }
  }

  interrupt() {
    this.gen++;
    const wasAudible = this.speaking;
    for (const s of this.srcs) { try { s.stop(); } catch { /* noop */ } }
    this.srcs.clear();
    for (const a of this.els) { try { a.pause(); } catch { /* noop */ } }
    this.els.clear();
    this.chain = Promise.resolve();
    this.next = 0; this.pending = 0; this.utterances = 0; this.endedTurn = -1;
    if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
    if (wasAudible) this.lastAudible = Date.now();
  }
  close() { this.interrupt(); void this.ctx.close(); }
}
