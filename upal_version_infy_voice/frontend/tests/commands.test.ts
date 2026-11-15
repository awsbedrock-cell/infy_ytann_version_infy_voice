// Mirror of tests/test_commands.py for the new wake-word commands — the
// frontend and backend parsers must agree.
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseCommand } from "../src/infy/commands.ts";

const cmd = (t: string) => parseCommand(t)[0];

test("infy start / stop still parse", () => {
  assert.equal(cmd("Infy Start"), "start");
  assert.equal(cmd("Infy Stop"), "stop");
  assert.equal(cmd("infy stop repeating"), "stop");
  assert.equal(cmd("infy hindi mein shuru karo"), "start");
});

test("infy repeat variants", () => {
  for (const t of ["infy repeat", "Infy, repeat that", "hey infy say that again",
    "infy dobara bolo", "infy phir se bolo", "इन्फी दोबारा बोलो",
    "इन्फी फिर से", "in fee repeat", "infy once more please"]) {
    assert.equal(cmd(t), "repeat", t);
  }
});

test("infy hindi / english variants", () => {
  for (const t of ["infy hindi", "Infy Hindi", "infy hindi mein bolo",
    "infy speak in hindi", "infy in hindi please", "इन्फी हिंदी में बोलो",
    "infy ab hindi mein baat karo"]) {
    assert.equal(cmd(t), "hindi", t);
  }
  for (const t of ["infy english", "Infy English", "infy english mein bolo",
    "infy speak in english", "इन्फी इंग्लिश", "इन्फी अंग्रेज़ी में बोलो"]) {
    assert.equal(cmd(t), "english", t);
  }
});

test("repeat/language need the wake word and the whole utterance", () => {
  for (const t of ["repeat", "hindi", "english", "say that again", "dobara bolo",
    "what is hindi", "infy what is pressure in hindi",
    "infy how do you say wind in english",
    "infy repeat the definition of humidity", "infy english grammar kya hai",
    "infy why does the wind repeat"]) {
    const c = cmd(t);
    assert.ok(c !== "repeat" && c !== "hindi" && c !== "english", t);
  }
});
