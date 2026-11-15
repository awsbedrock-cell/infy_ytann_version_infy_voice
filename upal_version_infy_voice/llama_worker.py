"""llama_worker.py — GGUF generation in an isolated subprocess.

The llama.cpp backend can abort the whole process on a C++ assert
(GGML_ASSERT) — inside the FastAPI process that kills the entire server and
every connected client. Running generation here means a crash costs one turn
(the parent spawns a fresh worker), not the service.

NOTE: the protocol runs over a localhost TCP socket, NOT stdio — llama_cpp's
import can redirect/close C-level std handles on Windows, corrupting pipes.
Port comes from env LLAMA_WORKER_PORT.

Protocol — JSON lines over the socket:
    → {"ready": true}                 once the model is loaded
    ← {"messages":[...], "max_tokens":N, "temperature":T}
    → {"token":"…"} … {"done":true}   or {"error":"…"}
    ← {"cmd":"cancel"}                polled between tokens mid-generation
"""
from __future__ import annotations

import json
import os
import queue
import site
import socket
import sys
import threading
from pathlib import Path

REQ: queue.Queue = queue.Queue()


def _reader(rfile):
    try:
        for line in rfile:
            try:
                REQ.put(json.loads(line))
            except Exception:
                pass
    except Exception:
        # a force-killed parent often resets rather than cleanly closing —
        # treat any read failure as EOF
        pass
    finally:
        # parent went away (backend restart/crash) — without this the
        # worker blocks on REQ.get() forever and lingers as a 2GB orphan
        REQ.put({"cmd": "_eof"})


def _cuda_dlls():
    # CUDA wheels need the pip-installed nvidia-*-cu12 DLL dirs on PATH
    # before llama.dll loads (RTLD_GLOBAL ignores add_dll_directory).
    try:
        add = [
            str(Path(sp) / "nvidia" / sub / "bin")
            for sp in site.getsitepackages()
            for sub in ("cublas", "cuda_runtime", "cuda_nvrtc")
            if (Path(sp) / "nvidia" / sub / "bin").is_dir()
        ]
        if add:
            os.environ["PATH"] = (os.pathsep.join(add) + os.pathsep +
                                  os.environ.get("PATH", ""))
    except Exception:
        pass


_LLAMA = None


def _llama():
    global _LLAMA
    if _LLAMA is None:
        _cuda_dlls()
        from llama_cpp import Llama
        model = os.getenv("GGUF_MODEL") or str(
            Path(__file__).parent / "models" / "Qwen2.5-3B-Instruct-Q4_K_M.gguf")
        # GPU offload is flaky on a 4GB card shared with desktop/Chrome —
        # try progressively less offload and finally pure CPU so a busy GPU
        # degrades to slow inference instead of a dead tutor.
        pref = int(os.getenv("GGUF_GPU_LAYERS", "-1"))
        for layers in dict.fromkeys([pref, 20, 10, 0]):
            try:
                _LLAMA = Llama(
                    model,
                    n_ctx=int(os.getenv("GGUF_CTX", "2048")),
                    n_threads=8,
                    n_gpu_layers=layers,
                    verbose=False)
                _emit({"log": f"model loaded with n_gpu_layers={layers}"})
                break
            except (ValueError, MemoryError):
                continue
        if _LLAMA is None:
            raise RuntimeError("model failed to load at every offload level "
                               "(GPU OOM or host RAM exhausted)")
    return _LLAMA


_WFILE = None


def _emit(obj):
    _WFILE.write(json.dumps(obj) + "\n")
    _WFILE.flush()


def _generate(req):
    try:
        stream = _llama().create_chat_completion(
            messages=req["messages"],
            max_tokens=req.get("max_tokens", 220),
            temperature=req.get("temperature", 0.4),
            stream=True)
        for chunk in stream:
            try:
                c = REQ.get_nowait()
            except queue.Empty:
                c = None
            if c and c.get("cmd") == "cancel" and (
                    c.get("id") is None or c.get("id") == req.get("id")):
                break                     # cancel for THIS request
            elif c and c.get("cmd") == "_eof":
                REQ.put(c)   # let the main loop see it and exit
                break
            elif c:
                REQ.put(c)  # a fresh request or a cancel for another id
            delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
            tok = delta.get("content")
            if tok:
                _emit({"token": tok})
        _emit({"done": True})
    except Exception as e:  # python-level error → clean report, not abort
        _emit({"error": f"{type(e).__name__}: {e}"})


def main():
    global _WFILE
    port = int(os.environ["LLAMA_WORKER_PORT"])
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    srv.settimeout(60)
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        return  # parent died before connecting — don't linger as an orphan
    srv.settimeout(None)
    rfile = conn.makefile("r", encoding="utf-8")
    _WFILE = conn.makefile("w", encoding="utf-8", buffering=1)
    threading.Thread(target=_reader, args=(rfile,), daemon=True).start()
    try:
        _llama()  # warm the model before declaring ready
        _emit({"ready": True})
    except Exception as e:
        try:
            _emit({"error": f"load: {type(e).__name__}: {e}"})
        except Exception:
            pass
        return
    while True:
        req = REQ.get()
        if not req:
            continue
        if req.get("cmd") == "_eof":
            return   # parent socket closed — exit, don't stay orphaned
        # Drain any backlog and keep only the newest generation request.
        # A cancel kills only requests queued BEFORE it — a request that
        # arrived after the cancel is a fresh turn and must still run.
        # Protocol invariant: every "messages" request ends with a terminal
        # frame — the client's drain thread blocks reading the socket until
        # it sees one, so a silently dropped request wedges the next turn
        # for ~30s.
        try:
            while True:
                nxt = REQ.get_nowait()
                if not nxt:
                    continue
                if nxt.get("cmd") == "_eof":
                    if "messages" in (req or {}):
                        _emit({"done": True})
                    return
                if nxt.get("cmd") == "cancel":
                    # id-scoped: a cancel kills only its own request — a
                    # stale cancel from an old turn must not kill a fresh
                    # one queued after it
                    if "messages" in (req or {}) and (
                            nxt.get("id") is None
                            or nxt.get("id") == req.get("id")):
                        _emit({"done": True})   # killed before it ran
                        req = {"cmd": "cancel"}
                elif "messages" in nxt:
                    if "messages" in (req or {}):
                        _emit({"done": True})   # superseded before it ran
                    req = nxt
                else:
                    req = nxt
        except queue.Empty:
            pass
        if req.get("cmd") == "cancel" or "messages" not in req:
            continue
        _generate(req)


if __name__ == "__main__":
    main()
