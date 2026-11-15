"""Seed varad.db from content.json — run once:  python seed_db.py"""
import json, sqlite3, pathlib

BASE = pathlib.Path(__file__).parent
data = json.load(open(BASE / "content.json", encoding="utf-8"))

db = sqlite3.connect(BASE / "varad.db")
db.execute("DROP TABLE IF EXISTS course")
db.execute("DROP TABLE IF EXISTS chapter")
db.execute("""CREATE TABLE course (
  id INTEGER PRIMARY KEY, title TEXT, subject TEXT, grade INTEGER,
  doc TEXT NOT NULL)""")
db.execute("""CREATE TABLE chapter (
  id INTEGER PRIMARY KEY, course_id INTEGER, title TEXT, subject TEXT,
  grade INTEGER, chapter_number INTEGER, slug TEXT, summary TEXT,
  doc TEXT NOT NULL)""")

for c in data["course"]:
    db.execute("INSERT INTO course VALUES (?,?,?,?,?)",
               (c["id"], c.get("title"), c.get("subject"), c.get("grade"),
                json.dumps(c, ensure_ascii=False)))
for c in data["chapter"]:
    db.execute("INSERT INTO chapter VALUES (?,?,?,?,?,?,?,?,?)",
               (c["id"], c.get("course_id"), c.get("title"), c.get("subject"),
                c.get("grade"), c.get("chapter_number"), c.get("slug"),
                c.get("summary"), json.dumps(c, ensure_ascii=False)))
db.commit()
print("seeded:", db.execute("SELECT COUNT(*) FROM chapter").fetchone()[0],
      "chapters,", db.execute("SELECT COUNT(*) FROM course").fetchone()[0],
      "courses")
db.close()

import rag  # noqa: E402  — build the FTS retrieval index for RAG
print("indexed:", rag.build_index(), "retrieval chunks")
