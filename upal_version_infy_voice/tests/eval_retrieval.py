"""eval_retrieval.py — retrieval metrics on the held-out eval set (no LLM).

    python tests/eval_retrieval.py [--set tests/eval_qa_ch1.jsonl] [--chapter 1]

in/premise: hit@3 (any top-3 passage contains a gold term), precision@3
            (share of top-3 passages that contain a gold term),
            page@3 (a textbook page is in top-3, i.e. citable)
out:        leak (retrieval returned passages for an off-chapter question)
Reported separately for English and Hindi.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import rag  # noqa: E402


def run(cases, chapter, verbose=False):
    rows = []
    for c in cases:
        hits = rag.retrieve(c["q"], chapter, k=3)
        rel = [any(g in h["text"].lower() for g in c["gold"]) for h in hits]
        rows.append({**c, "n": len(hits), "rel": rel,
                     "page": any(h.get("page") is not None for h in hits)})
        if verbose:
            print(f"{c['id']:6s} n={len(hits)} rel={sum(rel)} {c['q']}")
    out = {}
    for lang in ("en", "hi"):
        inn = [r for r in rows if r["lang"] == lang and r["type"] != "out"]
        off = [r for r in rows if r["lang"] == lang and r["type"] == "out"]
        if inn:
            out[f"{lang}_hit@3"] = round(sum(any(r["rel"]) for r in inn) / len(inn), 3)
            out[f"{lang}_precision@3"] = round(
                sum(sum(r["rel"]) / 3 for r in inn) / len(inn), 3)
            out[f"{lang}_page@3"] = round(sum(r["page"] for r in inn) / len(inn), 3)
        if off:
            out[f"{lang}_offchapter_leak"] = round(sum(r["n"] > 0 for r in off) / len(off), 3)
    return out, rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(ROOT / "tests" / "eval_qa_ch1.jsonl"))
    ap.add_argument("--chapter", type=int, default=1)
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    cases = [json.loads(l) for l in Path(a.set).read_text(encoding="utf-8").splitlines() if l.strip()]
    m, _ = run(cases, a.chapter, a.v)
    print(json.dumps(m, indent=1))
