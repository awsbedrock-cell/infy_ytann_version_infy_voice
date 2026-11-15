/* ============================================================================
 * <Infy /> — the voice tutor as a drop-in component.
 *
 *   <Infy chapterId={1} />
 *   <Infy chapterId={1} lang="hi" docked={false} />
 *
 * Voice UX: orb button (start/stop), status line, chat log with grounding
 * sources, typed input, language toggle. Session internals in use-infy.ts;
 * transport in ../pipeline.ts. Commands: "infy start/stop/repeat/hindi/english", "quiz me".
 * ==========================================================================*/

import { useEffect, useRef, useState } from "react";
import { useInfy, type InfyLang, type InfyOptions } from "./use-infy";
import { useInfyLive } from "./use-infy-live";

/** Backend picks the voice path: /api/health.voice_mode
 *   live    — Gemini realtime speech-to-speech
 *   cascade — local Web Speech -> RAG -> LLM -> TTS
 *   auto    — live when a GEMINI_API_KEY exists, else cascade
 * Live falling over (no key, quota, mic) degrades to cascade automatically. */
type VoiceMode = "auto" | "live" | "cascade";

function useVoiceMode(): "live" | "cascade" {
  const [mode, setMode] = useState<"live" | "cascade">("cascade");
  useEffect(() => {
    let dead = false;
    fetch("/api/health").then((r) => r.json()).then((h) => {
      if (dead) return;
      const m = String(h?.voice_mode ?? "cascade") as VoiceMode;
      const hasKey = !!h?.providers?.hosted_keys?.gemini;
      setMode(m === "live" || (m === "auto" && hasKey) ? "live" : "cascade");
    }).catch(() => {});
    return () => { dead = true; };
  }, []);
  return mode;
}

export interface InfyProps extends InfyOptions {
  /** docked (default): fixed bottom bar. false: renders inline in flow. */
  docked?: boolean;
  placeholder?: string;
  className?: string;
}

export function Infy({ chapterId, lang, docked = true,
                       placeholder = "or type a question… e.g. how are winds formed?",
                       className = "" }: InfyProps) {
  // both hooks stay mounted (cheap — nothing runs until start()); the
  // backend's voice_mode decides whose controls drive the UI. If live
  // fails to come up (missing key, quota, mic), swap to the cascade.
  const casc = useInfy({ chapterId, lang });
  const liveH = useInfyLive({ chapterId, lang });
  const mode = useVoiceMode();
  const [override, setOverride] = useState<"cascade" | null>(null);
  const h = mode === "live" && !override ? liveH : casc;
  useEffect(() => {
    if (mode === "live" && liveH.failed) setOverride("cascade");
  }, [mode, liveH.failed]);

  const { state, status, messages, sources, language, live,
          toggle, sendText, setLanguage } = h;
  const [q, setQ] = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => { logRef.current?.scrollTo(0, logRef.current.scrollHeight); }, [messages]);

  const ask = () => {
    const text = q.trim();
    if (!text) return;
    if (sendText(text)) setQ("");
  };

  return (
    <div className={`infy${docked ? " infy-docked" : ""} infy-${state} ${className}`}>
      <div className="infy-row">
        <button
          className={`infy-orb${live ? " on" : ""}${state === "connecting" ? " busy" : ""}`}
          title={live ? "Infy stop" : "Infy start"}
          aria-label={live ? "Stop Infy microphone" : "Start Infy microphone"}
          aria-pressed={live}
          onClick={toggle}
        >
          {live ? "⏹" : "🎤"}
        </button>
        <div className="infy-status" role="status" aria-live="polite">
          {status}
          <span className={`infy-state infy-state-${state}`}>{state}</span>
        </div>
        <div className="infy-lang">
          <button
            className={language === "en" ? "on" : ""}
            onClick={() => setLanguage("en" as InfyLang)}>EN</button>
          <button
            className={language === "hi" ? "on" : ""}
            onClick={() => setLanguage("hi" as InfyLang)}>हिं</button>
        </div>
      </div>
      <div className="infy-log" ref={logRef}>
        {messages.map((m, i) => (
          <div className={`infy-msg ${m.kind}`} key={i}>
            <div className="infy-who">{m.kind === "you" ? "You" : "Infy"}</div>
            {m.text}
          </div>
        ))}
        {!!sources.length && (
          <div className="infy-msg bot infy-src">sources: {sources.join(" · ")}</div>
        )}
      </div>
      <div className="infy-ask">
        <input
          className="infy-askText"
          placeholder={placeholder}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
        />
        <button className="infy-askBtn" onClick={ask}>Ask</button>
      </div>
      <div className="infy-cmds">
        say <b>“Infy start”</b> · <b>“Infy stop”</b> · <b>“Infy repeat”</b> ·{" "}
        <b>“Infy Hindi”</b> / <b>“Infy English”</b> · <b>“quiz me”</b> — or just talk over Infy to interrupt
      </div>
    </div>
  );
}
