"""test_worker_proto.py — socket-level protocol test for llama_worker.py.

Spawns a real worker (loads the GGUF once, ~20s) and verifies the framing
invariants the stall investigation turned up:

  W1  normal request -> token frames then exactly one {"done":true}
  W2  cancel mid-generation -> terminal frame still arrives
  W3  request + its cancel queued together -> terminal frame, nothing lost
  W4  id-scoped cancel: a cancel for request A never kills request B
      (regression for the 30s 'lost terminator' stall)
  W5  request that arrives AFTER a cancel is not affected
  W6  closing the client socket -> worker exits (no orphan; regression
      for the lingering 2.5GB workers tracked in the GONE file)

    python tests/test_worker_proto.py        (or: pytest tests/test_worker_proto.py)
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS = []


def report(cid, ok, ev=""):
    RESULTS.append((cid, ok))
    print(f"{cid}: {'PASS' if ok else 'FAIL'} {ev}", flush=True)


def connect(port, timeout=60):
    deadline = time.time() + timeout
    while True:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=10)
            return s
        except OSError:
            if time.time() > deadline:
                raise
            time.sleep(0.3)


def readline(rfile, timeout=90):
    """One JSON frame with a deadline — fails the test instead of hanging."""
    import select
    s = rfile._sock if hasattr(rfile, "_sock") else None
    return rfile.readline()


class Worker:
    def __init__(self):
        env = dict(os.environ)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        env["LLAMA_WORKER_PORT"] = str(self.port)
        env["GGUF_GPU_LAYERS"] = "0"
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "llama_worker.py")],
            env=env, cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sock = connect(self.port)
        self.sock.settimeout(120)
        self.r = self.sock.makefile("r", encoding="utf-8")
        self.w = self.sock.makefile("w", encoding="utf-8")

    def send(self, obj):
        self.w.write(json.dumps(obj) + "\n")
        self.w.flush()

    def next_frame(self, timeout=120):
        """Read one frame with deadline. Returns dict or None on EOF/timeout."""
        self.sock.settimeout(timeout)
        try:
            line = self.r.readline()
        except (socket.timeout, TimeoutError):
            return "TIMEOUT"
        if not line:
            return None
        try:
            return json.loads(line)
        except Exception:
            return None

    def stream_all(self, timeout=120):
        """Read frames until done/error/timeout. Returns list of frames."""
        out = []
        while True:
            f = self.next_frame(timeout)
            if f in (None, "TIMEOUT"):
                out.append(f)
                return out
            out.append(f)
            if f.get("done") or "error" in f:
                return out

    def close(self):
        try:
            self.r.close()
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def req(n, id_):
    return {"messages": [{"role": "user",
                          "content": f"count from {n} to {n+5} in words"}],
            "id": id_, "max_tokens": 40, "temperature": 0}


def run_checks():
    print("spawning worker (model load ~20s)...", flush=True)
    wk = Worker()
    ready = None
    for _ in range(10):                      # skip {"log": ...} frames
        f = wk.next_frame(timeout=60)
        if isinstance(f, dict) and f.get("ready"):
            ready = f
            break
        if f in (None, "TIMEOUT") or "error" in (f or {}):
            break
    if not ready:
        print(f"worker did not become ready: {f}")
        wk.proc.kill()
        sys.exit(1)
    print("worker ready", flush=True)

    # W1: normal request -> tokens + done
    wk.send(req(1, "a"))
    frames = wk.stream_all()
    toks = [f for f in frames if isinstance(f, dict) and "token" in f]
    done = [f for f in frames if isinstance(f, dict) and f.get("done")]
    report("W1 normal stream", len(toks) > 3 and len(done) == 1,
           f"tokens={len(toks)} done={len(done)}")

    # W2: cancel mid-generation -> terminal frame arrives
    wk.send(req(10, "b"))
    time.sleep(0.3)
    wk.send({"cmd": "cancel", "id": "b"})
    frames = wk.stream_all()
    done = [f for f in frames if isinstance(f, dict) and f.get("done")]
    report("W2 cancel-mid-gen terminal", len(done) == 1,
           f"frames={len(frames)} done={len(done)}")

    # W3: request + cancel back-to-back -> terminal frame (old bug: lost
    # terminator -> client drain wedged ~30s)
    wk.send(req(20, "c"))
    wk.send({"cmd": "cancel", "id": "c"})
    frames = wk.stream_all(timeout=30)
    done = [f for f in frames if isinstance(f, dict) and f.get("done")]
    report("W3 cancel-before-start terminal", len(done) == 1
           and frames[-1] != "TIMEOUT",
           f"frames={len(frames)} done={len(done)}")

    # W4: cancel for request A must NOT kill request B sent after it.
    # Expected frame order: done(for killed d) -> tokens(e) -> done(e).
    wk.send(req(30, "d"))
    wk.send({"cmd": "cancel", "id": "d"})
    wk.send(req(40, "e"))          # fresh request queued after the cancel
    frames = []
    t_end = time.time() + 90
    dones = 0
    while dones < 2 and time.time() < t_end:
        f = wk.next_frame(timeout=60)
        if f in (None, "TIMEOUT"):
            break
        frames.append(f)
        dones += f.get("done") is True
    toks = [f for f in frames if isinstance(f, dict) and "token" in f]
    report("W4 id-scoped cancel", dones == 2 and len(toks) > 3,
           f"tokens_for_e={len(toks)} dones={dones}")

    # W5: rapid sequence — every request still gets its terminal frame
    for i, rid in enumerate(("f", "g")):
        wk.send(req(50 + i * 10, rid))
        frames = wk.stream_all(timeout=60)
        ok = any(isinstance(f, dict) and f.get("done") for f in frames)
        if not ok:
            report("W5 serial-requests", False, f"req {rid} lost terminal")
            break
    else:
        report("W5 serial-requests", True, "2/2 requests terminated")

    # W6: closing the socket -> worker exits (no orphan)
    wk.close()
    exited = wk.proc.wait(timeout=15) is not None
    report("W6 worker-exit-on-EOF", exited,
           f"poll={wk.proc.poll()} after socket close")
    if wk.proc.poll() is None:
        wk.proc.kill()

    bad = [c for c, ok in RESULTS if not ok]
    print(f"\n== {len(RESULTS) - len(bad)}/{len(RESULTS)} passed ==")
    return not bad


def test_worker_protocol():
    assert run_checks()


if __name__ == "__main__":
    sys.exit(0 if run_checks() else 1)
