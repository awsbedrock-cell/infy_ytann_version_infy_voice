# Infy Voice — Stage 3 RAG audit (baseline)

Index and content untouched (read-only audit). Reproduce:

    python tests/rag_audit.py --json tests/rag_audit_out.json   # no LLM
    LLM_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b python eval_grounding.py --chapter 1

## Chunking (`fts_chunk`, 1,169 chunks, 4 chapters, all grade 8)

| Ch | Title | Chunks (page / Q&A) | Len min/median/max | >1500 chars | Dups | Pages indexed |
|---|---|---|---|---|---|---|
| 1 | Pressure, Winds, Storms, and Cyclones | 94 / 233 | 40 / 183 / 1515 | 1 | 0 | 18/18 |
| 2 | Solutes, Solvents, and Solutions | 127 / 234 | 40 / 170 / 1824 | 2 | 0 | 18/18 |
| 3 | How Nature Works in Harmony | 133 / 131 | 40 / 173 / 2089 | 2 | 0 | 20/20 |
| 4 | Proportional Reasoning-1 | 87 / 130 | 45 / 218 / 2067 | 3 | 0 | 28/30 |

Chunks are one page block each: no duplicates, good page coverage, short
(median ~180 chars), a few oversized blocks. 0 Devanagari chunks.

## Retrieval (top-3, curated `ask_qa` questions as queries)

| Ch | Own Q&A in top-3 | ≥1 textbook page in top-3 | Other chapters' Qs that still retrieve passages |
|---|---|---|---|
| 1 | 97.4% | 21.9% | 85.1% |
| 2 | 94.9% | 30.3% | 86.6% |
| 3 | 98.5% | 55.0% | 83.9% |
| 4 | 97.7% | 42.3% | 78.4% |

Hindi probe: `हवा का दबाव क्या होता है`, `चक्रवात कैसे बनते हैं` → **0 hits in
every chapter**; Hinglish `hawa ka dabav kya hai` → 3 hits (ch 1), mostly by
accident.

## End-to-end grounding (`eval_grounding.py`, ch 1, qwen3:8b) — 5/5 "pass"
- In-chapter (2): grounded, correct, cites pages/Q&A.
- Wrong premise: corrected ("not because the Earth rotates").
- "capital of France": answered "Paris" with no sources — the harness counts
  general answers as a pass; the product policy is undecided.
- Hindi: fluent Devanagari answer, but **sources: none** — ungrounded.

## Findings
1. **Self-Q&A precision is inflated**: the query is the stored question
   itself. It shows lexical retrieval works, not that paraphrased student
   questions work. A held-out paraphrase set is needed.
2. **Citations are weak**: only 22–55% of answers can cite a textbook page;
   the rest cite "chapter Q&A", which the student cannot open.
3. **Out-of-chapter refusal rests on the LLM alone**: 78–87% of other
   chapters' questions still pull "evidence" via shared words (BM25 OR-query
   without a score threshold).
4. **Hindi is not retrieved at all**: Devanagari queries never match English
   chunks, so every Hindi answer is from model memory.
5. 5-question eval is too small to steer decisions.

## Changes made and measured (index and content still untouched)

Held-out set `tests/eval_qa_ch1.jsonl` (30 questions: 12 paraphrased
in-chapter, 6 Hindi, 8 off-chapter, 4 wrong-premise; relevance = passage
contains a gold term). Run `python tests/eval_retrieval.py -v`.

1. Hindi→English glossary for Devanagari query words (`rag._HI_EN`).
2. Off-chapter gate: best passage needs BM25 ≥ `RAG_MIN_SCORE` (6.0), or
   ≥ `RAG_FLOOR_SCORE` (3.0) plus ≥ `RAG_MIN_COVERAGE` (50%) of query terms.
3. Prompt: off-chapter questions get a one-sentence answer, "beyond this
   chapter", then a link back to the chapter (product decision).
4. Tried and rejected: FTS5 prefix/stem queries. They lowered English hit@3
   from 0.875 to 0.812 and doubled off-chapter leak (14% to 29%).

| Metric | Baseline | Now |
|---|---|---|
| English hit@3 / precision@3 | 0.875 / 0.708 | 0.875 / 0.708 |
| Hindi hit@3 / precision@3 | 0 / 0 | 1.0 / 0.833 |
| Off-chapter leak (held-out, en) | 57% (4/7) | 14% (1/7: "salt dissolve in water") |
| Cross-chapter leak, ch 1–4 (`rag_audit.py`) | 85 / 87 / 84 / 78% | 48 / 47 / 35 / 34% |
| Curated self-Q&A top-3, ch 1–4 | 97.4 / 94.9 / 98.5 / 97.7% | unchanged |
| No-hit rate on curated Qs | not recorded | 0–0.4% |

`eval_grounding.py --chapter 1` (qwen3:8b): 5/5. "capital of France" now
gets "Paris… beyond what we're studying… in our chapter…". The Hindi
question now cites 3 chapter Q&A sources (was none).

Known limits: thresholds were tuned on a 30-question set for chapter 1 only.
The glossary is hand-written (~70 words) and covers just these 4 chapters.
`चक्रवात कैसे बनते हैं` still pulls passages in ch 2/4 through "form".
Page citations are unchanged (still 22–55%).

### Per-chapter held-out sets (ch 2–4)

`tests/eval_qa_ch{2,3,4}.jsonl`, 18 questions each (8 English paraphrase,
3 Hindi, 5 off-chapter, 2 wrong-premise). Written by hand from the chapter
key points, not copied from `ask_qa`. Baseline = `rag.py` at `07bc5a1`.
The glossary got ~25 more words (density, habitat, decomposer, food web…)
after the first ch 2–4 run showed 3 Hindi misses; the "Now" column is
after that, so Hindi numbers here are not held-out any more.

| Metric | ch2 base → now | ch3 base → now | ch4 base → now |
|---|---|---|---|
| English hit@3 | 1.0 → 1.0 | 1.0 → 1.0 | 1.0 → 1.0 |
| English precision@3 | 0.933 → 0.900 | 0.833 → 0.833 | 0.933 → 0.933 |
| Hindi hit@3 | 0 → 1.0 | 0 → 1.0 | 0 → 1.0 |
| Off-chapter leak (en) | 80% → 40% | 40% → 0% | 60% → 0% |
| Page citation in top 3 (en) | 30% → 40% | 70% → 70% | 10% → 10% |

Ch 2 still leaks "how do cyclones form over the ocean" (shared word
"form"/"water") and "who invented the telephone" (1 weak hit).

### Page citations for Q&A chunks

Curated Q&A chunks had no page, so they were cited as "chapter Q&A".
`rag._page_for` now attributes each Q&A hit to the textbook page block in
the same chapter that best matches its answer (FTS5 BM25), at query time
— the index rows are not changed. Source becomes `page N (chapter Q&A)`.

| Citable page in top 3 (held-out, en / hi) | Before | After |
|---|---|---|
| ch1 | 44% / 50% | 100% / 100% |
| ch2 | 40% / 33% | 100% / 100% |
| ch3 | 70% / 67% | 100% / 100% |
| ch4 | 10% / 0% | 100% / 100% |

"Citable" means a page number is attached, not that it is the right page.
Spot check of 6 random Q&A chunks: all 6 mapped to a page on the same
topic (e.g. lightning conductor → p. 92), but there is no gold page set,
so correctness of the page is **not measured**. Tests:
`tests/test_rag_pages.py`.

## Remaining candidates
- Hindi beyond the glossary: LLM query translation (hosted) or a
  multilingual embedding index alongside FTS5; measure on a larger Hindi set.
- Gold page numbers for the held-out sets, to measure citation correctness.
- Fine-tuning: not justified yet — no failure above is a model-knowledge
  problem; retrieval and prompt fixes come first.
