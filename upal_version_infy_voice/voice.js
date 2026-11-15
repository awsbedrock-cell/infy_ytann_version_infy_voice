/* ============================================================================
 * voice.js — the voice experiment file.
 * Everything voice-related lives here so Deepti can tweak ONE file:
 *   - Speech recognition  (Web Speech API — built into Chrome/Edge, no key)
 *   - Speech synthesis    (speechSynthesis — picks voice/rate/pitch)
 *   - Voice commands      (stop / pause / resume — English + Hindi)
 *   - Answer matcher      (toy version of the real offline matcher)
 *
 * EXPERIMENT KNOBS are marked with  >>> EXPERIMENT
 * ==========================================================================*/

/* >>> EXPERIMENT: speech output settings ---------------------------------- */
const VOICE = {
  rate: 0.95,        // 0.5 = slow … 2 = fast
  pitch: 1.05,       // 0 = low … 2 = high
  preferNames: [     // picked in order if present on the device
    "Google UK English Female",
    "Microsoft Swara",        // Hindi-Indian voice (Edge/Win11)
    "Microsoft Heera",
    "Google हिन्दी",
  ],
  lang: "en-IN",     // fallback language tag
};

/* >>> EXPERIMENT: recognition settings ------------------------------------ */
const LISTEN = {
  lang: "en-IN",     // try "hi-IN" for Hindi-first listening
  interimResults: true,
  continuous: true,
};

/* >>> EXPERIMENT: voice commands ------------------------------------------
 * key = what the user says (matched loosely), value = action name        */
const COMMANDS = {
  stop:     ["stop", "ruk jao", "band karo", "bas"],
  pause:    ["pause", "hold on", "ruko", "theher jao", "wait"],
  resume:   ["continue", "resume", "aage bolo", "chalu karo", "go on"],
  readChapter: ["read the chapter", "chapter padho", "start reading"],
};

/* --------------------------------------------------------------------------
 * Speech synthesis wrapper — queue-safe, interruptible, pause/resume.
 * ------------------------------------------------------------------------*/
const speaker = {
  speaking: false,
  paused: false,

  pick() {
    const vs = speechSynthesis.getVoices();
    for (const name of VOICE.preferNames) {
      const hit = vs.find((v) => v.name === name);
      if (hit) return hit;
    }
    return vs.find((v) => v.lang.startsWith(VOICE.lang.slice(0, 2))) || vs[0];
  },

  say(text, { onend } = {}) {
    this.stop();
    const u = new SpeechSynthesisUtterance(text);
    const v = this.pick();
    if (v) u.voice = v;
    u.rate = VOICE.rate;
    u.pitch = VOICE.pitch;
    u.lang = (v && v.lang) || VOICE.lang;
    this.speaking = true;
    u.onend = u.onerror = () => { this.speaking = false; onend && onend(); };
    speechSynthesis.speak(u);
  },
  stop()    { speechSynthesis.cancel(); this.speaking = false; this.paused = false; },
  pause()   { if (this.speaking) { speechSynthesis.pause();  this.paused = true;  } },
  resume()  { if (this.paused)   { speechSynthesis.resume(); this.paused = false; } },
};

/* --------------------------------------------------------------------------
 * Speech recognition wrapper — barge-in: user speech interrupts the bot.
 * ------------------------------------------------------------------------*/
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const listener = {
  rec: null,
  supported: !!SR,
  onFinal: null,   // set by app
  onInterim: null,
  onCommand: null,

  start() {
    if (!this.supported) return;
    this.stop();
    const r = (this.rec = new SR());
    r.lang = LISTEN.lang;
    r.interimResults = LISTEN.interimResults;
    r.continuous = LISTEN.continuous;
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        const text = res[0].transcript.trim();
        if (!text) continue;
        const cmd = matchCommand(text);
        if (cmd) { this.onCommand && this.onCommand(cmd, text); continue; }
        if (res.isFinal) this.onFinal && this.onFinal(text);
        else this.onInterim && this.onInterim(text);
      }
    };
    // Keep listening through pauses — restart unless we stopped on purpose.
    r.onend = () => { if (this._want) r.start(); };
    r.onerror = (e) => console.warn("[sr]", e.error);
    this._want = true;
    r.start();
  },
  stop() { this._want = false; if (this.rec) this.rec.stop(); },
};

function matchCommand(text) {
  const t = text.toLowerCase().replace(/[.,!?]/g, "");
  for (const [action, phrases] of Object.entries(COMMANDS)) {
    if (phrases.some((p) => t === p || t.includes(" " + p) || t.startsWith(p + " "))) {
      return action;
    }
  }
  return null;
}

/* --------------------------------------------------------------------------
 * Toy answer matcher — keyword overlap against chapter Q&A + page text.
 * >>> EXPERIMENT: scoring weights, min score, extra corpora
 * ------------------------------------------------------------------------*/
const STOPWORDS = new Set(
  "the a an is are was were what why how when where which who whom does do did can could should would explain tell me about of in on at to for and or it its".split(" ")
);

function buildCorpus(ch) {
  const docs = [];
  for (const qa of ch.ask_qa || []) {
    docs.push({ kind: "qa", text: qa.answer || qa.a, hay: (qa.question || qa.q || "") + " " + (qa.answer || qa.a || "") });
  }
  for (const p of ch.pages || []) {
    for (const b of p.blocks || []) {
      if (b.text && b.text.length > 30) docs.push({ kind: "page", text: b.text, hay: b.text });
    }
  }
  for (const kp of ch.key_points || []) {
    docs.push({ kind: "keypoint", text: typeof kp === "string" ? kp : kp.text, hay: typeof kp === "string" ? kp : kp.text });
  }
  return docs.filter((d) => d.text);
}

/* tiny stemmer — enough for formed→form, winds→wind, explanation→explain */
const stem = (w) =>
  w.length > 4 ? w.replace(/(ing|ed|es|s)$/, "") : w;

function answer(question, corpus) {
  const tokens = question.toLowerCase().replace(/[^a-z0-9\s]/g, "").split(/\s+/)
    .filter((w) => w && !STOPWORDS.has(w)).map(stem);
  if (!tokens.length) return null;
  let best = null, bestScore = 0;
  for (const doc of corpus) {
    const hay = stem === undefined ? doc.hay.toLowerCase()
      : doc.hay.toLowerCase().replace(/[^a-z0-9\s]/g, "").split(/\s+/).map(stem).join(" ");
    let hits = 0, score = 0;
    for (const t of tokens) {
      if (hay.includes(t)) { hits++; score += t.length; }
    }
    if (!hits) continue;
    score += hits * 3;                     // coverage bonus — more distinct words hit = better
    if (doc.kind === "qa") score *= 1.4;   // >>> EXPERIMENT weight
    if (score > bestScore) { bestScore = score; best = doc; }
  }
  return bestScore >= 7 ? best.text : null;  // >>> EXPERIMENT threshold
}

window.VoiceKit = { speaker, listener, answer, buildCorpus, matchCommand, VOICE, LISTEN, COMMANDS };
