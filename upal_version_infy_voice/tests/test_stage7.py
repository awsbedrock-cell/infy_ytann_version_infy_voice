"""Stage 7 regression tests — stop immediacy, start speed, language policy.

Each test reproduces a fixed failure mode (or locks in a required
behaviour). In-process fakes only; real-provider checks live in
ws_language.py / ws_stopstart.py / e2e/drive.py.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import commands  # noqa: E402
import pipeline  # noqa: E402
from test_pipeline import FakeIO, FakeLLM, FakeTTS, states  # noqa: E402

ANSWER = "Low pressure pulls air in from high pressure areas around it."


def make(llm=None, store=None, tts=None):
    io = FakeIO()
    s = pipeline.TutorSession(1, 8, "Pressure, Winds", "science",
                              io.send, io.audio, llm=llm or FakeLLM(ANSWER),
                              tts=tts or FakeTTS(), store=store)
    return s, io


async def finish_turn(s, io):
    for _ in range(200):
        if any(m["type"] == "turn_end" and m["turn"] == s._turn_id
               for m in io.msgs):
            break
        await asyncio.sleep(0.01)
    s.playback_done(s._turn_id)
    for _ in range(100):
        if s.state == "listening":
            return
        await asyncio.sleep(0.01)


# --------------------------------------------------------------------------
# language detection
# --------------------------------------------------------------------------

def test_detect_language_english_questions():
    for q in ["what is air pressure", "how are winds formed",
              "why do cyclones spin", "tell me about the storm surge",
              "what causes a thunderstorm"]:
        assert pipeline.detect_language(q) == "en", q


def test_detect_language_devanagari():
    for q in ["हवा कैसे चलती है", "दबाव क्या होता है",
              "चक्रवात क्यों बनता है"]:
        assert pipeline.detect_language(q) == "hi", q


def test_detect_language_romanized_hindi():
    assert pipeline.detect_language("hawa kaise chalti hai") == "hi"
    assert pipeline.detect_language("mujhe dabaav samjhao") == "hi"


def test_detect_language_ambiguous_keeps():
    for q in ["ok", "thanks", "yes", "cyclone", "hm"]:
        assert pipeline.detect_language(q) is None, q


# --------------------------------------------------------------------------
# loose stop (echo-garbled "infy stop" while speaking)
# --------------------------------------------------------------------------

def test_loose_stop_matches():
    for t in ["infy please stop now", "in face stop", "इन्फी रुक जाओ",
              "infy the wind is blowing stop talking", "stop infy"]:
        assert commands.loose_stop(t), t


def test_loose_stop_rejects():
    for t in ["what is pressure", "the wind stops at the coast",
              "infy what is a cyclone", "how do storms stop"]:
        assert not commands.loose_stop(t), t


def test_strict_vs_loose_stop():
    # strict already catches clean commands; loose adds echo-garbled ones
    assert commands.parse_command("infy stop")[0] == "stop"
    assert commands.parse_command("infy the wind is blowing stop")[0] != "stop"
    assert commands.loose_stop("infy the wind is blowing stop")


# --------------------------------------------------------------------------
# stop immediacy + state hygiene
# --------------------------------------------------------------------------

def test_stop_clears_question_dedup():
    """After stop, re-asking the SAME question must produce a new turn —
    the in-flight-dup window used to drop it for 10s."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("what is air pressure", True)
        await asyncio.sleep(0.05)
        await s.stop()
        turns_before = s._turn_id
        await s.start(say_hello=False)
        await s.on_transcript("what is air pressure", True)
        await asyncio.sleep(0.05)
        assert s._turn_id > turns_before, "re-asked question was deduped away"
    asyncio.run(run())


def test_interrupt_clears_voice_q():
    """Barge-in then repeating the same question must re-ask it."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("explain cyclones", True)
        await asyncio.sleep(0.05)
        await s.interrupt()
        await asyncio.sleep(1.3)          # past the post-interrupt echo tail
        await s.on_transcript("explain cyclones", True)
        await asyncio.sleep(0.05)
        assert "thinking" in states(io) or s.state in ("thinking", "speaking")
    asyncio.run(run())


def test_stop_clears_quiz():
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        s._quiz = {"q": "q?", "a": "a"}
        await s.on_transcript("infy stop", True)
        assert s._quiz is None
    asyncio.run(run())


def test_stop_start_cycles_10x():
    """10 consecutive stop/start cycles: every one must reach stopped then
    listening, with no wedged turn."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        for i in range(10):
            await s.on_transcript("infy stop", True)
            assert s.state == "stopped", f"cycle {i}: not stopped"
            await s.on_transcript("infy start", True)
            assert s.started.is_set(), f"cycle {i}: did not restart"
    asyncio.run(run())


def test_stop_then_immediate_start_answers():
    """stop mid-generation -> immediate start -> new question is answered."""
    llm = FakeLLM("one two three four five six seven eight nine ten")
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.ask("explain winds in detail")
        await asyncio.sleep(0.03)          # mid-generation
        await s.stop()
        await s.start(say_hello=False)
        await s.ask("what is pressure")
        await finish_turn(s, io)
        assert s.state == "listening", s.state
        bots = [m["text"] for m in io.msgs
                if m["type"] == "text" and m["role"] == "bot"]
        assert bots, "no answer after stop->start->ask"
    asyncio.run(run())


def test_loose_stop_reaches_server_path():
    """A garbled stop transcript while speaking must latch stopped.
    state is set directly — the loose rule only keys on state=speaking,
    and timing it against a real turn would make the test flaky."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.ask("explain cyclones in detail")
        await asyncio.sleep(0.05)
        s.state = "speaking"   # simulate mid-playback
        await s.on_transcript("infy the pressure is high stop now", True)
        assert s.state == "stopped", s.state
    asyncio.run(run())


def test_loose_stop_ignored_while_listening():
    """The loose rule must NOT fire when Infy is silent — a normal question
    mentioning 'stop' with a wake word is a question, not a command."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("infy when do storms become dangerous", True)
        await asyncio.sleep(0.05)
        assert s.state != "stopped"
        assert any(m.get("text") for m in io.msgs
                   if m["type"] == "text" and m["role"] == "you"), \
            "question should have spawned a turn"
    asyncio.run(run())


def test_no_audio_after_stop():
    """Once stop() returns, no further audio frames may be emitted for the
    cancelled turn."""
    llm = FakeLLM("one two three four five six seven eight nine ten "
                  "eleven twelve thirteen fourteen fifteen sixteen")
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.ask("explain winds in detail")
        await asyncio.sleep(0.03)
        await s.stop()
        n = len(io.chunks)
        await asyncio.sleep(0.3)
        assert len(io.chunks) == n, "audio frames kept arriving after stop"
    asyncio.run(run())


# --------------------------------------------------------------------------
# per-turn language policy
# --------------------------------------------------------------------------

def test_language_follows_current_utterance():
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        assert s.language == "English"
        await s.ask("हवा कैसे चलती है")
        assert s.language == "Hindi"
        await s.ask("what is air pressure")   # switch back intentionally
        assert s.language == "English"
    asyncio.run(run())


def test_ambiguous_keeps_session_language():
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        s.language = "Hindi"
        await s.ask("ok")
        assert s.language == "Hindi"
        await s.ask("hmm interesting")
        assert s.language == "Hindi"
    asyncio.run(run())


def test_old_hindi_history_does_not_override_english():
    """A clearly-English question after Hindi history must get an English
    system prompt — the original drift bug."""
    s, io = make()
    async def run():
        await s.start(say_hello=False)
        s.history += [{"role": "user", "content": "हवा क्या है"},
                      {"role": "assistant", "content": "हवा वायु है।"}]
        await s.ask("what is air pressure")
        assert s.language == "English"
        sys_prompt = s._system([])
        assert "Reply ONLY in English" in sys_prompt
    asyncio.run(run())


def test_system_prompt_language_rule_last():
    p = pipeline._SYS.format(grade=8, title="T", subject="science",
                             language="English", evidence="")
    assert p.rstrip().endswith("your reply language.")
    assert "Reply ONLY in English" in p


def test_tts_voice_follows_sentence_script():
    """A Devanagari sentence must go to the Hindi voice even under an
    English session — spoken language matches generated text."""
    langs = []

    class RecTTS(FakeTTS):
        async def speak(self, text, language="en"):
            langs.append(language)
            yield b"mp3"

    class MixedLLM(FakeLLM):
        async def stream(self, system, history, question):
            yield "It is simple. "
            yield "हवा ऊपर उठती है. "
            yield "That is all."

    s, io = make(llm=MixedLLM(), tts=RecTTS())
    async def run():
        await s.start(say_hello=False)
        s.language = "English"
        await s.ask("what is air pressure")
        for _ in range(200):
            if any(m["type"] == "turn_end" for m in io.msgs):
                break
            await asyncio.sleep(0.01)
        assert "hi" in langs, f"Devanagari sentence not voiced hi: {langs}"
        assert "en" in langs, f"English sentence not voiced en: {langs}"
    asyncio.run(run())


def test_empty_reply_language():
    s, io = make()

    class SilentLLM:
        name = "silent"

        async def stream(self, system, history, question):
            return
            yield

    s.llm = SilentLLM()
    async def run():
        await s.start(say_hello=False)
        s.language = "Hindi"
        await s.ask("हवा कैसे चलती है")
        for _ in range(200):
            if any(m["type"] == "turn_end" for m in io.msgs):
                break
            await asyncio.sleep(0.01)
        bots = [m["text"] for m in io.msgs
                if m["type"] == "text" and m["role"] == "bot"]
        assert any("माफ़" in b for b in bots), bots
    asyncio.run(run())


# --------------------------------------------------------------------------
# turn bookkeeping
# --------------------------------------------------------------------------

def test_no_duplicate_turns_on_rapid_start_stop():
    """Rapid stop/start must not spawn extra turns or greetings."""
    s, io = make()
    async def run():
        for _ in range(6):
            await s.stop()
            await s.start(say_hello=False)
        await asyncio.sleep(0.1)
        # greeted only once ever, no stray turns created by cycling
        assert sum(1 for m in io.msgs if m.get("type") == "turn_end") <= 1
    asyncio.run(run())


def test_metrics_cancelled_counts_stops():
    import metrics
    metrics.reset()
    llm = FakeLLM("one two three four five six seven eight nine ten")
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        for _ in range(3):
            await s.ask("explain winds")
            await asyncio.sleep(0.03)
            await s.stop()
            await s.start(say_hello=False)
            await asyncio.sleep(0.05)
    asyncio.run(run())
    assert metrics.report()["cancelled_turns"] >= 3


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
