// Simulated mic/audio-event tests for the browser voice loop (no browser,
// no microphone). Run: node --test frontend/tests/voice-logic.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  TranscriptFilter, BargeInDetector, isEchoText, norm, srRestartDelay,
  reconnectDelay, countsAsSttFailure, DUP_FINAL_MS,
} from "../src/infy/voice-logic.ts";
import { parseCommand } from "../src/infy/commands.ts";

const BOT = "Low pressure areas pull air in from high pressure areas around them.";
const ctx = (o: Partial<{ isCommand: boolean; gated: boolean; echoWindow: boolean; now: number }> = {}) =>
  ({ isCommand: false, gated: false, echoWindow: false, now: 0, ...o });

test("Devanagari survives normalization", () => {
  assert.equal(norm("हवा क्या है?"), "हवा क्या है");
});

test("assistant audio heard while audible is gated; commands pass", () => {
  const f = new TranscriptFilter();
  assert.equal(f.check("pull air in from high", true, ctx({ gated: true })), "gated");
  assert.equal(f.check("pull air", false, ctx({ gated: true })), "gated");
  assert.equal(f.check("infy stop", true, ctx({ gated: true, isCommand: true })), "send");
});

test("echo tail right after playback is dropped, real follow-up later is not", () => {
  const f = new TranscriptFilter();
  f.addBot(BOT, 0);
  assert.equal(f.check("pull air in from high pressure", true, ctx({ echoWindow: true, now: 500 })), "echo");
  assert.equal(f.check("why do low pressure areas pull air", true, ctx({ echoWindow: false, now: 4000 })), "send");
});

test("duplicate Chrome final within window is one turn", () => {
  const f = new TranscriptFilter();
  assert.equal(f.check("what is a cyclone", true, ctx({ now: 0 })), "send");
  assert.equal(f.check("What is a cyclone?", true, ctx({ now: 800 })), "dup");
  assert.equal(f.check("what is a cyclone", true, ctx({ now: DUP_FINAL_MS + 900 })), "send");
});

test("different Hindi finals are not collapsed into duplicates", () => {
  const f = new TranscriptFilter();
  assert.equal(f.check("हवा क्या है", true, ctx({ now: 0 })), "send");
  assert.equal(f.check("दबाव क्या है", true, ctx({ now: 300 })), "send");
});

test("interim results are forwarded but never deduped as finals", () => {
  const f = new TranscriptFilter();
  assert.equal(f.check("what is", false, ctx()), "send");
  assert.equal(f.check("what is", false, ctx()), "send");
});

test("simulated 10-turn conversation: 10 questions sent, 0 phantom turns", () => {
  const f = new TranscriptFilter();
  let t = 0, sent = 0;
  for (let i = 0; i < 10; i++) {
    const q = `question ${i} why does wind blow from high pressure`;
    if (f.check(q, true, ctx({ now: t })) === "send") sent++;
    t += 2000;
    const answer = `Answer ${i}: ${BOT}`;
    f.addBot(answer, t);
    for (const echo of [answer.slice(0, 40), "areas pull air in from high"]) {
      const v1 = f.check(echo, true, ctx({ gated: true, now: t + 500 }));
      const v2 = f.check(echo, true, ctx({ echoWindow: true, now: t + 3200 }));
      assert.notEqual(v1, "send"); assert.notEqual(v2, "send");
    }
    t += 6000;
  }
  assert.equal(sent, 10);
});

test("STT restart: immediate after silence, backoff only on real errors", () => {
  assert.equal(countsAsSttFailure("no-speech"), false);
  assert.equal(countsAsSttFailure("aborted"), false);
  assert.equal(countsAsSttFailure("network"), true);
  assert.equal(srRestartDelay(0), 0);
  assert.equal(srRestartDelay(1), 500);
  assert.equal(srRestartDelay(10), 5000);
});

test("reconnect backoff is bounded", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 9].map(reconnectDelay), [500, 1000, 2000, 4000, 8000, 8000]);
});

function feed(d: BargeInDetector, frames: [number, number, boolean][]) {
  let now = 1000;
  const fired: number[] = [];
  for (const [dur, rms, playing] of frames)
    for (let t = 0; t < dur; t += 16) { now += 16; if (d.tick(rms, playing, now)) fired.push(now); }
  return fired;
}

test("barge-in: steady speaker echo never self-interrupts", () => {
  assert.equal(feed(new BargeInDetector(), [[5000, 0.05, true]]).length, 0);
});

test("barge-in: sustained user speech over playback interrupts once (cooldown)", () => {
  assert.equal(feed(new BargeInDetector(), [[1000, 0.03, true], [600, 0.3, true]]).length, 1);
});

test("barge-in: short click or AEC-convergence burst is ignored", () => {
  assert.equal(feed(new BargeInDetector(), [[1000, 0.03, true], [100, 0.4, true], [1000, 0.03, true]]).length, 0);
  assert.equal(feed(new BargeInDetector(), [[200, 0.4, true], [1000, 0.03, true]]).length, 0);
});

test("barge-in: speech while Infy is silent is not an interrupt", () => {
  assert.equal(feed(new BargeInDetector(), [[2000, 0.3, false]]).length, 0);
});

test("echo check ignores single words", () => {
  assert.equal(isEchoText("pressure", [norm(BOT)]), false);
});

test("voice commands: English, Hinglish, Devanagari; no false stop", () => {
  assert.equal(parseCommand("infy stop")[0], "stop");
  assert.equal(parseCommand("इन्फी रुको")[0], "stop");
  assert.equal(parseCommand("bus stop")[0], null);
});
