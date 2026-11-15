"""pipeline.py — the tutor's turn engine, independent of transports.

State machine:
    stopped → (start) → listening ⇄ thinking ⇄ speaking
    any state → (stop) → stopped   (latched: voice ignored until start)
    thinking/speaking → (interrupt) → listening, in-flight turn cancelled

A "turn" = one asyncio task: retrieve → LLM stream → sentence chunker →
TTS stream → audio callbacks → wait for the CLIENT to finish playing. Every
outbound message carries the turn id so the client can drop anything that
belongs to a cancelled turn. Interruption/stop cancels the task — real
cancellation, not just muted playback.

"speaking" lasts until the browser reports playback finished (or a
duration-based fallback timeout) — not merely until the server has finished
*sending* audio. Ending it early reopened the mic while Infy's voice was
still coming out of the speakers, which is how Infy heard itself.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

import commands
import metrics
import providers
import rag

log = logging.getLogger("infy")

_SYS = (
    "You are Infy, a warm, playful voice tutor for a Grade {grade} student, "
    "speaking out loud in a live conversation.\n"
    "Rules:\n"
    "- Answer directly and briefly first — 2-4 spoken sentences, like a "
    "favorite teacher explaining out loud.\n"
    "- Plain spoken words only — no markdown, bullets, symbols, or anything "
    "a voice cannot say. No URLs.\n"
    "- Never narrate your thinking, planning, or what you are about to say — "
    "only the final spoken answer.\n"
    "- Ground answers in the RETRIEVED CHAPTER PASSAGES below; never invent "
    "chapter facts.\n"
    "- If the question is outside this chapter: give a brief, correct answer "
    "in one sentence, say it is beyond this chapter, then add one sentence "
    "linking back to something in this chapter.\n"
    "- Greet only once at the start; do not end every reply with a question.\n"
    "- If the student's question contains a wrong premise, say so FIRST and "
    "clearly ('actually, no — ...'), then give the correct explanation. Do "
    "not agree with a wrong premise.\n"
    "- If the student's question is ambiguous, ask one short clarifying "
    "question.\n"
    "- When explaining a process or calculation, teach it step by step out "
    "loud ('first… then… finally…'), one short sentence per step.\n"
    "\nCHAPTER: {title} (Grade {grade} {subject})\n"
    "{evidence}\n"
    "\nLANGUAGE RULE (most important): Reply ONLY in {language}. Earlier "
    "messages in this conversation may be in another language — never let "
    "them change your reply language."
)

_GREET = (
    "Say hi in one short, plain sentence — then stop and wait. "
    "Do not list topics, do not suggest questions."
)

# spoken when the LLM returns nothing — never leave the student in silence
_EMPTY_REPLY = {
    "en": "Sorry, I lost my words there. Could you ask that again?",
    "hi": "माफ़ करना, मैं जवाब नहीं दे पाई। क्या आप फिर से पूछेंगे?",
}

# "infy repeat" / "infy hindi" / "infy english" — answered without the LLM
_SESSION_CMDS = ("repeat", "hindi", "english")
_LANG_ACK = {
    "en": "Okay, I'll speak in English now.",
    "hi": "ठीक है, अब हिंदी में बात करते हैं।",
}
_NOTHING_TO_REPEAT = {
    "en": "There's nothing to repeat yet — ask me a question!",
    "hi": "अभी दोहराने के लिए कुछ नहीं है — कोई सवाल पूछो!",
}


async def _canned_stream(text: str):
    yield text


# seconds after playback ends during which a transcript is still checked
# against Infy's recent words (speaker echo tail / recognizer lag)
ECHO_TAIL_S = 2.0
SPLIT_FINAL_S = 2.5
# edge-tts mp3 is 48 kbit/s → 6000 bytes per second of audio
_MP3_BYTES_PER_S = 6000
# max silence between streamed sentences before the turn is declared wedged
_LLM_STALL_S = 40.0
_SESSION_TTL_S = 30 * 60
_SESSIONS: dict[str, dict] = {}


_DEVA = re.compile(r"[ऀ-ॿ]")

# Romanized-Hindi function words that are never English words — used to
# detect Hindi utterances that STT transcribed into Latin script. Kept
# deliberately high-precision: ambiguous single hits leave the session
# language untouched instead of forcing a switch.
_HI_LEX = set("""
kya kaise kyon kyu kahan kab kaun kis kise hai hain nahi mein mujhe tumhe
ko ki ka ke aur se par batao bataiye samjhao samjha matlab kitna kitne kitni
kyunki isliye bahut zyada thoda accha theek chahiye hota hoti hote gaya
raha rahi wala wali karo karna bolo bol bata dijiye abhi phir wapas madad
hawa pawan dabaav chakravaat toofan garam thanda baarish badal samundar
paani raftaar gubbara zameen taapmaan bijli garaj nami ghanatva aayatan
santript aavaas bhojan jal
""".split())


def detect_language(text: str) -> str | None:
    """Per-turn language detector: 'hi' | 'en' | None (ambiguous — caller
    should keep the current session language rather than force a switch).

    Devanagari is a certain Hindi signal. For Latin-script input we need
    multiple strong Hindi hits to say 'hi', and several words with zero
    Hindi signal to say 'en' — short/mixed utterances stay ambiguous so the
    preferred conversation language survives things like "ok" or "thanks"."""
    if not text:
        return None
    if _DEVA.search(text):
        return "hi"
    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return None
    hits = sum(1 for w in words if w in _HI_LEX)
    if hits >= 2 and hits / len(words) >= 0.34:
        return "hi"
    if hits >= 1 and hits / len(words) >= 0.5:
        return "hi"
    if len(words) >= 3 and hits == 0:
        return "en"
    return None


def _norm(text: str) -> str:
    """Lowercase word stream; keeps Devanagari so Hindi utterances don't
    all normalize to "" (which made every Hindi final a 'duplicate')."""
    return " ".join(re.findall(r"[a-z0-9\u0900-\u097f]+", (text or "").lower()))


def _shared_span(words: list[str], blob: str, n: int = 4) -> bool:
    """True when any contiguous n-word span of `words` occurs in `blob`
    (a space-joined normalized word stream)."""
    if len(words) < n:
        return False
    blob = " " + blob + " "
    for i in range(len(words) - n + 1):
        if " " + " ".join(words[i:i + n]) + " " in blob:
            return True
    return False


def resume_store(session_id: str | None, chapter_id: int) -> dict:
    """Conversation state that survives a websocket reconnect (same browser
    session id + same chapter): history and whether we already greeted."""
    now = time.time()
    for k in [k for k, v in _SESSIONS.items() if now - v["ts"] > _SESSION_TTL_S]:
        del _SESSIONS[k]
    st = _SESSIONS.get(session_id or "")
    if st is None or st["chapter_id"] != chapter_id:
        st = {"history": [], "greeted": False, "chapter_id": chapter_id,
              "ts": now}
        if session_id:
            _SESSIONS[session_id] = st
    st["ts"] = now
    return st


class TutorSession:
    """One websocket connection's tutor session. `send` ships dicts to the
    client; `audio` ships raw audio bytes (mp3 sentence fragments)."""

    def __init__(self, chapter_id: int, grade: int, title: str, subject: str,
                 send: Callable[[dict], Awaitable[None]],
                 audio: Callable[[bytes], Awaitable[None]],
                 llm=None, tts=None, store: dict | None = None,
                 retriever=None):
        self.chapter_id, self.grade = chapter_id, grade
        self.title, self.subject = title, subject
        self._send, self._audio = send, audio
        self.llm = llm or providers.make_llm()
        self.tts = tts or providers.make_tts()
        self.retriever = retriever or providers.make_retriever()
        self.language = "English"
        self.state = "stopped"
        self.store = store or {"history": [], "greeted": False,
                               "chapter_id": chapter_id, "ts": time.time()}
        self.history: list[dict] = self.store["history"]
        self._turn: asyncio.Task | None = None
        self._turn_id = 0
        self.started = asyncio.Event()
        self._playback_done = asyncio.Event()
        # echo guard: normalized words of everything Infy recently spoke.
        # Only consulted while Infy is audible (+ a short tail) — outside
        # that window a transcript is the student, even if it repeats words
        # from the last answer ("what is low pressure?" after an answer
        # about low pressure).
        self._recent_bot: list[tuple[float, str]] = []
        self._echo_until = 0.0
        self._last_final: tuple[str, float] | None = None
        self._interrupt_at = 0.0
        # split-final merge: Chrome often cuts one spoken question into two
        # finals ("what is" … "air pressure"); the second used to cancel
        # the first and get answered without its start.
        self._voice_q: tuple[str, float] | None = None
        self._spoke_turn = 0
        # practice mode: the active quiz item awaiting the student's answer
        self._quiz: dict | None = None
        self._quiz_asked: set[str] = set()
        # in-session learning progress — memory only, never persisted to
        # disk; privacy by design (no student profile survives the session)
        self.store.setdefault(
            "progress", {"asked": 0, "quiz_ok": 0, "quiz_n": 0})

    @property
    def greeted(self) -> bool:
        return self.store["greeted"]

    @greeted.setter
    def greeted(self, v: bool):
        self.store["greeted"] = v

    # -- outbound helpers ----------------------------------------------------
    async def _state(self, s: str, detail: str = "", turn: int | None = None):
        self.state = s
        await self._send({"type": "state", "state": s, "detail": detail,
                          "turn": self._turn_id if turn is None else turn})

    async def _text(self, role: str, text: str, turn: int | None = None):
        await self._send({"type": "text", "role": role, "text": text,
                          "turn": self._turn_id if turn is None else turn})

    def _idle_state(self) -> str:
        return "listening" if self.started.is_set() else "idle"

    # -- commands ------------------------------------------------------------
    async def start(self, say_hello: bool = True):
        """'Infy start' / mic button: enter listening; greet once per
        conversation (not again after a reconnect)."""
        self.started.set()
        if self._turn is None or self._turn.done():
            await self._state("listening")
        warm = getattr(self.llm, "warm", None)
        if warm:
            asyncio.ensure_future(warm())
        if say_hello and not self.greeted:
            self.greeted = True
            self._spawn_turn(_GREET, is_greeting=True)

    async def stop(self):
        """'Infy stop' / stop button: cancel the turn, latch stopped."""
        self._cancel_turn()
        self.started.clear()
        self._quiz = None            # stop exits practice mode too
        # a stopped question must not dedup-block the same question asked
        # again right after restart
        self._voice_q = None
        self._last_final = None
        self._echo_until = time.monotonic() + ECHO_TAIL_S
        await self._send({"type": "interrupted", "turn": self._turn_id})
        await self._state("stopped", "stopped — tap the mic or say 'Infy start'")

    async def interrupt(self, turn: int | None = None):
        """User started talking over Infy: cancel generation + playback.
        A stale interrupt (for an older turn) is ignored."""
        if turn is not None and turn != self._turn_id:
            return
        if self._turn and not self._turn.done():
            self._cancel_turn()
            await self._send({"type": "interrupted", "turn": self._turn_id})
        # the interrupted question is dead — if the student repeats it,
        # that's a fresh utterance, not a re-fire to dedup away
        self._voice_q = None
        # brief window where the cut-off echo tail can still arrive — the
        # fuzzy check drops it unless it's clearly a new user utterance
        self._interrupt_at = time.monotonic()
        self._echo_until = self._interrupt_at + ECHO_TAIL_S
        await self._state(self._idle_state())

    def playback_done(self, turn: int | None):
        """Client finished playing every audio buffer of `turn`."""
        if turn == self._turn_id:
            self._playback_done.set()

    def set_language(self, lang: str):
        self.language = "Hindi" if (lang or "").lower().startswith("hi") else "English"

    def set_chapter(self, chapter_id: int, grade: int, title: str, subject: str):
        if chapter_id != self.chapter_id:
            self.history.clear()  # new context: old follow-ups would mislead
        self.chapter_id, self.grade = chapter_id, grade
        self.title, self.subject = title, subject
        self.store["chapter_id"] = chapter_id

    def _is_echo(self, text: str, fuzz: float = 0.85) -> bool:
        """Transcript is Infy hearing its own speaker output: it shares a
        contiguous 4-word span with recently spoken bot text, or is mostly
        covered by it. `fuzz` lowers the coverage threshold for the
        post-interrupt window where echo is heavily misheard."""
        now = time.monotonic()
        self._recent_bot = [(t, s) for t, s in self._recent_bot
                            if now - t < 60]
        if not self._recent_bot:
            return False
        words = _norm(text).split()
        if len(words) < 2:
            return False
        blob = " ".join(s for _, s in self._recent_bot)
        if _shared_span(words, blob, 4):
            return True
        # short utterances: echo only when the whole phrase is verbatim bot
        if " " + " ".join(words) + " " in " " + blob + " ":
            return True
        bot_words = set(blob.split())
        covered = sum(1 for w in words if w in bot_words)
        return len(words) >= 5 and covered / len(words) >= fuzz

    def _echo_window(self) -> bool:
        return self.state == "speaking" or time.monotonic() < self._echo_until

    # -- incoming transcript --------------------------------------------------
    async def on_transcript(self, text: str, final: bool, ts_ms: int | None = None):
        if not text:
            return
        cmd, cleaned = commands.parse_command(text)
        # speaker echo can garble "infy stop" past the strict wake+verb
        # pattern — while Infy is audible accept a looser wake+stop pairing
        if cmd is None and self.state == "speaking" \
                and commands.loose_stop(text):
            cmd, cleaned = "stop", ""
            log.info("loose-stop accepted while speaking: %r", text)
        log.info("transcript final=%s state=%s cmd=%s text=%r",
                 final, self.state, cmd, text)
        if cmd == "stop":
            self._quiz = None
            await self.stop()
            return
        if cmd == "start":
            await self.start()
            return
        if cmd == "quiz":
            await self._start_quiz()
            return
        if cmd in _SESSION_CMDS:
            # act once per utterance: interims and Chrome's duplicate final
            # would otherwise say the answer twice
            if not final:
                return
            norm, now = _norm(text), time.monotonic()
            if self._last_final and self._last_final[0] == norm \
                    and now - self._last_final[1] < 4:
                return
            self._last_final = (norm, now)
            await self.command(cmd)
            return
        if not self.started.is_set():
            log.info("drop (stopped latch): %r", text)
            return  # stopped latch: ignore conversation while stopped
        # SPEAKING GATE: while Infy is audible, mic transcripts are
        # overwhelmingly its own echo (Web Speech has no AEC). Commands were
        # handled above; real barge-in comes from the client's echo-cancelled
        # mic VAD, which sends control("interrupt").
        if self.state == "speaking":
            log.info("drop (speaking gate): %r", text)
            return
        if not final:
            return  # interim transcripts never drive state; finals only
        if self._echo_window() and self._is_echo(text):
            log.info("drop (echo window): %r", text)
            return  # Infy heard itself — not a user utterance
        norm = _norm(text)
        now = time.monotonic()
        # Chrome can re-fire the same final — one utterance = one turn
        if norm and self._last_final and self._last_final[0] == norm \
                and now - self._last_final[1] < 4:
            log.info("drop (dup final): %r", text)
            return
        # post-interrupt tail: cut-off speaker echo can still arrive as a
        # "final" right after a barge-in. Within ~1.2s it must fuzzy-match
        # recent bot text to pass; a genuinely new question won't.
        if now - self._interrupt_at < 1.2 and self._is_echo(text, fuzz=0.5):
            log.info("drop (post-interrupt echo): %r", text)
            return
        # Chrome re-fires the SAME final (recognizer restarts) several
        # seconds apart — beyond the 4s window above. If it's the question
        # a turn is already answering, cancelling+restarting it just resets
        # latency forever: drop the re-fire instead. Keep dropping while the
        # turn runs, and for a short tail after it completes.
        if self._voice_q and norm == _norm(self._voice_q[0]):
            running = self._turn is not None and not self._turn.done()
            if running or now - self._voice_q[1] < 10:
                log.info("drop (in-flight dup of current question): %r",
                         text)
                return
        # noise fragment: an utterance made of ONLY filler words ("पर",
        # "ok", "लेकिन") carries no question — don't burn a turn on it
        if norm and not [w for w in norm.split()
                         if w not in commands._FILLER_WORDS]:
            log.info("drop (noise fragment): %r", text)
            return
        self._last_final = (norm, now)
        q = cleaned or text
        prev = self._voice_q
        if (prev and self.state == "thinking" and self._spoke_turn != self._turn_id
                and now - prev[1] < SPLIT_FINAL_S):
            q = f"{prev[0]} {q}"
        self._voice_q = (q, now)
        await self.ask(q, speech_ts=ts_ms)

    async def _start_quiz(self):
        """Practice mode: pull a curated chapter Q&A and pose it to the
        student; their next utterance is graded against the gold answer."""
        if not self.started.is_set():
            return
        item = rag.random_quiz(self.chapter_id, exclude=self._quiz_asked)
        if item is None:
            await self.ask("Say there are no practice questions left in "
                           "this chapter.", typed=True, detect_lang=False)
            return
        log.info("quiz start: %r", item["q"][:60])
        await self.ask(
            f"You are quizzing the student. Ask them this question word "
            f"for word, then stop and wait for their answer — do NOT "
            f"answer it yourself: \"{item['q']}\"", typed=True,
            detect_lang=False)
        # set AFTER the posing ask() — ask() grades when _quiz is set
        self._quiz = item
        self._quiz_asked.add(item["q"])

    async def command(self, cmd: str, typed: bool = False):
        """Voice needs the started latch (typed is an explicit action); a
        language switch is still applied while stopped, just not spoken."""
        if cmd in ("hindi", "english"):
            lang = "hi" if cmd == "hindi" else "en"
            self.set_language(lang)
            await self._send({"type": "language", "lang": lang,
                              "turn": self._turn_id})
            if typed or self.started.is_set():
                await self._say(_LANG_ACK[lang])
            return
        if cmd == "repeat":
            if not typed and not self.started.is_set():
                return
            last = next((m["content"] for m in reversed(self.history)
                         if m["role"] == "assistant"), None)
            lang = "hi" if self.language == "Hindi" else "en"
            await self._say(last or _NOTHING_TO_REPEAT[lang])

    async def _say(self, text: str):
        """Speak fixed text as a normal (interruptible) turn."""
        self._cancel_turn()
        self._turn_id += 1
        await self._state("thinking")
        self._spawn_turn(text, canned=True, new_id=False)

    async def ask(self, question: str, speech_ts: int | None = None,
                  typed: bool = False, detect_lang: bool = True):
        """Voice questions need the started latch; a typed question is an
        explicit action and is answered even while the mic is off."""
        if not typed and not self.started.is_set():
            log.info("ask dropped (not started): %r", question)
            return
        q = question.strip()
        if not q:
            return
        if typed:
            # typed "quiz me" reaches ask() directly, bypassing on_transcript
            cmd, cleaned = commands.parse_command(q)
            if cmd == "quiz":
                await self._start_quiz()
                return
            if cmd in _SESSION_CMDS:
                await self.command(cmd, typed=True)
                return
            q = cleaned or q
        if detect_lang:
            # Language policy: the student's CURRENT utterance decides the
            # reply language. A confident switch (Devanagari, clear English,
            # or dominant Hindi lexicon) updates the session language;
            # ambiguous input keeps it. This is what stops old Hindi history
            # from overriding a plainly-English question.
            qlang = detect_language(q)
            if qlang:
                new = "Hindi" if qlang == "hi" else "English"
                if new != self.language:
                    log.info("language switch %s -> %s on %r",
                             self.language, new, q[:40])
                    self.language = new
        self.store["progress"]["asked"] += 1
        if self._quiz:
            # the student is answering a practice question — grade it with
            # the gold answer, don't treat it as a fresh question
            item, self._quiz = self._quiz, None
            self.store["progress"]["quiz_n"] += 1
            log.info("quiz answer for %r: %r", item["q"][:60], q)
            q = (
                f"QUIZ CHECK: You asked the student: \"{item['q']}\" The "
                f"correct answer is: \"{item['a']}\" The student answered: "
                f"\"{q}\" Tell them clearly whether they were right — "
                f"praise a correct answer warmly, gently correct a wrong "
                f"one — then give the correct answer in one or two "
                f"sentences.")
        log.info("ask -> turn %d: %r", self._turn_id + 1, q)
        self._cancel_turn()  # a new question supersedes anything in flight
        self._turn_id += 1
        await self._text("you", q)
        await self._state("thinking")
        self._spawn_turn(q, speech_ts=speech_ts, new_id=False)

    # -- the turn -------------------------------------------------------------
    def _spawn_turn(self, question: str, is_greeting: bool = False,
                    speech_ts: int | None = None, new_id: bool = True,
                    canned: bool = False):
        if new_id:
            self._turn_id += 1
        tid = self._turn_id
        self._playback_done.clear()
        self._turn = asyncio.create_task(
            self._run_turn(tid, question, is_greeting, speech_ts, canned))

    def _cancel_turn(self):
        if self._turn and not self._turn.done():
            self._turn.cancel()

    def _system(self, evidence_hits: list[dict]) -> str:
        if evidence_hits:
            # cap each passage — prompt eval on CPU dominates first-audio
            # latency, so long passages cost real seconds
            ev = "\nRETRIEVED CHAPTER PASSAGES:\n" + "\n---\n".join(
                f"[{h['source']}] {h['text'][:700]}" for h in evidence_hits)
        else:
            ev = ("\nRETRIEVED CHAPTER PASSAGES: none found for this question. "
                  "Treat it as outside this chapter: one-sentence answer, say "
                  "it is beyond this chapter, then steer back to the chapter.")
        return _SYS.format(grade=self.grade, title=self.title,
                           subject=self.subject, language=self.language,
                           evidence=ev)

    async def _run_turn(self, tid: int, question: str, is_greeting: bool,
                        speech_ts: int | None, canned: bool = False):
        t0 = time.perf_counter()
        first_tok = first_audio = None
        reply_parts: list[str] = []
        audio_s = 0.0          # seconds of audio sent this turn (estimate)
        spoke_fallback = False
        try:
            hits = [] if (is_greeting or canned) else self.retriever.retrieve(
                question, self.chapter_id, k=3)
            if not canned:
                await self._send({"type": "sources", "turn": tid,
                                  "sources": [{"source": h["source"],
                                               "kind": h["kind"]}
                                              for h in hits]})
            lang = "hi" if self.language == "Hindi" else "en"

            async def speak(sent: str):
                nonlocal audio_s, first_audio
                self._spoke_turn = tid
                self._recent_bot.append((time.monotonic(), _norm(sent)))
                await self._text("bot", sent, tid)
                if self.state != "speaking":
                    await self._state("speaking", turn=tid)
                # audio language follows the sentence's own script: a stray
                # Devanagari sentence must not be fed to the English voice
                slang = "hi" if _DEVA.search(sent) else lang
                if self.tts.name == "browser":
                    await self._send({"type": "speak", "turn": tid,
                                      "text": sent, "lang": slang})
                    audio_s += len(sent) / 14
                    first_audio = first_audio or time.perf_counter()
                    return
                await self._send({"type": "audio_start", "turn": tid,
                                  "format": "mp3"})
                async for chunk in self.tts.speak(sent, slang):
                    await self._audio(chunk)
                    audio_s += len(chunk) / _MP3_BYTES_PER_S
                    if first_audio is None:
                        first_audio = time.perf_counter()
                await self._send({"type": "audio_end", "turn": tid})

            token_stream = _canned_stream(question) if canned else \
                self.llm.stream(self._system(hits), self.history[-12:],
                                question)
            sgen = providers.sentences(token_stream)
            try:
                while True:
                    try:
                        sent = await asyncio.wait_for(
                            sgen.__anext__(), timeout=_LLM_STALL_S)
                    except StopAsyncIteration:
                        break
                    # a wedged worker (silent C++ abort) never errors and
                    # never finishes — without this the session stays on
                    # "speaking" forever and gates every later question
                    if first_tok is None:
                        first_tok = time.perf_counter()
                        if not canned:
                            metrics.record_llm_ttfb(
                                (first_tok - t0) * 1000)
                    reply_parts.append(sent)
                    await speak(sent)
                if not reply_parts and not is_greeting:
                    log.warning("turn %s: empty LLM reply", tid)
                    await speak(_EMPTY_REPLY[lang])
                    spoke_fallback = True
            finally:
                # a cancelled turn leaves token_stream suspended mid-yield;
                # its finally (worker cancel + socket drain + _busy clear)
                # only runs when the generator is closed — schedule it now.
                asyncio.ensure_future(token_stream.aclose())
            if first_audio is not None and not canned:
                metrics.record_first_audio((first_audio - t0) * 1000,
                                           speech_ts=speech_ts)
            if reply_parts and not is_greeting and not canned:
                self.history.append({"role": "user", "content": question})
                self.history.append({"role": "assistant",
                                     "content": " ".join(reply_parts)})
                del self.history[:-40]
            await self._send({"type": "turn_end", "turn": tid})
            if reply_parts or spoke_fallback:
                # stay "speaking" until the browser has actually finished
                # playing; fallback if the client never reports back
                try:
                    await asyncio.wait_for(self._playback_done.wait(),
                                           timeout=audio_s + 3.0)
                except asyncio.TimeoutError:
                    log.warning("turn %s: no playback_done from client", tid)
                self._echo_until = time.monotonic() + ECHO_TAIL_S
            if tid == self._turn_id and self.state != "stopped":
                await self._state(self._idle_state(), turn=tid)
        except asyncio.CancelledError:
            metrics.record_cancelled()
            raise
        except Exception as e:
            log.exception("turn %s failed", tid)
            if tid == self._turn_id:
                # never forward raw exception text (may contain URLs/keys)
                await self._state("error", f"answer failed ({type(e).__name__}) — "
                                           "please ask again", turn=tid)
                await asyncio.sleep(0.05)
                if self.state == "error":
                    await self._state(self._idle_state(), turn=tid)


async def handle_ws(websocket, chapter_docs: dict[int, dict]):
    """Wire a FastAPI WebSocket to a TutorSession. `chapter_docs` maps
    chapter_id → full doc dict for grade/title/subject lookup."""
    await websocket.accept()
    sess: TutorSession | None = None

    async def send(msg: dict):
        await websocket.send_json(msg)

    async def audio(chunk: bytes):
        await websocket.send_bytes(chunk)

    try:
        while True:
            try:
                raw = await websocket.receive()
            except RuntimeError:
                break   # starlette raises on receive-after-disconnect
            if raw.get("type") == "websocket.disconnect":
                break
            if raw.get("bytes") is not None:
                continue
            try:
                msg = json_loads(raw.get("text") or "{}")
            except Exception:
                continue
            mtype = msg.get("type")
            if mtype in ("chapter", "hello"):
                cid = int(msg.get("chapter_id") or msg.get("chapterId") or 1)
                doc = chapter_docs.get(cid)
                if not doc:
                    await send({"type": "state", "state": "error",
                                "detail": f"no chapter {cid}"})
                    continue
                if sess is None:
                    sess = TutorSession(cid, doc.get("grade") or 8,
                                        doc.get("title") or "",
                                        doc.get("subject") or "",
                                        send, audio,
                                        store=resume_store(
                                            msg.get("session_id"), cid))
                else:
                    sess.set_chapter(cid, doc.get("grade") or 8,
                                     doc.get("title") or "", doc.get("subject") or "")
                await send({"type": "ready", "chapter_id": cid,
                            "title": doc.get("title"),
                            "resumed": bool(sess.history) or sess.greeted})
            elif sess is None:
                await send({"type": "state", "state": "error",
                            "detail": "send chapter/hello first"})
            elif mtype == "transcript":
                await sess.on_transcript(msg.get("text") or "",
                                         bool(msg.get("final")),
                                         msg.get("ts"))
            elif mtype == "ask":
                await sess.ask(msg.get("text") or "", typed=True)
            elif mtype == "language":
                sess.set_language(msg.get("lang") or "en")
            elif mtype == "playback_done":
                sess.playback_done(msg.get("turn"))
            elif mtype == "control":
                cmd = msg.get("cmd")
                if cmd == "start":
                    await sess.start(say_hello=msg.get("greet", True))
                elif cmd == "stop":
                    await sess.stop()
                elif cmd == "interrupt":
                    await sess.interrupt(msg.get("turn"))
    finally:
        if sess:
            sess._cancel_turn()


def json_loads(s: str):
    import json as _j
    return _j.loads(s)
