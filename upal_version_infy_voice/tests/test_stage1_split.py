"""Split-final merge and voice-pause regressions (simulated transcripts)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stage1 import make, finish_turn, asks  # noqa: E402


class SlowLLM:
    name = "slow"

    def __init__(self, delay=0.3):
        self.delay, self.qs = delay, []

    async def stream(self, system, history, question):
        self.qs.append(question)
        await asyncio.sleep(self.delay)
        yield "Air pressure is the push of air on things around it."


def test_split_final_is_merged_into_one_question():
    """Chrome cut "what is" / "air pressure" into two finals: answer the
    whole question, not just its tail."""
    llm = SlowLLM()
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("what is", True)
        await asyncio.sleep(0.05)
        await s.on_transcript("air pressure", True)
        await finish_turn(s, io)
        assert llm.qs[-1] == "what is air pressure", llm.qs
    asyncio.run(run())


def test_new_question_after_answer_started_is_not_merged():
    llm = SlowLLM(delay=0.01)
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("what is air pressure", True)
        await finish_turn(s, io)
        s._echo_until = 0
        await s.on_transcript("how are cyclones formed", True)
        await finish_turn(s, io)
        assert llm.qs == ["what is air pressure", "how are cyclones formed"], llm.qs
    asyncio.run(run())


def test_split_merge_window_expires():
    llm = SlowLLM(delay=1.0)
    s, io = make(llm=llm)
    async def run():
        await s.start(say_hello=False)
        await s.on_transcript("what is", True)
        s._voice_q = (s._voice_q[0], s._voice_q[1] - 10)  # long pause
        await s.on_transcript("a cyclone", True)
        await asyncio.sleep(0.05)
        assert llm.qs[-1] == "a cyclone", llm.qs
        s._cancel_turn()
    asyncio.run(run())


def test_voice_stop_then_voice_start_resumes_without_regreeting():
    s, io = make()
    async def run():
        await s.start(say_hello=True)
        await finish_turn(s, io)
        bot = lambda: [m for m in io.msgs if m["type"] == "text" and m["role"] == "bot"]
        n_greet = len(bot())
        await s.on_transcript("infy stop", True)
        assert s.state == "stopped"
        await s.on_transcript("what is pressure", True)   # ignored while paused
        await s.on_transcript("infy start", True)
        await asyncio.sleep(0.1)
        assert s.state == "listening"
        assert len(bot()) == n_greet, "re-greeted after voice resume"
        assert asks(io) == []
    asyncio.run(run())
