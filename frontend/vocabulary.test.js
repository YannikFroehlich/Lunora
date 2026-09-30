import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

// vocabulary.js is a plain <script>; without a document it only registers its pure helpers.
const source = readFileSync(new URL("../app/static/js/vocabulary.js", import.meta.url), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
runInNewContext(source, sandbox);
const vocabulary = sandbox.LunoraVocabulary;

describe("checkAnswer", () => {
  it("accepts every listed meaning, ignoring case, spacing and trailing punctuation", () => {
    expect(vocabulary.checkAnswer("  haus ", "Haus, Gebäude")).toBe("correct");
    expect(vocabulary.checkAnswer("Gebäude!", "Haus, Gebäude")).toBe("correct");
    expect(vocabulary.checkAnswer("Wohnung", "Haus / Gebäude")).toBe("wrong");
    expect(vocabulary.checkAnswer("", "Haus")).toBe("wrong");
  });

  it("treats optional parts in brackets as optional", () => {
    expect(vocabulary.checkAnswer("run", "(to) run")).toBe("correct");
    expect(vocabulary.checkAnswer("to run", "(to) run")).toBe("correct");
  });

  it("tolerates small typos and missing accents on longer words only", () => {
    expect(vocabulary.checkAnswer("Gebaude", "Gebäude")).toBe("typo");
    expect(vocabulary.checkAnswer("wetter", "Wettter")).toBe("typo");
    expect(vocabulary.checkAnswer("cat", "car")).toBe("wrong");
    expect(vocabulary.checkAnswer("Schmetterlnig", "Schmetterling")).toBe("typo");
  });
});

describe("pickDistractors", () => {
  it("returns distinct wrong answers only", () => {
    const cards = [
      { term: "a", translation: "Haus" },
      { term: "b", translation: "haus" },
      { term: "c", translation: "Baum" },
      { term: "d", translation: "Auto" },
    ];
    const options = vocabulary.pickDistractors(cards, cards[0], "translation");
    expect([...options].sort()).toEqual(["Auto", "Baum"]);
  });
});

describe("editDistance", () => {
  it("counts single edits", () => {
    expect(vocabulary.editDistance("kitten", "sitting")).toBe(3);
    expect(vocabulary.editDistance("", "abc")).toBe(3);
  });
});
