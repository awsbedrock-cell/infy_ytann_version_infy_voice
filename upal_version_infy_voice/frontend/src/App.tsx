import { useEffect, useState } from "react";
import type { Chapter } from "./types";
import { AskView, CardsView, FactsView, PointsView, QuizView, ReadView } from "./views";
import { Infy } from "./infy";

type Tab = "read" | "points" | "ask" | "quiz" | "cards" | "facts";

const TABS: [Tab, string][] = [
  ["read", "📖 Read"], ["points", "✅ Key Points"], ["ask", "💬 Q&A"],
  ["quiz", "📝 Quiz"], ["cards", "🃏 Flashcards"], ["facts", "✨ Fun Facts"],
];

interface ChapterStub { id: number; title: string; subject: string; grade: number; chapter_number: number }

export default function App() {
  const [chapters, setChapters] = useState<ChapterStub[]>([]);
  const [ch, setCh] = useState<Chapter | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<Tab>("read");
  const [chapterId, setChapterId] = useState(1);

  useEffect(() => {
    fetch("/api/chapter")
      .then((r) => (r.ok ? r.json() : Promise.reject(`chapters ${r.status}`)))
      .then((j) => setChapters(j.items))
      .catch(() => {/* list is optional — selector hides */});
  }, []);

  useEffect(() => {
    setCh(null); setErr("");
    fetch(`/api/chapter/${chapterId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(`chapter ${r.status}`)))
      .then(setCh)
      .catch((e) => setErr(String(e)));
  }, [chapterId]);

  return (
    <>
      <header>
        <h1>{ch ? ch.title : "Varad Learn"}</h1>
        <p>
          {ch
            ? `${ch.subject} · Grade ${ch.grade} · ${ch.pages.length} pages · ${ch.quiz.length} quiz · ${ch.flashcards.length} flashcards`
            : err || "loading chapter…"}
        </p>
        {chapters.length > 1 && (
          <select
            className="chapter-pick"
            value={chapterId}
            onChange={(e) => setChapterId(Number(e.target.value))}
          >
            {chapters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.subject === "math" ? "📐" : "🔬"} Ch {c.chapter_number}: {c.title}
              </option>
            ))}
          </select>
        )}
      </header>
      <main>
        <div className="tabs">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
        {ch && (
          tab === "read" ? <ReadView ch={ch} /> :
          tab === "points" ? <PointsView ch={ch} /> :
          tab === "ask" ? <AskView ch={ch} /> :
          tab === "quiz" ? <QuizView ch={ch} /> :
          tab === "cards" ? <CardsView ch={ch} /> :
          <FactsView ch={ch} />
        )}
      </main>
      <Infy key={chapterId} chapterId={chapterId} />
    </>
  );
}
