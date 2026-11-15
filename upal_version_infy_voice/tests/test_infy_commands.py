"""'Infy …' session commands: repeat / hindi / english (no LLM call)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pipeline  # noqa: E402
from test_pipeline import FakeLLM  # noqa: E402
from test_stage7 import ANSWER, finish_turn, make  # noqa: E402


class CountingLLM(FakeLLM):
    def __init__(self, reply=ANSWER):
        super().__init__(reply)
        self.calls = 0

    async def stream(self, system, history, question):
        self.calls += 1
        async for t in super().stream(system, history, question):
            yield t


def bot(io):
    return " ".join(m["text"] for m in io.msgs
                    if m["type"] == "text" and m["role"] == "bot")


def test_repeat_says_last_answer_without_llm():
    llm = CountingLLM()
    s, io = make(llm=llm)

    async def run():
        await s.start(say_hello=False)
        await s.ask("what is air pressure")
        await finish_turn(s, io)
        hist = len(s.history)
        io.msgs.clear()
        await s.on_transcript("infy repeat", True)
        await finish_turn(s, io)
        assert llm.calls == 1
        assert ANSWER.split()[0] in bot(io) and "areas" in bot(io)
        assert len(s.history) == hist        # a repeat is not a new turn
    asyncio.run(run())


def test_repeat_with_nothing_said_yet():
    s, io = make()

    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("infy dobara bolo", True)
        await finish_turn(s, io)
        assert "nothing to repeat" in bot(io)
    asyncio.run(run())


def test_repeat_ignores_interims_and_duplicate_final():
    llm = CountingLLM()
    s, io = make(llm=llm)

    async def run():
        await s.start(say_hello=False)
        await s.ask("what is air pressure")
        await finish_turn(s, io)
        tid = s._turn_id
        await s.on_transcript("infy repeat", False)
        assert s._turn_id == tid
        await s.on_transcript("infy repeat", True)
        await s.on_transcript("infy repeat", True)   # Chrome re-fire
        assert s._turn_id == tid + 1
    asyncio.run(run())


def test_repeat_ignored_while_stopped():
    s, io = make()

    async def run():
        await s.on_transcript("infy repeat", True)
        assert not any(m["type"] == "text" for m in io.msgs)
    asyncio.run(run())


def test_infy_hindi_switches_and_confirms():
    llm = CountingLLM()
    s, io = make(llm=llm)

    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("infy hindi mein bolo", True)
        await finish_turn(s, io)
        assert s.language == "Hindi"
        assert {"type": "language", "lang": "hi"}.items() <= next(
            m for m in io.msgs if m["type"] == "language").items()
        assert "हिंदी में बात करते" in bot(io)
        await s.on_transcript("infy english", True)
        await finish_turn(s, io)
        assert s.language == "English"
        assert llm.calls == 0
    asyncio.run(run())


def test_language_switch_while_stopped_is_silent():
    s, io = make()

    async def run():
        await s.on_transcript("infy hindi", True)
        assert s.language == "Hindi"
        assert any(m["type"] == "language" for m in io.msgs)
        assert not any(m["type"] == "text" for m in io.msgs)
    asyncio.run(run())


def test_typed_commands_work_with_mic_off():
    s, io = make()

    async def run():
        await s.ask("Infy Hindi", typed=True)
        await finish_turn(s, io)
        assert s.language == "Hindi"
        assert "हिंदी में बात करते" in bot(io)
    asyncio.run(run())


def test_question_mentioning_hindi_is_a_question():
    llm = CountingLLM()
    s, io = make(llm=llm)

    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("infy what is pressure in hindi", True)
        await asyncio.sleep(pipeline.SPLIT_FINAL_S + 0.3)
        await finish_turn(s, io)
        assert llm.calls == 1
        assert not any(m["type"] == "language" for m in io.msgs)
    asyncio.run(run())
