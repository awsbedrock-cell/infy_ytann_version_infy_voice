"""commands.py — deterministic Infy voice-command parser.

Normal conversation must NOT trigger commands: a phrase only counts when it
contains the wake word "infy" (or "infi"/"inphie" — common STT mishears, or
the Devanagari forms the hi-IN recognizer produces) plus a control verb.
Mirrors commands.ts in the frontend — keep the two in sync.

Returns (command, cleaned_text): command in {"start","stop","quiz",
"repeat","hindi","english",None};
cleaned_text is the transcript with the command phrase stripped ("" if it
was pure command).
"""
import re

_WAKE = (r"(?:infy|infi|infie|inphie|enfee|in fee|inf e|in face|in feet|"
         r"in faith|in the face|in fi|in fie|in fit|win fi|"
         r"इन्फी|इंफी|इनफी|इन्फ़ी|इंफ़ी|इन्फि|इन फिर|इन फि|इनफिर|इन फीयर|इन फियर|इन फी)")
_START = (r"(?:start|begin|wake up|sun|suno|shuru|shuru karo|chalu|chalu karo|"
          r"स्टार्ट|शुरू करो|शुरू|शुरु|चालू करो|चालू|सुनो)")
_STOP = (r"(?:stop talking|stop|pause|wait|hold on|enough|quiet|ruk jao|ruko|"
         r"ruk|band karo|band|bas karo|bas|chup ho jao|chup raho|chup|shaant|"
         r"shant|bolna band|sunna band|shut up|"
         r"स्टॉप|रुक जाओ|रुको|रुक|बंद करो|बंद|बस करो|बस|चुप हो जाओ|चुप रहो|चुप|"
         r"शांत|बोलना बंद|सुनना बंद)")

# word boundaries that also work for Devanagari (\b breaks on matras)
_L = r"(?<![\w\u0900-\u097f])"
_R = r"(?![\w\u0900-\u097f])"
_GAP = r"[\s\w\u0900-\u097f,.'-]"

# "infy stop", "infy please stop", "ok infy stop now" etc.
_START_RE = re.compile(rf"{_L}{_WAKE}{_R}{_GAP}{{0,20}}?{_L}{_START}{_R}", re.I)
_STOP_RE = re.compile(rf"{_L}{_WAKE}{_R}{_GAP}{{0,20}}?{_L}{_STOP}{_R}", re.I)
# "hey infy, stop" / "stop infy" forms where the verb precedes the wake word
_STOP_FIRST_RE = re.compile(rf"{_L}{_STOP}{_R}{_GAP}{{0,12}}?{_L}{_WAKE}{_R}", re.I)
_WAKE_ANY_RE = re.compile(rf"{_L}{_WAKE}{_R}", re.I)

# STT often drops the wake word entirely: "infy start" arrives as bare
# "start", "infy stop" as "stop"/"stopping". An utterance that is only a
# control verb (+ politeness fillers) is never a real question, so treat it
# as the command. Multi-word conversation is unaffected — "the show will
# start soon" is >3 words and a single bare "stop" was already unambiguous.
_FILLER_WORDS = {"please", "plz", "ok", "okay", "hey", "ab", "na", "now",
                 "karo", "bhi", "se", "it", "ho", "jao", "do", "kar",
                 "par", "lekin", "matlab", "theek",
                 "हो", "जाओ", "कर", "दो", "तो", "अब", "पर", "लेकिन", "मतलब",
                 "ठीक", "अच्छा"}
_START_BARE = (_START + "|continue|resume|aage|aage badho|aage bolo|go on|"
               r"keep going|jaari|jaari rakho|jari rakho|bolo|बोलो|आगे बोलो|"
               r"जारी रखो|जारी")
_BARE_STOP_RE = re.compile(rf"^(?:{_STOP})$", re.I)
_BARE_START_RE = re.compile(rf"^(?:{_START_BARE})$", re.I)


def _bare_command(low: str):
    """Whole-utterance control verb (STT often drops the wake word).
    Only fires when the ENTIRE text is verb words + politeness fillers."""
    words = re.findall(r"[\w\u0900-\u097f]+", low)
    if not 0 < len(words) <= 3:
        return None
    core = [w for w in words if w not in _FILLER_WORDS]
    if not core:
        return None
    joined = " ".join(core)
    if _BARE_STOP_RE.fullmatch(joined) \
            or all(_BARE_STOP_RE.fullmatch(w) for w in core):
        return "stop"
    if _BARE_START_RE.fullmatch(joined) \
            or all(_BARE_START_RE.fullmatch(w) for w in core):
        return "start"
    return None


# "quiz me" / "test me" — a practice request is conversational, not a
# control verb, so no wake word is required (but it must be short).
_QUIZ_LEAD = (r"(?:give me|gimme|ask me|mujhe|mujhko|ek|can i have|"
              r"can you give me|lets do|let us do|please)")
_QUIZ_RE = re.compile(
    rf"^{_L}(?:{_WAKE}{_R}{_GAP}{{0,10}})?"
    rf"(?:{_QUIZ_LEAD}{_GAP}{{0,10}})?"
    r"(?:quiz|test|practice|sawaal|sawal|poochho|pucho|imtihaan|"
    r"क्विज़|टेस्ट|पूछो|पूछ|सवाल|प्रश्न|इम्तिहान)"
    rf"{_R}{_GAP}{{0,25}}$", re.I)


# "infy repeat" / "infy hindi" / "infy english": the wake word is REQUIRED
# and, after removing it, the WHOLE remainder must be the command phrase —
# so "infy, what is pressure in hindi" stays a question.
_REPEAT_FULL = re.compile(
    r"^(?:please |plz |ok |okay )?"
    r"(?:repeat(?: that| it| karo)?|say (?:that|it) again|say again|again|"
    r"once more|one more time|dobara(?: bolo| boliye| batao)?|"
    r"phir se(?: bolo| boliye| batao)?|fir se(?: bolo| batao)?|"
    r"दोबारा(?: बोलो| बोलिए| बताओ)?|फिर से(?: बोलो| बोलिए| बताओ)?|"
    r"रिपीट(?: करो)?)(?: please| plz)?$", re.I)
_LANG_LEAD = r"(?:(?:please|plz|ok|okay|ab|अब|speak|talk|reply|switch to|switch|in) )*"
_LANG_TAIL = (r"(?: (?:mein|me|main|में|please|plz|bolo|बोलो|boliye|बोलिए|"
              r"baat karo|बात करो|mode|language))*")
_HINDI_FULL = re.compile(rf"^{_LANG_LEAD}(?:hindi|हिंदी|हिन्दी){_LANG_TAIL}$", re.I)
_ENGLISH_FULL = re.compile(
    rf"^{_LANG_LEAD}(?:english|angrezi|angreji|इंग्लिश|अंग्रेज़ी|अंग्रेजी)"
    rf"{_LANG_TAIL}$", re.I)


_LEAD_FILLER = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|oye|arre|acha|achha|हे|ओके|अरे|अच्छा) )+",
    re.I)


def _wake_command(rest: str):
    """`rest` = utterance with the wake word removed."""
    r = re.sub(r"[^\w\u0900-\u097f]+", " ", rest).strip()
    r = _LEAD_FILLER.sub("", r)   # "hey infy repeat" -> "repeat"
    if not r:
        return None
    if _REPEAT_FULL.fullmatch(r):
        return "repeat"
    if _HINDI_FULL.fullmatch(r):
        return "hindi"
    if _ENGLISH_FULL.fullmatch(r):
        return "english"
    return None


# while Infy is speaking, speaker echo makes a strict wake+verb parse fail
# ("infy stop" arrives garbled inside echo text). A looser rule: the wake
# word AND a stop verb anywhere in the utterance, in either order. Only safe
# to apply while gated — the wake word requirement keeps Infy's own echo
# (which never contains "infy") from self-stopping.
def loose_stop(text: str) -> bool:
    low = (text or "").lower()
    return bool(_WAKE_ANY_RE.search(low)) and bool(
        re.search(rf"{_L}{_STOP}{_R}", low))


def parse_command(text: str):
    """Deterministic command parse: wake word + verb, verb-first + wake word,
    or a whole-utterance bare control verb (STT drops 'infy' often)."""
    t = (text or "").strip()
    if not t:
        return None, ""
    low = t.lower()
    if _STOP_RE.search(low) or _STOP_FIRST_RE.search(low):
        return "stop", ""
    if _QUIZ_RE.search(low) and len(low.split()) <= 8:
        return "quiz", ""
    if _START_RE.search(low):
        return "start", ""
    # Wake word present without a control verb → it's address, not a command.
    # Strip the wake phrase so "infy what is pressure?" still reaches the tutor
    # as "what is pressure?".
    if _WAKE_ANY_RE.search(low):
        cleaned = _WAKE_ANY_RE.sub("", low)
        cleaned = re.sub(r"^[^\w\u0900-\u097f]+|[^\w\u0900-\u097f]+$", "",
                         cleaned).strip()
        c = _wake_command(cleaned)
        if c:
            return c, ""
        # the remainder may itself be a control verb — "continue Infi
        # continue" (STT put the wake word mid-phrase) -> "continue continue"
        c = _bare_command(cleaned)
        if c:
            return c, ""
        return None, cleaned or ""
    c = _bare_command(low)
    if c:
        return c, ""
    return None, t
