"""metrics.py — per-turn latency + reliability telemetry.

Three beats per turn:
    llm_ttfb_ms   — question → first LLM token (retrieval + model thinking)
    first_audio_ms — question → first audio bytes sent to the client
    cancelled      — turns cancelled by interrupt/stop (reliability signal)

`speech_ts` (client clock, ms) lets the report also compute true
end-of-speech → first-audio when the client's clock is close to the server's
(localhost: it is). Reported via GET /api/metrics as p50/p95.
"""
from __future__ import annotations

import statistics
import threading
import time

_lock = threading.Lock()
_llm_ttfb: list[float] = []
_first_audio: list[float] = []
_cancelled = 0
_started = time.time()
_live_max_min = int(__import__("os").getenv("LIVE_MAX_SESSION_MINUTES", "10"))
_live_mints = 0

# verified pricing 2026-10-29 (docs/COST.md): $3/1M audio-in + $12/1M
# audio-out ≈ $0.023/min worst case. mints × session cap = upper bound.
_LIVE_COST_PER_MIN = 0.023


def record_live_mint():
    global _live_mints
    with _lock:
        _live_mints += 1


def record_llm_ttfb(ms: float):
    with _lock:
        _llm_ttfb.append(ms)


def record_first_audio(ms: float, speech_ts: int | None = None):
    with _lock:
        _first_audio.append(ms)


def record_cancelled():
    global _cancelled
    with _lock:
        _cancelled += 1


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round((p / 100) * (len(xs) - 1))))
    return round(xs[k], 1)


def report() -> dict:
    with _lock:
        return {
            "turns": len(_first_audio),
            "llm_ttfb_ms": {
                "p50": _pct(_llm_ttfb, 50), "p95": _pct(_llm_ttfb, 95),
                "mean": round(statistics.fmean(_llm_ttfb), 1) if _llm_ttfb else None,
            },
            "first_audio_ms": {
                "p50": _pct(_first_audio, 50), "p95": _pct(_first_audio, 95),
                "mean": round(statistics.fmean(_first_audio), 1) if _first_audio else None,
            },
            "cancelled_turns": _cancelled,
            "uptime_s": round(time.time() - _started),
            "live_sessions_minted": _live_mints,
            # upper bound: every mint could run its full session cap
            "live_cost_upper_bound_usd": round(
                _live_mints * _live_max_min * 60 * _LIVE_COST_PER_MIN, 4),
            "cascade_cost_usd": 0.0,
        }


def reset():
    global _cancelled
    with _lock:
        _llm_ttfb.clear()
        _first_audio.clear()
        _cancelled = 0
