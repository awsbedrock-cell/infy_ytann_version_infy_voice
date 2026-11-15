"""Deterministic command parsing — the contract that 'infy start'/'infy stop'
must be reliable and normal conversation must NOT trigger them."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import commands  # noqa: E402


def cmd(t):
    return commands.parse_command(t)[0]


def test_infy_start_variants():
    assert cmd("infy start") == "start"
    assert cmd("Infy Start") == "start"
    assert cmd("ok infy start") == "start"
    assert cmd("hey infy, start") == "start"
    assert cmd("infy shuru karo") == "start"
    assert cmd("infy chalu") == "start"


def test_infy_stop_variants():
    assert cmd("infy stop") == "stop"
    assert cmd("Infy Stop") == "stop"
    assert cmd("infy ruk jao") == "stop"
    assert cmd("infy band karo") == "stop"
    assert cmd("stop infy") == "stop"
    assert cmd("infy please stop") == "stop"
    assert cmd("infy chup ho jao") == "stop"


def test_no_false_positives_without_wake_word():
    for phrase in [
        "tell me about bus stops", "what is a stop sign",
        "bas kitne page hain", "can you stop for a second",
        "the bus stop near school", "how to start a fire",
        "when did the cyclone start", "stopwatch kaise kaam karta hai",
    ]:
        assert cmd(phrase) is None, phrase


def test_bare_command_words_fire():
    # real-mic evidence: STT drops the wake word ("infy start" arrives as
    # bare "start"). A whole utterance that is only a control verb/fillers
    # is never a real question — it is the command.
    for phrase, want in [
        ("stop", "stop"), ("start", "start"), ("ruk jao", "stop"),
        ("band karo", "stop"), ("bas", "stop"), ("please stop talking", "stop"),
        ("continue", "start"), ("resume", "start"), ("wait", "stop"),
        ("shuru", "start"), ("chalu", "start"), ("suno", "start"),
        ("stop it", "stop"), ("please start", "start"),
    ]:
        assert cmd(phrase) == want, phrase


def test_stt_mishears_of_wake_word():
    assert cmd("infi stop") == "stop"
    assert cmd("enfee start") == "start"


def test_wake_word_remainder_is_bare_command():
    # observed live: "continue Infi continue" -> stripped to "continue
    # continue" which is itself a start command, not a question
    assert cmd("continue infi continue") == "start"
    assert cmd("infi continue") == "start"
    assert cmd("infi ruk jao") == "stop"


def test_wake_word_question_is_not_a_command():
    c, cleaned = commands.parse_command("infy what is pressure")
    assert c is None
    assert cleaned == "what is pressure"


def test_command_only_matches_with_wake():
    # "infinitely start" must not count as wake + start
    assert cmd("infinitely start") is None
    assert cmd("infinity stop") is None


def test_quiz_command():
    for phrase in ["quiz me", "infy quiz me", "test me", "practice",
                   "give me a quiz", "sawaal poocho", "mujhe test lo",
                   "प्रश्न पूछो", "imtihaan"]:
        assert cmd(phrase) == "quiz", phrase


def test_quiz_no_false_positives():
    for phrase in [
        "what is the test of solubility", "tell me about the quiz show",
        "what does practice mean in sports", "how to test air pressure",
        "questions about practice questions are in this chapter",
    ]:
        assert cmd(phrase) is None, phrase


# --- "infy repeat" / "infy hindi" / "infy english" -------------------------

def test_infy_repeat_variants():
    for t in ("infy repeat", "Infy, repeat that", "hey infy say that again",
              "infy dobara bolo", "infy phir se bolo", "इन्फी दोबारा बोलो",
              "इन्फी फिर से", "in fee repeat", "infy once more please"):
        assert cmd(t) == "repeat", t


def test_infy_language_variants():
    for t in ("infy hindi", "Infy Hindi", "infy hindi mein bolo",
              "infy speak in hindi", "infy in hindi please", "इन्फी हिंदी में बोलो",
              "infy ab hindi mein baat karo"):
        assert cmd(t) == "hindi", t
    for t in ("infy english", "Infy English", "infy english mein bolo",
              "infy speak in english", "इन्फी इंग्लिश", "इन्फी अंग्रेज़ी में बोलो"):
        assert cmd(t) == "english", t


def test_repeat_and_language_need_wake_and_whole_utterance():
    for t in ("repeat", "hindi", "english", "say that again",
              "dobara bolo", "what is hindi",
              "infy what is pressure in hindi", "infy how do you say wind in english",
              "infy repeat the definition of humidity",
              "infy english grammar kya hai", "infy why does the wind repeat"):
        c, _ = commands.parse_command(t)
        assert c not in ("repeat", "hindi", "english"), t


def test_stop_and_start_still_win():
    assert cmd("infy stop repeating") == "stop"
    assert cmd("infy hindi mein shuru karo") == "start"
