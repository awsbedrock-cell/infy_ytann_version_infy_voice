/* ============================================================================
 * useInfy — the Infy voice session as a self-contained hook.
 *
 *   STT:      Web Speech API (free, streaming, en-IN / hi-IN) — interim +
 *             final transcripts stream to the backend over /ws/tutor.
 *   Commands: "infy start" / "infy stop" (English, Hinglish, Devanagari) are
 *             deterministic (commands.ts) — stop kills local audio instantly
 *             AND latches the server turn.
 *   Turns:    backend RAG → LLM stream → per-sentence TTS → mp3 → SentencePlayer
 *   Mic gate: while Infy is audible (+ tail) transcripts are dropped — Web
 *             Speech has no echo cancellation. Barge-in comes from a second,
 *             echo-cancelled mic stream (VAD), which cancels the turn for real.
 *   Reconnect: socket drops retry with backoff; the server resumes history
 *             by session id and doesn't greet again.
 * ==========================================================================*/

import { useCallback, useEffect, useRef, useState } from "react";
import { TutorSocket, SentencePlayer } from "../pipeline";
import { parseCommand, looseStop } from "./commands";
import {
  TranscriptFilter, BargeInDetector, srRestartDelay, reconnectDelay,
  countsAsSttFailure, PENDING_VOICE_MAX_MS,
} from "./voice-logic";

export type InfyState =
  | "idle" | "connecting" | "listening" | "thinking"
  | "speaking" | "stopped" | "ended" | "error";

export interface InfyMessage { kind: "you" | "bot"; text: string }
export type InfyLang = "en" | "hi";

export interface InfyOptions {
  chapterId?: number;
  lang?: InfyLang;
}

const STATUS: Record<InfyState, string> = {
  idle: "tap the mic to talk, or type a question",
  connecting: "connecting…",
  listening: "listening — speak",
  thinking: "thinking…",
  speaking: "Infy is speaking — talk over it to interrupt",
  stopped: "stopped — mic off, tap the mic to resume",
  ended: "session ended — tap to start again",
  error: "connection error — retrying…",
};

// voice "Infy stop" pauses answering but leaves the mic on for "Infy start"
const PAUSED = "paused — mic still on: say “Infy start” or tap the mic";

const SR_LANG: Record<InfyLang, string> = { en: "en-IN", hi: "hi-IN" };
// after Infy's audio ends, keep the mic gate closed this long: speaker
// reverb + recognizer lag still deliver Infy's last words as "speech"
const ECHO_TAIL_MS = 700;
// echo text check only runs this long after playback (then it's the user)
const ECHO_WINDOW_MS = 2500;

// e2e observability: timestamped event timeline at window.__events for the
// browser tests (transcript-sent -> first audio_chunk = first-audio latency)
function mark(ev: string, data?: string) {
  const w = window as unknown as { __events?: { t: number; ev: string; data?: string }[] };
  (w.__events ??= []).push({ t: performance.now(), ev, data });
}

function sessionId() {
  try {
    let id = sessionStorage.getItem("infy.session");
    if (!id) {
      id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      sessionStorage.setItem("infy.session", id);
    }
    return id;
  } catch { return `${Date.now()}`; }
}

export function useInfy({ chapterId = 1, lang = "en" }: InfyOptions) {
  const [state, setState] = useState<InfyState>("idle");
  const [status, setStatus] = useState(STATUS.idle);
  const [messages, setMessages] = useState<InfyMessage[]>([]);
  const [language, setLangState] = useState<InfyLang>(lang);
  const [sources, setSources] = useState<string[]>([]);
  const [live, setLive] = useState(false);

  const sockRef = useRef<TutorSocket | null>(null);
  const playerRef = useRef<SentencePlayer | null>(null);
  const srRef = useRef<SpeechRecognition | null>(null);
  const chapterRef = useRef(chapterId);
  const langRef = useRef(language);
  const activeRef = useRef(false);       // "started" latch, mirrors server
  const closingRef = useRef(false);      // true while we intentionally close
  const retryRef = useRef(0);
  const srFailRef = useRef(0);           // consecutive STT restart failures
  const pendingAskRef = useRef<string[]>([]);
  const filterRef = useRef(new TranscriptFilter());
  const pendingVoiceRef = useRef<{ text: string; ts: number } | null>(null);
  const stateRef = useRef<InfyState>("idle");
  const sessionRef = useRef(sessionId());
  const vadRef = useRef<{ ctx: AudioContext; stream: MediaStream; raf: number } | null>(null);
  chapterRef.current = chapterId;
  langRef.current = language;

  const set = useCallback((s: InfyState, extra?: string) => {
    stateRef.current = s;
    setState(s); setStatus(extra ?? STATUS[s]);
  }, []);

  const say = useCallback((kind: InfyMessage["kind"], text: string) =>
    setMessages((m) => [...m, { kind, text }]), []);

  /** true while Infy is audible or within the echo tail after it */
  const micGated = () => {
    const p = playerRef.current;
    if (stateRef.current === "speaking") return true;
    if (p?.speaking) return true;
    return !!p && Date.now() - p.lastAudible < ECHO_TAIL_MS;
  };

  /** cut Infy off locally right now (barge-in or stop) */
  const cutLocal = () => {
    sockRef.current?.dropCurrent();
    playerRef.current?.interrupt();
  };

  /* ---- socket ------------------------------------------------------------*/
  const startSTTRef = useRef<() => void>(() => {});

  const connect = useCallback(() => {
    const player = (playerRef.current ??= new SentencePlayer());
    const sock = new TutorSocket({
      onReady: (_cid, _title, resumed) => {
        retryRef.current = 0;
        mark("ready", resumed ? "resumed" : "new");
        sock.setLanguage(langRef.current);
        if (activeRef.current) sock.control("start");
        for (const q of pendingAskRef.current.splice(0)) sock.ask(q);
        // a question spoken while the socket was down — replay if still fresh
        const pv = pendingVoiceRef.current;
        pendingVoiceRef.current = null;
        if (pv && activeRef.current && Date.now() - pv.ts < PENDING_VOICE_MAX_MS)
          sock.transcript(pv.text, true);
        if (!activeRef.current && stateRef.current === "connecting") set("idle");
      },
      onState: (s, detail) => {
        mark("state", s);
        if (s === "idle" && activeRef.current) return;
        if (s === "stopped") set("stopped", activeRef.current ? PAUSED : STATUS.stopped);
        else if (s in STATUS) set(s as InfyState, detail || undefined);
        // resume listening if recognition silently died during the turn
        if (s === "listening" && activeRef.current && !srRef.current)
          startSTTRef.current();
      },
      onText: (role, text) => {
        mark("text", `${role}:${text.slice(0, 60)}`);
        if (role === "bot") filterRef.current.addBot(text, Date.now());
        say(role, text);
      },
      onSources: (s) => setSources(s.map((x) => x.source)),
      onInterrupted: () => { mark("interrupted"); player.interrupt(); },
      // server confirmed "infy hindi"/"infy english": mirror it in the UI and
      // restart recognition in the new language (onend picks up langRef)
      onLanguage: (l) => {
        if (l !== "en" && l !== "hi") return;
        mark("lang_cmd", l);
        setLangState(l);
        langRef.current = l;
        try { srRef.current?.stop(); } catch { /* noop */ }
      },
      onSpeak: (text, l, turn) => player.speakText(text, l, turn),
      onTurnEnd: (turn) => { mark("turn_end", String(turn)); player.turnEnded(turn); },
      onError: () => mark("socket_error"),
      onClose: () => {
        sockRef.current = null;
        player.interrupt();               // a dead socket's audio is stale
        if (closingRef.current || !activeRef.current) {
          if (stateRef.current !== "stopped") set("ended");
          return;
        }
        // unexpected drop — retry with backoff while the session is active
        const delay = reconnectDelay(retryRef.current++);
        mark("reconnect", String(delay));
        set("error", `connection lost — retrying in ${Math.round(delay / 1000)}s`);
        setTimeout(() => { if (activeRef.current && !sockRef.current) connect(); }, delay);
      },
    }, (mp3, turn) => { mark("audio_chunk", String(turn)); player.push(mp3, turn); });
    player.onTurnDone = (turn) => {
      mark("playback_done", String(turn));
      sock.playbackDone(turn);
    };
    sock.connect(chapterRef.current, sessionRef.current);
    sockRef.current = sock;
  }, [set, say]);

  /* ---- speech recognition --------------------------------------------------*/
  // Infy's own speaker output gets transcribed too; shortly after playback
  // the text echo check (voice-logic.ts) drops replays of what Infy said.
  const inEchoWindow = () => {
    const p = playerRef.current;
    return micGated() || (!!p && Date.now() - p.lastAudible < ECHO_WINDOW_MS);
  };

  const startSTT = useCallback(() => {
    const w = window as unknown as Record<string, typeof SpeechRecognition | undefined>;
    const SRImpl = w.SpeechRecognition ?? w.webkitSpeechRecognition;
    if (!SRImpl) { set("error", "speech recognition needs Chrome or Edge"); return; }
    if (srRef.current) return;
    const sr = new SRImpl();
    sr.lang = SR_LANG[langRef.current];
    sr.continuous = true;
    sr.interimResults = true;
    sr.onstart = () => { mark("stt_start", sr.lang); };
    sr.onresult = (e: SpeechRecognitionEvent) => {
      srFailRef.current = 0;
      // iterate every result — Chrome can batch a final with a new interim,
      // and reading only the last result silently drops the final (a lost
      // question that never reaches the backend)
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        const text = res[0].transcript.trim();
        if (!text) continue;
        // instant local stop — don't wait for the server round-trip.
        // Speaker echo can garble "infy stop" past the strict parse, so
        // while Infy is audible also accept the loose wake+stop pairing.
        const [cmd] = parseCommand(text);
        const stopNow = cmd === "stop" || (micGated() && looseStop(text));
        if (stopNow) {
          mark("stop_cmd", text);
          cutLocal();
          // explicit control beats forwarding the transcript: the server
          // latches stopped even if its own parse of the garbled text
          // would differ — and the utterance never becomes a "question"
          sockRef.current?.control("stop");
          continue;
        }
        // GATE: while Infy is audible (+ tail) the mic mostly hears Infy
        // itself. Only commands pass; real barge-in is the AEC mic VAD.
        const v = filterRef.current.check(text, res.isFinal, {
          isCommand: !!cmd, gated: micGated(),
          echoWindow: inEchoWindow(), now: Date.now(),
        });
        if (v !== "send") { if (res.isFinal) mark(v, text); continue; }
        if (res.isFinal) mark("transcript_final", text);
        if (sockRef.current?.open) sockRef.current.transcript(text, res.isFinal);
        else if (res.isFinal && !cmd)
          pendingVoiceRef.current = { text, ts: Date.now() };
      }
    };
    sr.onerror = (e: SpeechRecognitionErrorEvent) => {
      mark("stt_error", e.error);
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        srRef.current = null; sr.onend = null;
        set("error", "microphone blocked — allow mic access and tap the mic");
        activeRef.current = false; setLive(false);
        sockRef.current?.control("stop");
      } else if (e.error === "audio-capture") {
        set("error", "no microphone found");
      }
      // "no-speech" is just silence: restart immediately (backing off here
      // left multi-second deaf gaps). network/other errors back off.
      if (countsAsSttFailure(e.error)) srFailRef.current++;
    };
    sr.onend = () => {
      // Chrome ends recognition after silence, errors and every ~60s —
      // restart while active; back off only on real errors so a broken
      // mic/network can't spin
      if (!activeRef.current || srRef.current !== sr) return;
      const delay = srRestartDelay(srFailRef.current);
      setTimeout(() => {
        if (!activeRef.current || srRef.current !== sr) return;
        sr.lang = SR_LANG[langRef.current];
        try { sr.start(); } catch { /* already started */ }
      }, delay);
    };
    srRef.current = sr;
    try { sr.start(); } catch { /* noop */ }
  }, [set]);
  startSTTRef.current = startSTT;

  const stopSTT = useCallback(() => {
    const sr = srRef.current;
    srRef.current = null;
    if (sr) { sr.onend = null; try { sr.abort(); } catch { /* noop */ } }
  }, []);

  /* ---- barge-in VAD --------------------------------------------------------*/
  // Web Speech's own capture hears speaker echo; a second mic stream opened
  // with echoCancellation lets Chrome's DSP subtract Infy's output, so real
  // user energy during playback means a human actually talked over it.
  // The threshold adapts to the residual echo/noise floor measured while
  // Infy plays, so loud speakers don't self-interrupt and quiet rooms stay
  // sensitive.
  const startVAD = useCallback(async () => {
    if (vadRef.current || !navigator.mediaDevices?.getUserMedia) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true,
                 autoGainControl: true },
      });
      if (!activeRef.current) { stream.getTracks().forEach(t => t.stop()); return; }
      const ctx = new AudioContext();
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      const det = new BargeInDetector();
      const tick = () => {
        if (!vadRef.current) return;
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const d = (buf[i] - 128) / 128;
          sum += d * d;
        }
        const rms = Math.sqrt(sum / buf.length);
        if (det.tick(rms, !!playerRef.current?.speaking, performance.now())) {
          mark("vad_interrupt", rms.toFixed(3));
          cutLocal();
          sockRef.current?.control("interrupt");
        }
        vadRef.current!.raf = requestAnimationFrame(tick);
      };
      vadRef.current = { ctx, stream, raf: requestAnimationFrame(tick) };
    } catch { /* mic unavailable — barge-in degraded, commands still work */ }
  }, []);

  const stopVAD = useCallback(() => {
    const v = vadRef.current;
    vadRef.current = null;
    if (!v) return;
    cancelAnimationFrame(v.raf);
    v.stream.getTracks().forEach(t => t.stop());
    void v.ctx.close();
  }, []);

  /* ---- public API -----------------------------------------------------------*/
  const start = useCallback(() => {
    if (activeRef.current) return;
    mark("start_cmd");
    activeRef.current = true; setLive(true);
    closingRef.current = false;
    retryRef.current = 0; srFailRef.current = 0;
    (playerRef.current ??= new SentencePlayer()).resume();
    set("connecting");
    startSTT();
    void startVAD();
    if (sockRef.current?.open) sockRef.current.control("start");
    else if (!sockRef.current) connect();
  }, [connect, set, startSTT, startVAD]);

  const stop = useCallback(() => {
    mark("stop_cmd", "button");
    activeRef.current = false; setLive(false);
    cutLocal();
    stopSTT();
    stopVAD();
    sockRef.current?.control("stop");
    set("stopped");
  }, [set, stopSTT, stopVAD]);

  // mic button: after a voice "Infy stop" the mic is still on, so a tap
  // resumes instead of tearing the session down
  const toggle = useCallback(() => {
    if (!activeRef.current) start();
    else if (stateRef.current === "stopped") {
      set("connecting");
      sockRef.current?.control("start");
    } else stop();
  }, [set, start, stop]);

  /** typed questions work with or without the mic session */
  const sendText = useCallback((text: string) => {
    const t = text.trim();
    if (!t) return false;
    (playerRef.current ??= new SentencePlayer()).resume();
    cutLocal();
    if (sockRef.current?.open) sockRef.current.ask(t);
    else {
      pendingAskRef.current.push(t);
      if (!sockRef.current) { closingRef.current = false; connect(); }
    }
    return true;
  }, [connect]);

  const setLanguage = useCallback((l: InfyLang) => {
    setLangState(l);
    langRef.current = l;
    sockRef.current?.setLanguage(l);
    // restart recognition so the new lang takes effect
    if (srRef.current) {
      const sr = srRef.current;
      try { sr.stop(); } catch { /* noop */ } // onend restarts with new lang
    }
  }, []);

  const setChapter = useCallback((id: number) => {
    chapterRef.current = id;
    sockRef.current?.setChapter(id);
  }, []);

  /* teardown on unmount */
  useEffect(() => () => {
    closingRef.current = true;
    activeRef.current = false;
    stopSTT();
    stopVAD();
    playerRef.current?.close(); playerRef.current = null;
    sockRef.current?.close(); sockRef.current = null;
  }, [stopSTT, stopVAD]);

  return {
    state, status, messages, sources, language, live,
    start, stop, toggle, sendText, setLanguage, setChapter,
  };
}
