"""Pipeline state-machine tests with in-process fake providers.

These test OUR logic — the stop latch, turn cancellation, transcript routing —
not the external services. Real-provider latency/accuracy runs live in
eval_latency.py / eval_grounding.py.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import metrics  # noqa: E402
import pipeline  # noqa: E402


class FakeLLM:
    name = "fake"

    def __init__(self, reply="Winds form when air moves from high to low pressure."):
        self.reply = reply
        self.cancelled = False

    async def stream(self, system, history, question):
        try:
            for tok in self.reply.split():
                await asyncio.sleep(0.01)   # slow enough to allow interruption
                yield tok + " "
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FakeTTS:
    name = "fake"

    async def speak(self, text, language="en"):
        yield b"mp3bytes"


class FakeIO:
    def __init__(self):
        self.msgs, self.chunks = [], []

    async def send(self, m): self.msgs.append(m)
    async def audio(self, b): self.chunks.append(b)


def make(io=None, llm=None):
    io = io or FakeIO()
    s = pipeline.TutorSession(1, 8, "Pressure, Winds", "science",
                              io.send, io.audio,
                              llm=llm or FakeLLM(), tts=FakeTTS())
    return s, io


def states(io):
    return [m["state"] for m in io.msgs if m["type"] == "state"]


def test_stop_latch_ignores_conversation():
    s, io = make()
    asyncio.run(_run(s))
    return


async def _run(s):
    await s.start(say_hello=False)
    await s.stop()
    await s.on_transcript("what is pressure", True)
    await asyncio.sleep(0.05)


def test_conversations_ignored_until_start():
    s, io = make()
    async def run():
        await s.on_transcript("what is pressure", True)
        await asyncio.sleep(0.05)
        assert "thinking" not in states(io), states(io)
    asyncio.run(run())


def test_full_turn_produces_text_and_audio():
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.ask("how are winds formed")
        for _ in range(100):
            if s.state == "listening":
                break
            await asyncio.sleep(0.02)
        assert io.chunks, "no audio emitted"
        assert any(m.get("role") == "bot" for m in io.msgs if m["type"] == "text")
        assert s.history, "turn not recorded in history"
    asyncio.run(run())


def test_stop_during_turn_cancels_generation():
    llm = FakeLLM("one two three four five six seven eight nine ten")
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.ask("explain winds")
        await asyncio.sleep(0.03)   # mid-generation
        await s.stop()
        await asyncio.sleep(0.1)
        assert llm.cancelled, "LLM stream was not cancelled"
        assert s.state == "stopped"
    asyncio.run(run())


def test_interrupt_during_speech_cancels_and_relives():
    llm = FakeLLM("one two three four five six seven eight nine ten")
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.ask("explain winds")
        await asyncio.sleep(0.03)
        await s.interrupt()
        await asyncio.sleep(0.05)
        assert llm.cancelled
        assert s.state == "listening"
    asyncio.run(run())


def test_voice_stop_command_latches():
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("infy stop", True)
        await asyncio.sleep(0.02)
        assert s.state == "stopped"
        await s.on_transcript("what is pressure", True)
        await asyncio.sleep(0.02)
        assert "thinking" not in states(io)
    asyncio.run(run())


def test_metrics_recorded_on_turn():
    metrics.reset()
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.ask("winds?")
        for _ in range(100):
            if s.state == "listening":
                break
            await asyncio.sleep(0.02)
    asyncio.run(run())
    rep = metrics.report()
    assert rep["turns"] >= 1
    assert rep["first_audio_ms"]["p50"] is not None
