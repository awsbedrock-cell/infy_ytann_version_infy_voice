import asyncio, json, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline, rag, providers


async def main():
    con = sqlite3.connect("varad.db")
    doc = json.loads(con.execute("SELECT doc FROM chapter WHERE id=1").fetchone()[0])
    con.close()
    sess = pipeline.TutorSession(1, doc.get("grade", 8), doc["title"],
                                 doc.get("subject", ""),
                                 lambda m: asyncio.sleep(0),
                                 lambda b: asyncio.sleep(0))
    hits = rag.retrieve("how are winds formed", 1, k=3)
    sys_prompt = sess._system(hits)
    print("prompt chars:", len(sys_prompt), "est tokens:", len(sys_prompt)//4)

    llm = providers.make_llm()
    t0 = time.time()
    ntok = 0
    marks = []
    async for tok in llm.stream(sys_prompt, [], "how are winds formed"):
        ntok += 1
        marks.append((ntok, round((time.time()-t0)*1000)))
        if ntok > 60:
            break
    print("tok1:", marks[0], " tok10:", marks[9] if len(marks) > 9 else None,
          " tok30:", marks[29] if len(marks) > 29 else None,
          " last:", marks[-1])
    # tokens/sec after first
    if len(marks) > 10:
        dt = (marks[-1][1] - marks[0][1]) / 1000
        print(f"decode rate after tok1: {round((len(marks)-1)/dt,1)} tok/s")


asyncio.run(main())
