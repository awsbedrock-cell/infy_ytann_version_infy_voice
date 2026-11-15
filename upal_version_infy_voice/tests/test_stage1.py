"""Stage 1 regression tests: one test per reproduced failure mode.

Each test fails on main (285c5ad) and passes on stage1-stabilize.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import commands  # noqa: E402
import pipeline  # noqa: E402
from test_pipeline import FakeIO, FakeLLM, FakeTTS, states  # noqa: E402

ANSWER = "Low pressure areas pull air in from high pressure areas around them."


def make(llm=None, store=None):
    io = FakeIO()
    s = pipeline.TutorSession(1, 8, "Pressure, Winds", "science",
                              io.send, io.audio, llm=llm or FakeLLM(ANSWER),
                              tts=FakeTTS(), store=store)
    return s, io


async def finish_turn(s, io, played=True):
    """Wait until the server sent turn_end, then report playback done."""
    for _ in range(200):
        if any(m["type"] == "turn_end" and m["turn"] == s._turn_id for m in io.msgs):
            break
        await asyncio.sleep(0.01)
    if played:
        s.playback_done(s._turn_id)
    for _ in range(100):
        if s.state == "listening":
            return
        await asyncio.sleep(0.01)


def asks(io):
    return [m["text"] for m in io.msgs if m["type"] == "text" and m["role"] == "you"]


def test_speaking_lasts_until_client_playback_done():
    """Mic must stay gated while the browser is still playing queued audio."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.ask("what is low pressure")
        for _ in range(200):
            if any(m["type"] == "turn_end" for m in io.msgs):
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        assert s.state == "speaking", "listening before playback finished"
        s.playback_done(s._turn_id)
        await asyncio.sleep(0.05)
        assert s.state == "listening"
    asyncio.run(run())


def test_echo_while_audible_is_dropped():
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.ask("what is low pressure")
        await finish_turn(s, io)
        # recognizer delivers Infy's own words right after playback
        await s.on_transcript("pull air in from high pressure", True)
        await asyncio.sleep(0.05)
        assert asks(io) == ["what is low pressure"], asks(io)
    asyncio.run(run())


def test_followup_repeating_answer_words_not_dropped_after_tail():
    """Old 25s text window dropped real follow-ups that reused 4 words."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.ask("what is low pressure")
        await finish_turn(s, io)
        s._echo_until = 0  # echo tail elapsed
        await s.on_transcript("why do low pressure areas pull air", True)
        await asyncio.sleep(0.05)
        assert "why do low pressure areas pull air" in asks(io), asks(io)
    asyncio.run(run())


def test_ten_consecutive_questions_all_answered():
    s, io = make()
    qs = [f"question number {i} about winds" for i in range(10)]
    async def run():
        await s.start(say_hello=False)
        for q in qs:
            s._echo_until = 0
            await s.on_transcript(q, True)
            await finish_turn(s, io)
        assert asks(io) == qs
        assert len(s.history) == 20
    asyncio.run(run())


def test_hindi_finals_are_not_duplicates_of_each_other():
    """_norm used to strip Devanagari → every Hindi final normalized to ''."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("हवा का दबाव क्या है", True)
        await finish_turn(s, io)
        s._echo_until = 0
        await s.on_transcript("चक्रवात कैसे बनते हैं", True)
        await asyncio.sleep(0.05)
        assert asks(io) == ["हवा का दबाव क्या है", "चक्रवात कैसे बनते हैं"], asks(io)
    asyncio.run(run())


def test_duplicate_final_is_one_turn():
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("how are winds formed", True)
        await s.on_transcript("How are winds formed?", True)
        await asyncio.sleep(0.05)
        assert asks(io) == ["how are winds formed"]
    asyncio.run(run())


def test_stale_interrupt_for_old_turn_is_ignored():
    llm = FakeLLM("one two three four five six seven eight nine ten. " * 3)
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.ask("first")
        old = s._turn_id
        await s.ask("second")
        await asyncio.sleep(0.03)
        await s.interrupt(turn=old)
        assert s._turn and not s._turn.done(), "new turn killed by stale interrupt"
    asyncio.run(run())


def test_every_message_carries_turn_id():
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.ask("winds")
        await finish_turn(s, io)
        for m in io.msgs:
            assert "turn" in m, m
    asyncio.run(run())


def test_typed_question_answered_while_mic_off():
    s, io = make()
    async def run():
        await s.ask("what is pressure", typed=True)
        await finish_turn(s, io)
        assert any(m.get("role") == "bot" for m in io.msgs if m["type"] == "text")
    asyncio.run(run())


def test_reconnect_resumes_history_without_regreeting():
    pipeline._SESSIONS.clear()
    st = pipeline.resume_store("abc", 1)
    s, io = make(store=st)
    async def run():
        await s.start()                       # greets once
        await finish_turn(s, io)
        await s.ask("what is pressure")
        await finish_turn(s, io)
    asyncio.run(run())
    st2 = pipeline.resume_store("abc", 1)
    assert st2 is st and len(st2["history"]) == 2
    s2, io2 = make(store=st2)
    async def run2():
        await s2.start()
        await asyncio.sleep(0.1)
        assert not any(m["type"] == "text" for m in io2.msgs), "greeted again"
    asyncio.run(run2())
    # different chapter → fresh conversation
    assert pipeline.resume_store("abc", 2)["history"] == []


def test_error_text_never_leaks_exception_message():
    class Boom:
        name = "boom"
        async def stream(self, *a):
            raise RuntimeError("https://x?key=SECRET")
            yield ""
    s, io = make(llm=Boom())
    async def run():
        await s.start(say_hello=False)
        await s.ask("x")
        await asyncio.sleep(0.1)
    asyncio.run(run())
    assert not any("SECRET" in str(m) for m in io.msgs)
    assert "error" in states(io)


def test_empty_llm_reply_speaks_fallback_line():
    """An empty LLM answer used to end the turn in silence."""
    s, io = make(llm=FakeLLM(""))
    async def run():
        await s.start(say_hello=False)
        await s.ask("what is a storm surge")
        await finish_turn(s, io)
    asyncio.run(run())
    bot = [m["text"] for m in io.msgs if m["type"] == "text" and m["role"] == "bot"]
    assert bot == [pipeline._EMPTY_REPLY["en"]] and io.chunks


def test_fallback_llm_answers_when_primary_fails():
    import providers

    class Down:
        name = "down"
        async def stream(self, *a):
            raise RuntimeError("HTTP 429")
            yield ""
    llm = providers.FallbackLLM(Down(), FakeLLM(ANSWER))
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.ask("what is low pressure")
        await finish_turn(s, io)
    asyncio.run(run())
    bot = " ".join(m["text"] for m in io.msgs if m["type"] == "text" and m["role"] == "bot")
    assert "Low pressure" in bot
    assert "error" not in states(io)


def test_devanagari_commands():
    assert commands.parse_command("इन्फी रुको")[0] == "stop"
    assert commands.parse_command("इंफी स्टॉप")[0] == "stop"
    assert commands.parse_command("इन्फी शुरू करो")[0] == "start"
    assert commands.parse_command("बस करो यार")[0] is None   # no wake word
    assert commands.parse_command("इन्फी हवा क्या है") == (None, "हवा क्या है")
