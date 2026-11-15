"""rag.py — SQLite FTS5 retrieval over real chapter content.

Replaces the old approach (stuff title+summary+keypoints+first-18-pages into
the system prompt, hard-trimmed at 12KB, silently dropping most of every
chapter). Now: every page block and every curated ask_qa pair is indexed per
chapter; each student question retrieves only the passages that matter, and
the answer prompt carries those passages + citations.

Index lives in varad.db itself (fts_chunk virtual table). Rebuild with:
    python -c "import rag; rag.build_index()"
seed_db.py calls it too.
"""
import json
import os
import re
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DB = BASE / "varad.db"

_WORD = re.compile(r"[a-zA-Z0-9\u0900-\u097F']+")
_DEVA = re.compile(r"[\u0900-\u097F]")


def _chunks_from_doc(doc: dict) -> list[dict]:
    """One retrieval unit per page block + one per ask_qa pair."""
    out = []
    for p in doc.get("pages") or []:
        for b in p.get("blocks") or []:
            t = (b.get("text") or "").strip()
            if len(t) >= 40:  # skip page furniture / headings-fragments
                out.append({
                    "kind": "page", "page": p.get("page"), "text": t,
                    "source": f"page {p.get('page')}",
                })
    for qa in doc.get("ask_qa") or []:
        q, a = (qa.get("q") or "").strip(), (qa.get("a") or "").strip()
        if q and a:
            out.append({
                "kind": "qa", "page": None, "text": f"Q: {q}\nA: {a}",
                "source": "chapter Q&A",
            })
    return out


def build_index(db_path: Path = DB):
    con = sqlite3.connect(db_path)
    con.executescript("""
        DROP TABLE IF EXISTS fts_chunk;
        CREATE VIRTUAL TABLE fts_chunk USING fts5(
            text, chapter UNINDEXED, kind UNINDEXED, page UNINDEXED,
            source UNINDEXED, tokenize='unicode61');
    """)
    n = 0
    for (doc_json,) in con.execute("SELECT doc FROM chapter"):
        doc = json.loads(doc_json)
        cid = doc.get("id")
        for ch in _chunks_from_doc(doc):
            con.execute(
                "INSERT INTO fts_chunk(text,chapter,kind,page,source) VALUES(?,?,?,?,?)",
                (ch["text"], cid, ch["kind"], ch["page"], ch["source"]))
            n += 1
    con.commit()
    con.close()
    return n


# Question-frame words that carry no topic signal. Without this filter a
# question like "what is the capital of France" matches chapter pages merely
# because they contain "what", producing bogus 'sources' and confident answers
# on out-of-chapter topics.
_STOP = set(
    "what who whom whose which when where why how does do did is are was were "
    "will would shall should can could may might must the and for with about "
    "into from that this these those there here your you its it its his her "
    "their our we they he she them him us me my mine not no yes kya kaise kyon "
    "kyu kab kahan kaun hai hain ho hota hoti hote".split()
)


# Chapters are indexed in English, so Devanagari query words never matched
# (0 hits for every Hindi question). Map common Hindi science words onto the
# English terms the chapters use. Keys are nukta-stripped (़ removed).
_HI_EN = {
    "हवा": "air wind", "वायु": "air", "पवन": "wind", "दबाव": "pressure",
    "दाब": "pressure", "वायुदाब": "air pressure", "चक्रवात": "cyclone",
    "तूफान": "storm", "आंधी": "storm", "आँधी": "storm", "गरम": "warm hot",
    "गर्म": "warm hot", "ठंडा": "cold", "ठंडी": "cold", "ऊपर": "rises",
    "उठती": "rises", "उठता": "rises", "फैलती": "expands", "फैलता": "expands",
    "चलती": "blow", "चलता": "blow", "बहती": "blow", "बनते": "form",
    "बनती": "form", "बनता": "form", "आँख": "eye", "आंख": "eye", "बचने": "safety",
    "बचाव": "safety", "सुरक्षा": "safety", "चेतावनी": "warning",
    "बारिश": "rain", "वर्षा": "rain", "बादल": "cloud", "समुद्र": "sea ocean",
    "पानी": "water", "जल": "water", "गति": "speed", "रफ्तार": "speed",
    "गुब्बारा": "balloon", "जमीन": "land", "भूमि": "land", "तापमान": "temperature",
    "बिजली": "lightning", "गरज": "thunder", "नमी": "moisture", "भाप": "vapour",
    "विलयन": "solution", "विलेय": "solute", "विलायक": "solvent", "घुलना": "dissolve",
    "घुलता": "dissolve", "घुलती": "dissolve", "नमक": "salt", "चीनी": "sugar",
    "अनुपात": "ratio", "समानुपात": "proportion", "प्रकृति": "nature",
    "पौधे": "plants", "पौधा": "plant", "जानवर": "animals", "जंगल": "forest",
    "घनत्व": "density", "द्रव्यमान": "mass", "आयतन": "volume", "तैरती": "float",
    "तैरता": "float", "डूबती": "sink", "डूबता": "sink", "संतृप्त": "saturated",
    "घुलनशीलता": "solubility", "आवास": "habitat", "अपघटक": "decomposers",
    "उत्पादक": "producers", "उपभोक्ता": "consumers", "भोजन": "food",
    "आहार": "food", "श्रृंखला": "chain", "जाल": "web", "समुदाय": "community",
    "जनसंख्या": "population", "जीव": "organisms", "सरल": "simplify",
    "पद": "terms",
}


_SUFFIX = ("ations", "ation", "ings", "ing", "ed", "es", "s")


def _stem(w: str) -> str:
    """Crude suffix strip for the coverage check only (unicode61 does no
    stemming). FTS5 prefix queries with these stems were measured and
    retrieved worse, so MATCH keeps exact words."""
    for s in _SUFFIX:
        if w.endswith(s) and len(w) - len(s) >= 4:
            return w[: -len(s)]
    return w


def _terms(q: str) -> list[str]:
    out = []
    for w in _WORD.findall(q.lower()):
        if _DEVA.search(w):
            out += _HI_EN.get(w.replace("\u093c", ""), "").split()
        elif len(w) > 2 and w not in _STOP:
            out.append(w)
    return list(dict.fromkeys(out))[:12]


# Off-chapter gate on the best passage (BM25 from FTS5 rank, which is
# negative; bigger magnitude = better): accept if it scores >= MIN_SCORE, or
# scores >= FLOOR_SCORE and contains >= MIN_COVERAGE of the query terms.
# Short on-topic questions ("how are winds formed") score low but match half
# their terms; off-topic ones share one stray word ("india", "cup").
# Tuned on tests/eval_qa_ch1.jsonl, checked with tests/rag_audit.py.
MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "6.0"))
FLOOR_SCORE = float(os.getenv("RAG_FLOOR_SCORE", "3.0"))
MIN_COVERAGE = float(os.getenv("RAG_MIN_COVERAGE", "0.5"))


def _coverage(terms: list[str], text: str) -> float:
    low = text.lower()
    return sum(_stem(t) in low for t in terms) / len(terms)


def retrieve(question: str, chapter_id: int, k: int = 6,
             db_path: Path = DB) -> list[dict]:
    """Top-k passages for a question within one chapter. Returns [] when the
    index is missing/empty or nothing matches — callers must treat that as
    'no evidence', not 'answer anyway'."""
    terms = _terms(question)
    if not terms:
        return []
    # OR the terms — FTS5 bm25 ranks by match density; OR recall > AND.
    match = " OR ".join(f'"{t}"' for t in terms)
    try:
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(
                "SELECT text, kind, page, source, rank FROM fts_chunk "
                "WHERE chapter=? AND fts_chunk MATCH ? "
                "ORDER BY rank LIMIT ?", (chapter_id, match, k)).fetchall()
            best = -rows[0][4] if rows else 0.0
            if not rows or (best < MIN_SCORE and (
                    best < FLOOR_SCORE
                    or _coverage(terms, rows[0][0]) < MIN_COVERAGE)):
                return []
            out = []
            for text, kind, page, source, score in rows:
                if kind == "qa" and page is None:
                    page = _page_for(con, chapter_id, text)
                    if page is not None:
                        source = f"page {page} (chapter Q&A)"
                out.append({"text": text, "kind": kind, "page": page,
                            "source": source, "score": score})
            return out
        finally:
            con.close()
    except sqlite3.Error:
        return []


def random_quiz(chapter_id: int, db_path: Path = DB,
                exclude: set[str] | None = None):
    """A random curated Q&A pair from this chapter for practice mode.
    Returns {"q": ..., "a": ...} or None when the chapter has none."""
    import random
    try:
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(
                "SELECT text FROM fts_chunk WHERE chapter=? AND kind='qa'",
                (chapter_id,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    pool = []
    for (text,) in rows:
        if "\nA:" not in text:
            continue
        q, a = text.split("\nA:", 1)
        q, a = q.removeprefix("Q:").strip(), a.strip()
        if q and a and (not exclude or q not in exclude):
            pool.append({"q": q, "a": a})
    return random.choice(pool) if pool else None


def _page_for(con: sqlite3.Connection, chapter_id: int, qa_text: str):
    """Citable page for a curated Q&A chunk: the textbook page block that
    best matches its answer. Computed at query time so the index and the
    course content stay untouched."""
    terms = _terms(qa_text.split("\nA:", 1)[-1])
    if not terms:
        return None
    row = con.execute(
        "SELECT page FROM fts_chunk WHERE chapter=? AND kind='page' "
        "AND fts_chunk MATCH ? ORDER BY rank LIMIT 1",
        (chapter_id, " OR ".join(f'"{t}"' for t in terms))).fetchone()
    return row[0] if row else None
