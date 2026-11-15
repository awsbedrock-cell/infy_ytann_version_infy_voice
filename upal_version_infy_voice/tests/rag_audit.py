"""rag_audit.py — Stage 3 retrieval audit (no LLM, deterministic).

For every chapter in varad.db:
  * chunk stats: count by kind, length distribution, duplicates, page coverage
  * self-retrieval: each curated ask_qa question as a query — is its own
    Q&A chunk in top-3? (proxy for retrieval precision on in-chapter Qs)
  * page source: does at least one *page* chunk come back (citable page)?
  * cross-chapter leakage: other chapters' questions asked here — how often
    retrieval still returns passages (=> model sees "evidence" and may answer
    instead of refusing)
  * Hindi probe: Devanagari / Hinglish queries against English chunks

    python tests/rag_audit.py [--json out.json]
"""
import argparse, json, re, sqlite3, statistics, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import rag  # noqa: E402

DEVA = re.compile(r"[\u0900-\u097f]")


def pct(a, b):
    return round(100 * a / b, 1) if b else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()
    con = sqlite3.connect(rag.DB)
    chapters = {}
    for (doc_json,) in con.execute("SELECT doc FROM chapter"):
        d = json.loads(doc_json)
        chapters[d.get("id")] = d
    rows = con.execute("SELECT chapter, kind, page, text FROM fts_chunk").fetchall()
    con.close()
    by_ch = {}
    for c, kind, page, text in rows:
        by_ch.setdefault(int(c), []).append((kind, page, text))

    report = {"chapters": [], "index_chunks": len(rows)}
    all_qs = [(cid, (qa.get("q") or "").strip())
              for cid, d in chapters.items() for qa in d.get("ask_qa") or [] if qa.get("q")]
    for cid, d in sorted(chapters.items()):
        ch = by_ch.get(cid, [])
        lens = [len(t) for _, _, t in ch]
        norm = Counter(re.sub(r"\W+", " ", t.lower()).strip() for _, _, t in ch)
        dups = sum(n - 1 for n in norm.values() if n > 1)
        pages = {p for k, p, _ in ch if k == "page"}
        qs = [(qa.get("q") or "").strip() for qa in d.get("ask_qa") or [] if qa.get("q")]
        self_hit = page_hit = empty = 0
        for q in qs:
            hits = rag.retrieve(q, cid, k=3)
            if not hits:
                empty += 1
            if any(h["kind"] == "qa" and h["text"].startswith(f"Q: {q}") for h in hits):
                self_hit += 1
            if any(h["kind"] == "page" for h in hits):
                page_hit += 1
        others = [q for c, q in all_qs if c != cid]
        leak = sum(1 for q in others if rag.retrieve(q, cid, k=3))
        entry = {
            "chapter": cid, "title": d.get("title"), "grade": d.get("grade"),
            "chunks": len(ch), "kinds": dict(Counter(k for k, _, _ in ch)),
            "len_min_median_max": [min(lens), int(statistics.median(lens)), max(lens)] if lens else None,
            "chunks_over_1500_chars": sum(1 for x in lens if x > 1500),
            "duplicate_chunks": dups, "pages_indexed": len(pages),
            "pages_in_doc": len(d.get("pages") or []),
            "devanagari_chunks": sum(1 for _, _, t in ch if DEVA.search(t)),
            "ask_qa": len(qs),
            "self_qa_in_top3_pct": pct(self_hit, len(qs)),
            "page_source_in_top3_pct": pct(page_hit, len(qs)),
            "no_hits_pct": pct(empty, len(qs)),
            "cross_chapter_qs": len(others),
            "cross_chapter_leak_pct": pct(leak, len(others)),
        }
        report["chapters"].append(entry)
        print(json.dumps(entry, ensure_ascii=False))
    hindi_qs = ["हवा का दबाव क्या होता है", "चक्रवात कैसे बनते हैं", "hawa ka dabav kya hai"]
    report["hindi_probe"] = {q: {cid: len(rag.retrieve(q, cid, k=3)) for cid in chapters}
                             for q in hindi_qs}
    print("hindi_probe:", json.dumps(report["hindi_probe"], ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
