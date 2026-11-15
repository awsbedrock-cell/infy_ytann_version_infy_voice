/* commands.ts — deterministic Infy voice-command parser (mirror of
 * commands.py — keep the two in sync). A phrase only counts as a command when
 * it contains the wake word "infy" (or common STT mishears, or the Devanagari
 * forms the hi-IN recognizer produces) plus a control verb — ordinary speech
 * like "bus stop" or "bas kya hua" never triggers. */

const WAKE = String.raw`(?:infy|infi|infie|inphie|enfee|in fee|inf e|in face|in feet|in faith|in the face|in fi|in fie|in fit|win fi|इन्फी|इंफी|इनफी|इन्फ़ी|इंफ़ी|इन्फि|इन फिर|इन फि|इनफिर|इन फीयर|इन फियर|इन फी)`;
const START = String.raw`(?:start|begin|wake up|sun|suno|shuru|shuru karo|chalu|chalu karo|स्टार्ट|शुरू करो|शुरू|शुरु|चालू करो|चालू|सुनो)`;
const STOP = String.raw`(?:stop talking|stop|pause|wait|hold on|enough|quiet|ruk jao|ruko|ruk|band karo|band|bas karo|bas|chup ho jao|chup raho|chup|shaant|shant|bolna band|sunna band|shut up|स्टॉप|रुक जाओ|रुको|रुक|बंद करो|बंद|बस करो|बस|चुप हो जाओ|चुप रहो|चुप|शांत|बोलना बंद|सुनना बंद)`;

// word boundaries that also work for Devanagari (\b is ASCII-only in JS)
const L = String.raw`(?<![\w\u0900-\u097f])`;
const R = String.raw`(?![\w\u0900-\u097f])`;
const GAP = String.raw`[\s\w\u0900-\u097f,.'-]`;

const START_RE = new RegExp(`${L}${WAKE}${R}${GAP}{0,20}?${L}${START}${R}`, "i");
const STOP_RE = new RegExp(`${L}${WAKE}${R}${GAP}{0,20}?${L}${STOP}${R}`, "i");
const STOP_FIRST_RE = new RegExp(`${L}${STOP}${R}${GAP}{0,12}?${L}${WAKE}${R}`, "i");
const WAKE_ANY_RE = new RegExp(`${L}${WAKE}${R}`, "i");

// STT often drops the wake word entirely ("infy start" arrives as bare
// "start"). An utterance that is only a control verb (+ politeness fillers)
// is never a real question, so treat it as the command. Mirror of
// commands.py — keep in sync.
const FILLER_WORDS = new Set(["please", "plz", "ok", "okay", "hey", "ab",
  "na", "now", "karo", "bhi", "se", "it", "ho", "jao", "do", "kar",
  "par", "lekin", "matlab", "theek",
  "हो", "जाओ", "कर", "दो", "तो", "अब", "पर", "लेकिन", "मतलब",
  "ठीक", "अच्छा"]);
const START_BARE = `${START}|continue|resume|aage|aage badho|aage bolo|go on|keep going|jaari|jaari rakho|jari rakho|bolo|बोलो|आगे बोलो|जारी रखो|जारी`;
const BARE_STOP_RE = new RegExp(`^(?:${STOP})$`, "i");
const BARE_START_RE = new RegExp(`^(?:${START_BARE})$`, "i");

/** whole-utterance control verb — only fires when the ENTIRE text is
 *  verb words + politeness fillers (STT often drops the wake word) */
function bareCommand(low: string): InfyCommand {
  const words = low.match(/[\w\u0900-\u097f]+/g) || [];
  if (!words.length || words.length > 3) return null;
  const core = words.filter((w) => !FILLER_WORDS.has(w));
  if (!core.length) return null;
  const joined = core.join(" ");
  if (BARE_STOP_RE.test(joined)
      || core.every((w) => BARE_STOP_RE.test(w))) return "stop";
  if (BARE_START_RE.test(joined)
      || core.every((w) => BARE_START_RE.test(w))) return "start";
  return null;
}

// "quiz me" / "test me" — conversational practice request, no wake word
// required, but only fires on a short whole-utterance ask. Mirror of
// commands.py.
const QUIZ_LEAD = String.raw`(?:give me|gimme|ask me|mujhe|mujhko|ek|` +
  String.raw`can i have|can you give me|lets do|let us do|please)`;
const QUIZ_RE = new RegExp(
  `^${L}(?:${WAKE}${R}${GAP}{0,10})?` +
  `(?:${QUIZ_LEAD}${GAP}{0,10})?` +
  String.raw`(?:quiz|test|practice|sawaal|sawal|poochho|pucho|imtihaan|` +
  String.raw`क्विज़|टेस्ट|पूछो|पूछ|सवाल|प्रश्न|इम्तिहान)` +
  `${R}${GAP}{0,25}$`, "i");

export type InfyCommand =
  "start" | "stop" | "quiz" | "repeat" | "hindi" | "english" | null;

// "infy repeat" / "infy hindi" / "infy english": wake word REQUIRED and the
// WHOLE remainder (wake word removed) must be the command phrase, so "infy,
// what is pressure in hindi" stays a question. Mirror of commands.py.
const REPEAT_FULL = new RegExp(
  String.raw`^(?:please |plz |ok |okay )?` +
  String.raw`(?:repeat(?: that| it| karo)?|say (?:that|it) again|say again|again|` +
  String.raw`once more|one more time|dobara(?: bolo| boliye| batao)?|` +
  String.raw`phir se(?: bolo| boliye| batao)?|fir se(?: bolo| batao)?|` +
  String.raw`दोबारा(?: बोलो| बोलिए| बताओ)?|फिर से(?: बोलो| बोलिए| बताओ)?|` +
  String.raw`रिपीट(?: करो)?)(?: please| plz)?$`, "i");
const LANG_LEAD = String.raw`(?:(?:please|plz|ok|okay|ab|अब|speak|talk|reply|switch to|switch|in) )*`;
const LANG_TAIL = String.raw`(?: (?:mein|me|main|में|please|plz|bolo|बोलो|boliye|बोलिए|baat karo|बात करो|mode|language))*`;
const HINDI_FULL = new RegExp(`^${LANG_LEAD}(?:hindi|हिंदी|हिन्दी)${LANG_TAIL}$`, "i");
const ENGLISH_FULL = new RegExp(
  `^${LANG_LEAD}(?:english|angrezi|angreji|इंग्लिश|अंग्रेज़ी|अंग्रेजी)${LANG_TAIL}$`, "i");

const LEAD_FILLER =
  /^(?:(?:hey|hi|hello|ok|okay|oye|arre|acha|achha|हे|ओके|अरे|अच्छा) )+/i;

function wakeCommand(rest: string): InfyCommand {
  const r = rest.replace(/[^\w\u0900-\u097f]+/g, " ").trim()
    .replace(LEAD_FILLER, "");   // "hey infy repeat" -> "repeat"
  if (!r) return null;
  if (REPEAT_FULL.test(r)) return "repeat";
  if (HINDI_FULL.test(r)) return "hindi";
  if (ENGLISH_FULL.test(r)) return "english";
  return null;
}

/** While Infy is speaking, speaker echo garbles "infy stop" past the strict
 *  wake+verb pattern. Looser rule: wake word AND a stop verb anywhere in the
 *  utterance, either order. Only safe while gated — the wake word keeps
 *  Infy's own echo (which never says "infy") from self-stopping.
 *  Mirror of commands.loose_stop — keep in sync. */
const LOOSE_STOP_WORD_RE = new RegExp(`${L}${STOP}${R}`, "i");
export function looseStop(text: string): boolean {
  const low = (text || "").toLowerCase();
  return WAKE_ANY_RE.test(low) && LOOSE_STOP_WORD_RE.test(low);
}

/** Returns [command, cleanedText]. cleanedText is the transcript minus the
 * command phrase — "" when it was pure command. */
export function parseCommand(text: string): [InfyCommand, string] {
  const t = (text || "").trim();
  if (!t) return [null, ""];
  const low = t.toLowerCase();
  if (STOP_RE.test(low) || STOP_FIRST_RE.test(low)) return ["stop", ""];
  if (QUIZ_RE.test(low) && low.split(/\s+/).length <= 8) return ["quiz", ""];
  if (START_RE.test(low)) return ["start", ""];
  if (WAKE_ANY_RE.test(low)) {
    const cleaned = low.replace(WAKE_ANY_RE, "")
      .replace(/^[^\w\u0900-\u097f]+|[^\w\u0900-\u097f]+$/g, "").trim();
    const w = wakeCommand(cleaned);
    if (w) return [w, ""];
    // the remainder may itself be a control verb — "continue Infi continue"
    const c = bareCommand(cleaned);
    if (c) return [c, ""];
    return [null, cleaned];
  }
  const c = bareCommand(low);
  if (c) return [c, ""];
  return [null, t];
}
