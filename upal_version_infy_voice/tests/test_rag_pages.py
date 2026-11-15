"""Every retrieved passage should be citable as a textbook page."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rag  # noqa: E402


def test_qa_hits_are_attributed_to_a_page():
    for ch, q in ((1, "what is air pressure"), (2, "what is a saturated solution"),
                  (3, "what do decomposers do"), (4, "how do I simplify a ratio")):
        hits = rag.retrieve(q, ch, k=3)
        assert hits, q
        for h in hits:
            assert h["page"] is not None, (q, h["source"])
            assert h["source"].startswith("page "), h["source"]


def test_page_attribution_stays_in_chapter():
    pages = {}
    import sqlite3
    con = sqlite3.connect(rag.DB)
    for ch, p in con.execute("SELECT chapter, page FROM fts_chunk WHERE kind='page'"):
        pages.setdefault(int(ch), set()).add(p)
    con.close()
    for h in rag.retrieve("what is a saturated solution", 2, k=6):
        assert h["page"] in pages[2]
