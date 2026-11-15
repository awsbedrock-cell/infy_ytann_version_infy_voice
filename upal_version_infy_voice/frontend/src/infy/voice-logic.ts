/* ============================================================================
 * voice-logic.ts — pure (DOM-free) decision logic of the voice loop, split
 * out of useInfy so it can be driven by simulated mic/audio events in
 * `node --test frontend/tests/` without a browser or a microphone.
 * ==========================================================================*/

export function norm(text: string) {
  return (text.toLowerCase().match(/[a-z0-9\u0900-\u097f]+/g) || []).join(" ");
}

/** Chrome can re-fire the same final; one utterance = one turn */
export const DUP_FINAL_MS = 3000;
/** bot text older than this can't be echo any more */
const BOT_MEMORY_MS = 60000;

/** utterance replays what Infy just said: a contiguous 4-word span, the
 *  whole phrase verbatim, or (5+ words) 85% covered by bot words */
export function isEchoText(text: string, botTexts: string[]): boolean {
  if (!botTexts.length) return false;
  const words = norm(text).split(" ").filter(Boolean);
  if (words.length < 2) return false;
  const blob = " " + botTexts.join(" ") + " ";
  for (let i = 0; i + 4 <= words.length; i++)
    if (blob.includes(" " + words.slice(i, i + 4).join(" ") + " ")) return true;
  if (blob.includes(" " + words.join(" ") + " ")) return true;
  if (words.length >= 5) {
    const bot = new Set(blob.trim().split(" "));
    if (words.filter((w) => bot.has(w)).length / words.length >= 0.85) return true;
  }
  return false;
}

export type Verdict = "send" | "gated" | "dup" | "echo";

export interface TranscriptCtx {
  isCommand: boolean;   // "infy stop/start" always passes the gate
  gated: boolean;       // Infy audible (+ tail): mic mostly hears Infy
  echoWindow: boolean;  // shortly after playback: run the text echo check
  now: number;
}

/** Decides what happens to each recognizer result. */
export class TranscriptFilter {
  private lastFinal: { t: string; ts: number } | null = null;
  private bot: { s: string; ts: number }[] = [];

  addBot(text: string, now: number) {
    this.bot.push({ s: norm(text), ts: now });
  }

  check(text: string, isFinal: boolean, ctx: TranscriptCtx): Verdict {
    if (!ctx.isCommand && ctx.gated) return "gated";
    if (!isFinal) return "send";
    const n = norm(text);
    const lf = this.lastFinal;
    if (lf && lf.t === n && ctx.now - lf.ts < DUP_FINAL_MS) return "dup";
    if (!ctx.isCommand && ctx.echoWindow) {
      this.bot = this.bot.filter((b) => ctx.now - b.ts < BOT_MEMORY_MS);
      if (isEchoText(text, this.bot.map((b) => b.s))) return "echo";
    }
    this.lastFinal = { t: n, ts: ctx.now };
    return "send";
  }
}

/** recognizer restart after onend: immediate after plain silence
 *  ("no-speech"/"aborted" don't count as failures), backoff on real errors */
export function srRestartDelay(failures: number) {
  return failures ? Math.min(5000, 250 * 2 ** failures) : 0;
}

export function countsAsSttFailure(error: string) {
  return error !== "no-speech" && error !== "aborted";
}

export function reconnectDelay(attempt: number) {
  return Math.min(8000, 500 * 2 ** attempt);
}

/** a voice final captured while the socket was down is replayed after
 *  reconnect only if still fresh — older ones are stale transcripts */
export const PENDING_VOICE_MAX_MS = 10000;

/** Barge-in on the echo-cancelled mic stream. The threshold adapts to the
 *  residual echo/noise floor learned while Infy plays, so loud speakers
 *  don't self-interrupt and quiet rooms stay sensitive. Fires on sustained
 *  speech, not during AEC convergence, and not during cooldown. */
export class BargeInDetector {
  floor = 0.02;
  minThr = 0.06;
  floorMul = 3;
  sustainMs = 250;
  convergeMs = 300;
  cooldownMs = 1500;
  private hotSince = 0;
  private coolUntil = 0;
  private playStart = 0;

  tick(rms: number, playing: boolean, now: number): boolean {
    if (!playing) { this.hotSince = 0; this.playStart = 0; return false; }
    this.playStart ||= now;
    const thr = Math.max(this.minThr, this.floor * this.floorMul);
    if (rms > thr) this.hotSince ||= now;
    else {
      this.hotSince = 0;
      this.floor = this.floor * 0.98 + rms * 0.02;
    }
    if (this.hotSince && now - this.hotSince >= this.sustainMs
        && now - this.playStart > this.convergeMs && now > this.coolUntil) {
      this.hotSince = 0;
      this.coolUntil = now + this.cooldownMs;
      return true;
    }
    return false;
  }
}
