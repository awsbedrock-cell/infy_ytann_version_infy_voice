"""RAG retrieval sanity: chapter questions must retrieve passages from the
RIGHT chapter, and unrelated questions must not fake confidence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rag  # noqa: E402


def test_index_is_built_and_covers_all_chapters():
    import sqlite3
    con = sqlite3.connect(rag.DB)
    counts = dict(con.execute(
        "SELECT chapter, COUNT(*) FROM fts_chunk GROUP BY chapter").fetchall())
    con.close()
    assert set(counts) >= {1, 2, 3, 4}
    assert all(n > 50 for n in counts.values())


def test_chapter1_questions_retrieve_relevant_passages():
    hits = rag.retrieve("how are winds formed", 1)
    assert hits, "expected hits for a core chapter-1 question"
    joined = " ".join(h["text"].lower() for h in hits)
    assert "wind" in joined or "pressure" in joined


def test_retrieval_is_scoped_to_the_selected_chapter():
    # cyclones are chapter 1; asking in chapter 4 (math) shouldn't pull them
    hits = rag.retrieve("why do cyclones weaken on land", 4)
    joined = " ".join(h["text"].lower() for h in hits)
    assert "cyclone" not in joined or not hits


def test_no_hits_for_empty_or_garbage_questions():
    assert rag.retrieve("", 1) == []
    assert rag.retrieve("???", 1) == []


def test_hindi_questions_retrieve_english_passages():
    for q in ("हवा का दबाव क्या होता है", "चक्रवात की आँख क्या होती है"):
        assert rag.retrieve(q, 1, k=3), q


def test_off_chapter_questions_retrieve_nothing():
    for q in ("who wrote the national anthem of India",
              "who won the cricket world cup",
              "what is photosynthesis in plants"):
        assert rag.retrieve(q, 1) == [], q


def test_ask_qa_pairs_are_indexed():
    # curated Q&A content must be reachable, not just page text
    hits = rag.retrieve("monsoon winds india", 1)
    assert hits
