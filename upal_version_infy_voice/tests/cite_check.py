"""cite_check.py — spot-check that Q&A-chunk page attributions map to a
topically matching textbook page. Prints chunk + mapped page snippet for
manual judgment; --json prints verdict-ready rows."""
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import rag  # noqa: E402

random.seed(7)
con = sqlite3.connect(Path(__file__).parents[1] / "varad.db")

rows = con.execute(
    "SELECT rowid, chapter, page, text FROM fts_chunk WHERE kind='qa'"
).fetchall()
sample = random.sample(rows, 10)
n_ok = 0
for cid, ch, page, text in sample:
    mapped = rag._page_for(con, ch, text)
    ptxt = ""
    if mapped:
        row = con.execute(
            "SELECT text FROM fts_chunk WHERE chapter=? AND page=? "
            "AND kind='page' LIMIT 1", (ch, mapped)).fetchone()
        ptxt = (row[0][:160] if row else "").replace("\n", " ")
    print(f"Q&A {cid} (ch{ch}): {text[:80]!r}")
    print(f"   -> page {mapped}: {ptxt!r}")
    print()
con.close()
