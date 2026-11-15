@echo off
cd /d %~dp0
if not exist varad.db python seed_db.py
rem Ensure the FTS retrieval index exists (idempotent, fast)
python -c "import rag,sqlite3;c=sqlite3.connect('varad.db');n=c.execute(\"select count(*) from sqlite_master where name='fts_chunk'\").fetchone()[0];c.close();exit(0 if n else 1)" 2>nul || python -c "import rag;rag.build_index()"
if not exist models\Qwen2.5-3B-Instruct-Q4_K_M.gguf (
  echo Downloading local LLM model ^(one-time, ~2 GB^)...
  hf download Qwen/Qwen2.5-3B-Instruct-GGUF Qwen2.5-3B-Instruct-Q4_K_M.gguf --local-dir models
)
rem CPU inference is the stable default: GPU offload on the shared 4GB
rem RTX 2050 crashes under contention. To try GPU when the box is quiet,
rem run: set GGUF_GPU_LAYERS=-1 && python backend.py
if not defined GGUF_GPU_LAYERS set GGUF_GPU_LAYERS=0
python backend.py
pause
