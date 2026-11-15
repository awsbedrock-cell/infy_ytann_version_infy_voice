import { useMemo, useState } from "react";
import type { Chapter } from "./types";

/* ---- Read: every textbook page, scanned image + text blocks ------------- */
export function ReadView({ ch }: { ch: Chapter }) {
  return (
    <>
      {ch.pages.map((p) => (
        <div className="card" key={p.page}>
          {p.image && <img className="pgimg" src={p.image} alt={`page ${p.page}`} loading="lazy" />}
          {p.blocks.map((b, i) => {
            switch (b.type) {
              case "heading": return <p className="blk-h" key={i}>{b.text}</p>;
              case "question": return <p className="blk-q" key={i}>{b.text}</p>;
              case "activity": return <div className="blk-act" key={i}>🧪 {b.text}</div>;
              case "table": return <div className="blk-tbl" key={i}>📊 {b.text}</div>;
              case "figure": return <p className="blk-fig" key={i}>🖼 {b.text}</p>;
              default: return <p className="blk-p" key={i}>{b.text}</p>;
            }
          })}
          <div className="pgno">— page {p.page} —</div>
        </div>
      ))}
    </>
  );
}

/* ---- Key points --------------------------------------------------------- */
export function PointsView({ ch }: { ch: Chapter }) {
  return (
    <div className="card">
      {ch.key_points.map((k, i) => (
        <p className="kp" key={i}>• {typeof k === "string" ? k : k.text}</p>
      ))}
    </div>
  );
}

/* ---- Ask Q&A bank (filterable) ------------------------------------------- */
export function AskView({ ch }: { ch: Chapter }) {
  const [f, setF] = useState("");
  const rows = useMemo(() => {
    const needle = f.trim().toLowerCase();
    return needle
      ? ch.ask_qa.filter((q) => (q.q + " " + q.a).toLowerCase().includes(needle))
      : ch.ask_qa;
  }, [f, ch]);
  return (
    <div className="card">
      <input
        className="qa-filter"
        placeholder={`filter ${ch.ask_qa.length} questions… e.g. winds, cyclone, pressure`}
        value={f}
        onChange={(e) => setF(e.target.value)}
      />
      {rows.length === 0 && <p className="kp">No match in this chapter</p>}
      {rows.slice(0, 80).map((q, i) => (
        <details className="qa" key={i}>
          <summary>{q.q}</summary>
          <p>{q.a}</p>
        </details>
      ))}
      {rows.length > 80 && <p className="kp">…and {rows.length - 80} more — type to filter</p>}
    </div>
  );
}

/* ---- Quiz: interactive MCQs --------------------------------------------- */
export function QuizView({ ch }: { ch: Chapter }) {
  const [done, setDone] = useState<Record<number, number>>({});
  const answered = Object.keys(done).length;
  const score = ch.quiz.filter((q, i) => done[i] === q.answer).length;
  return (
    <div className="card">
      {ch.quiz.map((q, i) => (
        <div key={i}>
          <div className="quiz-q">{i + 1}. {q.q}</div>
          {q.options.map((o, j) => {
            let cls = "quiz-opt";
            if (done[i] !== undefined) {
              if (j === q.answer) cls += " right";
              else if (j === done[i]) cls += " wrong";
            }
            return (
              <button className={cls} key={j} disabled={done[i] !== undefined}
                onClick={() => setDone((d) => ({ ...d, [i]: j }))}>
                {o}
              </button>
            );
          })}
          {done[i] !== undefined && <p className="quiz-ex">{q.explain}</p>}
        </div>
      ))}
      {answered === ch.quiz.length && (
        <div className="quiz-score">
          Score: {score} / {ch.quiz.length}
          {score === ch.quiz.length ? " 🎉" : score >= 8 ? " — well done!" : " — revise and retry"}
        </div>
      )}
    </div>
  );
}

/* ---- Flashcards ---------------------------------------------------------- */
export function CardsView({ ch }: { ch: Chapter }) {
  const [idx, setIdx] = useState(0);
  const [flip, setFlip] = useState(false);
  const c = ch.flashcards[idx];
  const go = (d: number) => {
    setIdx((i) => (i + d + ch.flashcards.length) % ch.flashcards.length);
    setFlip(false);
  };
  return (
    <div className="card">
      <div className={`fc${flip ? " flip" : ""}`} onClick={() => setFlip(!flip)}>
        <div className="fc-in">
          <div className="fc-f">{c.front}</div>
          <div className="fc-b">{c.back}</div>
        </div>
      </div>
      <div className="fc-nav">
        <button onClick={() => go(-1)}>← prev</button>
        <button onClick={() => go(1)}>next →</button>
      </div>
      <div className="fc-count">{idx + 1} / {ch.flashcards.length} — tap card to flip</div>
    </div>
  );
}

/* ---- Fun facts ----------------------------------------------------------- */
export function FactsView({ ch }: { ch: Chapter }) {
  return (
    <div className="card">
      {ch.fun_facts.map((f, i) => (
        <div className="fact" key={i}>💡 {f}</div>
      ))}
    </div>
  );
}
