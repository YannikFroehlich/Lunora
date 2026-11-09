import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

// shopping.js is a plain <script>; without a document it only registers its pure helpers.
const source = readFileSync(new URL("../app/static/js/shopping.js", import.meta.url), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
runInNewContext(source, sandbox);
const { rankSuggestions, singular } = sandbox.LunoraShopping;

const items = [
  "Äpfel",
  "Apfelmus",
  "Apfelsaft",
  "Bananen",
  "Gurke",
  "Salatgurke",
  "Gewürzgurken",
  "Kiwis",
  "Reis",
  "Reibekäse",
  "Milch",
  "Vollmilch",
  "Hafer Milch",
].map((name) => ({ name }));
const names = (query, limit) => rankSuggestions(items, query, limit).map((item) => item.name);

describe("singular", () => {
  it("drops German plural endings but keeps short words intact", () => {
    expect(singular("gurken")).toBe(singular("gurke"));
    expect(singular("bananen")).toBe(singular("banane"));
    expect(singular("kiwis")).toBe("kiwi");
    expect(singular("reis")).toBe("reis");
    expect(singular("eis")).toBe("eis");
  });
});

describe("rankSuggestions", () => {
  it("finds the plural for a singular, ignoring umlauts, and puts it first", () => {
    expect(names("Apfel")).toEqual(["Äpfel", "Apfelmus", "Apfelsaft"]);
    expect(names("banane")).toEqual(["Bananen"]);
    expect(names("kiwi")).toEqual(["Kiwis"]);
  });

  it("finds singular names and compounds for a plural", () => {
    expect(names("Gurken")).toEqual(["Gurke", "Gewürzgurken", "Salatgurke"]);
  });

  it("puts prefix matches before word matches before the rest", () => {
    expect(names("mil")).toEqual(["Milch", "Hafer Milch", "Vollmilch"]);
  });

  it("does not turn short words into prefixes of others", () => {
    expect(names("reis")).toEqual(["Reis"]);
  });

  it("returns nothing for blank input and honours the limit", () => {
    expect(names("  ")).toEqual([]);
    expect(names("a", 2)).toHaveLength(2);
  });
});
