/* ============================================================================
 * useInfyLive — the Infy voice session over Gemini Live (speech-to-speech).
 *
 * Same public interface as useInfy so <Infy/> swaps transparently:
 *   mintTutor (ephemeral token, key never in the bundle)
 *     -> LiveSession WS -> capturePcm 16kHz in / PcmPlayer 24kHz out.
 *
 * Turn-taking: the server's native VAD handles barge-in (onInterrupted kills
 * the play queue). Commands: input transcription is parsed with the SAME
 * commands.ts, so "infy stop"/"infy start"/"बोलो" work like the cascade —
 * pause keeps mic audio flowing (so "infy start" is still heard) but drops
 * all output audio until resume; the system prompt also tells the model to
 * stay silent while paused as a second layer.
 * Grounding: the mint bakes the chapter into the system instruction, so
 * answers stay chapter-grounded without tool calls.
 * Reconnect: unexpected close re-mints and re-uses the server resumption
 * handle; history is preserved.
 * ==========================================================================*/

import { useCallback, useEffect, useRef, useState } from "react";
import { LiveSession, PcmPlayer, capturePcm, mintTutor, LIVE_API } from "../live";
import { parseCommand, looseStop } from "./commands";
import type { InfyLang, InfyOptions, InfyState, InfyMessage } from "./use-infy";

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

const PAUSED = "paused — say “Infy start” or tap the mic";

const SR_LANG: Record<InfyLang, string> = { en: "en-IN", hi: "hi-IN" };

// Devanagari = the model/user actually spoke Hindi — the drift-detection
// and language-lock logic below keys on this.
const DEVA = /[ऀ-ॿ]/;
// in-command transcription buffer cap — keeps a never-`finished` utterance
// from poisoning the next one's command parse
const IN_TEXT_MAX = 160;

function mark(ev: string, data?: string) {
  const w = window as unknown as { __events?: { t: number; ev: string; data?: string }[] };
  (w.__events ??= []).push({ t: performance.now(), ev, data });
}

export function useInfyLive({ chapterId = 1, lang = "en" }: InfyOptions) {
  const [state, setState] = useState<InfyState>("idle");
  const [status, setStatus] = useState(STATUS.idle);
  const [messages, setMessages] = useState<InfyMessage[]>([]);
  const [language, setLangState] = useState<InfyLang>(lang);
  const [live, setLive] = useState(false);
  const [failed, setFailed] = useState(false);   // parent: fall back to cascade

  const sessRef = useRef<LiveSession | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  const micRef = useRef<{ stop(): void } | null>(null);
  const micOnRef = useRef(false);
  const activeRef = useRef(false);
  const closingRef = useRef(false);
  const pausedRef = useRef(false);
  const resumeHandleRef = useRef("");
  const chapterRef = useRef(chapterId);
  const langRef = useRef(language);
  const stateRef = useRef<InfyState>("idle");
  const retryRef = useRef(0);
  const limitTimerRef = useRef<number | undefined>(undefined);
  const outTextRef = useRef("");
  const inTextRef = useRef("");
  const cmdHandledRef = useRef("");      // utterance text already consumed as a command
  const userLangRef = useRef<"en" | "hi">("en"); // script of the student's last utterance
  const cmdSrRef = useRef<SpeechRecognition | null>(null); // command-only recognizer
  const streakRef = useRef(0);                        // consecutive loud mic blocks
  const duckedRef = useRef(false);                    // output gain dropped by VAD
  const duckTimerRef = useRef<number | undefined>(undefined);

  // spoken "infy hindi"/"infy english": Gemini heard it too and switches by
  // its system rule — only mirror it locally (UI + command-ear language)
  const voiceLang = useCallback((l: InfyLang) => {
    if (langRef.current === l) return;
    setLangState(l); langRef.current = l;
    try { cmdSrRef.current?.stop(); } catch { /* onend restarts in l */ }
  }, []);

  const set = useCallback((s: InfyState, extra?: string) => {
    stateRef.current = s;
    setState(s);
    setStatus(extra ?? STATUS[s]);
    mark("state", s);
  }, []);

  const addMsg = useCallback((kind: "you" | "bot", text: string) => {
    setMessages((m) => [...m, { kind, text }]);
  }, []);

  const cutAudio = useCallback(() => {
    window.clearTimeout(duckTimerRef.current);
    duckedRef.current = false; streakRef.current = 0;
    playerRef.current?.interrupt();
  }, []);

  /* ---- session bring-up ----------------------------------------------------*/
  const connect = useCallback(async () => {
    if (!activeRef.current) return;
    set("connecting");
    // never stack sessions: a previous connect still in-flight (or a
    // session kept for reuse) is closed before minting another — the
    // orphaned one's onReady no-ops because it isn't sessRef any more
    const stale = sessRef.current;
    sessRef.current = null;
    stale?.close();
    mark("live_mint");
    let mint;
    try {
      mint = await mintTutor(LIVE_API, chapterRef.current, langRef.current);
    } catch (e) {
      mark("live_mint_fail", String(e).slice(0, 80));
      // no key / quota dead — tell the parent to swap to the local pipeline
      setFailed(true); set("error");
      return;
    }

    const sess = new LiveSession({
      onReady: () => {
        mark("live_ready");
        retryRef.current = 0;
        // dead session (stopped or superseded by a newer connect) — don't
        // activate it, or two live sessions end up with mics open
        if (!activeRef.current || sessRef.current !== sess) {
          sess.close();
          return;
        }
        pausedRef.current = false;
        set("listening");
        void startMic(sess);
        // one greeting, only on a fresh session (no resumption handle)
        if (!resumeHandleRef.current && mint.greeting) sess.sendText(mint.greeting);
        // spending guard mirrors the token's own expiry
        window.clearTimeout(limitTimerRef.current);
        limitTimerRef.current = window.setTimeout(() => {
          mark("live_limit");
          end();
          set("ended", "session limit reached — tap the mic to start a new one");
        }, (mint.max_session_minutes ?? 10) * 60 * 1000);
      },
      onAudio: (b64) => {
        // paused or fully stopped (session kept open for a fast restart):
        // the model may still emit audio — drop every chunk, play none
        if (pausedRef.current || !activeRef.current) return;
        playerRef.current?.push(b64);
        if (stateRef.current !== "speaking") set("speaking");
      },
      onInterrupted: () => {
        mark("live_interrupted");
        cutAudio();
        if (!pausedRef.current && activeRef.current) set("listening");
      },
      onInputText: (t) => {
        inTextRef.current = (inTextRef.current + t).slice(-IN_TEXT_MAX);
        // parse the WHOLE utterance so far — "infy" and "stop" can land in
        // separate transcription deltas and neither alone parses as a command
        const cmd = inTextRef.current === cmdHandledRef.current
          ? null : parseCommand(inTextRef.current)[0];
        if (cmd === "stop" && !pausedRef.current) {
          mark("stop_cmd", inTextRef.current.slice(-60));
          cmdHandledRef.current = inTextRef.current;
          pausedRef.current = true;
          cutAudio();
          set("stopped", PAUSED);
        } else if (cmd === "start" && pausedRef.current) {
          mark("start_cmd", inTextRef.current.slice(-60));
          cmdHandledRef.current = inTextRef.current;
          pausedRef.current = false;
          set("listening");
        } else if (cmd === "hindi" || cmd === "english") {
          mark("lang_cmd", cmd);
          cmdHandledRef.current = inTextRef.current;
          voiceLang(cmd === "hindi" ? "hi" : "en");
        }
      },
      onUserSpeechDone: () => {
        const t = inTextRef.current.trim();
        inTextRef.current = "";
        cmdHandledRef.current = "";
        // the script the student actually used this utterance — drives the
        // drift check below (a Hindi utterance means Hindi output is wanted)
        if (t) userLangRef.current = DEVA.test(t) ? "hi" : "en";
        if (t) { addMsg("you", t); if (!pausedRef.current) set("thinking"); }
      },
      onOutputText: (t) => { outTextRef.current += t; },
      onTurnComplete: () => {
        const t = outTextRef.current.trim();
        outTextRef.current = "";
        if (t && !pausedRef.current) {
          addMsg("bot", t);
          // DRIFT CORRECTION: the model answered in Hindi while the student
          // spoke English (and selected English). Steer it back once so the
          // accent-driven drift can't lock the session into Hindi.
          if (langRef.current === "en" && userLangRef.current === "en"
              && DEVA.test(t)) {
            mark("lang_drift_corrected");
            sessRef.current?.sendText(
              "Please reply only in English for the rest of this session — " +
              "do not use Hindi unless I write or speak a full Hindi " +
              "sentence.");
          }
        }
        if (stateRef.current === "thinking" || stateRef.current === "speaking") {
          set("listening");
        }
      },
      onResumption: (h) => { resumeHandleRef.current = h; },
      onError: () => mark("live_error"),
      onClose: () => {
        mark("live_close");
        sessRef.current = null;
        if (closingRef.current || !activeRef.current) return;
        // unexpected drop — re-mint and resume with the server's handle
        const delay = Math.min(1000 * 2 ** retryRef.current++, 15000);
        set("error");
        window.setTimeout(() => {
          if (activeRef.current && !sessRef.current) void connect();
        }, delay);
      },
    });
    sessRef.current = sess;
    sess.connect(
      `${mint.ws_url}?access_token=${encodeURIComponent(mint.token)}`,
      {
        model: mint.model,
        voice: mint.voice,
        systemInstruction: mint.system_instruction,
        vad: mint.vad,
        resumption: resumeHandleRef.current
          ? { handle: resumeHandleRef.current } : {},
        transcription: {},
      },
    );
  }, [addMsg, cutAudio, set]);

  const startMic = useCallback(async (sess: LiveSession) => {
    if (micOnRef.current) return;
    try {
      micRef.current = await capturePcm(
        (b64) => sess.sendAudio(b64),
        (rms) => {
          /* local-VAD barge-in: sustained loud mic input while Infy talks →
           * duck output NOW (~50ms), the command recognizer confirms "stop"
           * ~300ms later; if no stop lands the fade comes back. Residual
           * speaker echo sits below the threshold thanks to echoCancellation. */
          streakRef.current = rms > 0.035 ? streakRef.current + 1 : 0;
          const talking = !pausedRef.current
            && (stateRef.current === "speaking"
                || (playerRef.current?.queued() ?? 0) > 0.1);
          if (talking && streakRef.current >= 2 && !duckedRef.current) {
            duckedRef.current = true;
            playerRef.current?.duck();
            window.clearTimeout(duckTimerRef.current);
            duckTimerRef.current = window.setTimeout(() => {
              if (!pausedRef.current) playerRef.current?.unduck();
              duckedRef.current = false; streakRef.current = 0;
            }, 1400);
          }
        },
      );
      micOnRef.current = true;
    } catch {
      mark("live_mic_fail");
      set("error", "microphone unavailable — check permission");
      setFailed(true);
    }
  }, [set]);

  const stopMic = useCallback(() => {
    micRef.current?.stop(); micRef.current = null; micOnRef.current = false;
  }, []);

  /* ---- command recognizer ---------------------------------------------------
   * Stop must not wait for Gemini's input transcription — by the time the
   * model emits it, the whole answer has already played. A dedicated Web
   * Speech recognizer listens ONLY for commands ("infy stop" / "infy
   * start"), exactly like the cascade path: detection → local audio cut is
   * instant, the pause drops any late audio, and "infy start" is heard
   * while paused. */
  const startCmdSTT = useCallback(() => {
    const w = window as unknown as Record<string, typeof SpeechRecognition | undefined>;
    const SRImpl = w.SpeechRecognition ?? w.webkitSpeechRecognition;
    if (!SRImpl || cmdSrRef.current) return;
    const sr = new SRImpl();
    sr.lang = SR_LANG[langRef.current];
    sr.continuous = true;
    sr.interimResults = true;
    sr.onresult = (e: SpeechRecognitionEvent) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0].transcript.trim();
        if (!text) continue;
        // speaker echo can garble "infy stop" — while Infy is audible also
        // accept the loose wake+stop pairing (same rule as the cascade)
        const gated = stateRef.current === "speaking"
          || (playerRef.current?.queued() ?? 0) > 0;
        const [cmd] = parseCommand(text);
        const stopNow = cmd === "stop" || (gated && looseStop(text));
        if (stopNow && !pausedRef.current) {
          mark("stop_cmd", text);
          pausedRef.current = true;
          cutAudio();                      // silence NOW — not at turn end
          set("stopped", PAUSED);
        } else if (cmd === "start" && pausedRef.current) {
          mark("start_cmd", text);
          pausedRef.current = false;
          set("listening");
        } else if (cmd === "hindi" || cmd === "english") {
          mark("lang_cmd", text);
          voiceLang(cmd === "hindi" ? "hi" : "en");
        }
      }
    };
    sr.onerror = () => { /* recognizer missing/blocked — Gemini path still works */ };
    sr.onend = () => {
      // keep the command ear alive while the session is active or paused —
      // it is how "infy start" is heard
      if (cmdSrRef.current === sr && activeRef.current) {
        sr.lang = SR_LANG[langRef.current];
        try { sr.start(); } catch { /* already running */ }
      }
    };
    cmdSrRef.current = sr;
    try { sr.start(); } catch { /* noop */ }
  }, [cutAudio, set, voiceLang]);

  const stopCmdSTT = useCallback(() => {
    const sr = cmdSrRef.current;
    cmdSrRef.current = null;
    if (sr) { sr.onend = null; try { sr.abort(); } catch { /* noop */ } }
  }, []);

  /* ---- public API (mirrors useInfy) -----------------------------------------*/
  /** Resume on a still-open session: unpause + restart the mic — skips the
   *  whole mint+setup round-trip (~1s saved per restart). Returns false
   *  when no reusable session exists. */
  const resume = useCallback(() => {
    const sess = sessRef.current;
    if (!sess?.open) return false;
    pausedRef.current = false;
    set("listening");
    void startMic(sess);
    startCmdSTT();
    return true;
  }, [set, startMic, startCmdSTT]);

  const start = useCallback(() => {
    if (activeRef.current) return;
    mark("start_cmd");
    activeRef.current = true; setLive(true);
    closingRef.current = false; retryRef.current = 0;
    const p = (playerRef.current ??= new PcmPlayer());
    if (p.ctx.state === "suspended") void p.ctx.resume();
    if (!resume()) { set("connecting"); void connect(); }
    startCmdSTT();
  }, [connect, resume, set, startCmdSTT]);

  const stop = useCallback(() => {
    mark("stop_cmd", "button");
    activeRef.current = false; setLive(false);
    pausedRef.current = false;
    cutAudio(); stopMic(); stopCmdSTT();
    // the session stays OPEN while stopped: no mic audio is sent (nothing
    // is billed), and start() reuses it instead of re-minting a token.
    // If the server drops an idle session, onClose clears sessRef and the
    // next start() mints fresh.
    set("stopped");
  }, [cutAudio, set, stopMic, stopCmdSTT]);

  const toggle = useCallback(() => {
    if (!activeRef.current) start();
    else if (stateRef.current === "stopped") {
      if (!resume()) { set("connecting"); void connect(); }
    } else stop();
  }, [connect, resume, set, start, stop]);

  const sendText = useCallback((text: string) => {
    const t = text.trim();
    if (!t) return false;
    if (!activeRef.current || !sessRef.current?.open) return false;
    addMsg("you", t);
    pausedRef.current = false;
    const [c] = parseCommand(t);
    if (c === "hindi" || c === "english") voiceLang(c === "hindi" ? "hi" : "en");
    sessRef.current.sendText(t);
    set("thinking");
    return true;
  }, [addMsg, set, voiceLang]);

  // steer a live session immediately; the next mint also bakes the choice
  // into the system instruction via the lang field (backend pins it with a
  // LANGUAGE LOCK so accent-driven drift can't flip the session)
  const setLanguage = useCallback((l: InfyLang) => {
    setLangState(l); langRef.current = l;
    if (sessRef.current?.open) {
      sessRef.current.sendText(
        l === "hi"
          ? "Please reply only in Hindi for the rest of this session."
          : "Please reply only in English for the rest of this session — " +
            "do not use Hindi unless I write or speak a full Hindi sentence.");
    }
  }, []);

  const setChapter = useCallback((id: number) => {
    chapterRef.current = id;
    // re-mint on next start so the new chapter lands in the system prompt;
    // if a session is live, restart it transparently
    if (activeRef.current) {
      closingRef.current = true;
      sessRef.current?.close(); sessRef.current = null;
      resumeHandleRef.current = "";
      closingRef.current = false;
      void connect();
    }
  }, [connect]);

  const end = useCallback(() => {
    closingRef.current = true;
    activeRef.current = false; setLive(false);
    window.clearTimeout(limitTimerRef.current);
    cutAudio(); stopMic(); stopCmdSTT();
    sessRef.current?.close(); sessRef.current = null;
    playerRef.current?.close(); playerRef.current = null;
    resumeHandleRef.current = "";
  }, [cutAudio, stopMic, stopCmdSTT]);

  useEffect(() => () => { end(); }, [end]);

  return {
    state, status, messages, sources: [] as string[], language, live,
    failed,
    start, stop, toggle, sendText, setLanguage, setChapter,
  };
}
