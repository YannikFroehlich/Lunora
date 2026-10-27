import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

// focus_timer.js is a plain <script>; without a document it only registers its pure helpers.
const source = readFileSync(new URL("../app/static/js/focus_timer.js", import.meta.url), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
runInNewContext(source, sandbox);
const timer = sandbox.LunoraFocusTimer;

describe("formatClock", () => {
  it("rounds partial seconds up and pads minutes and seconds", () => {
    expect(timer.formatClock(25 * 60)).toBe("25:00");
    expect(timer.formatClock(59.2)).toBe("01:00");
    expect(timer.formatClock(9)).toBe("00:09");
    expect(timer.formatClock(-3)).toBe("00:00");
  });
});

describe("nextPhase", () => {
  it("alternates focus and short breaks and gives a long break every n-th focus block", () => {
    let state = { phase: "focus", completedFocus: 0 };
    const phases = [];
    for (let step = 0; step < 8; step += 1) {
      state = timer.nextPhase(state.phase, state.completedFocus, 4);
      phases.push(state.phase);
    }
    expect(phases).toEqual(["short", "focus", "short", "focus", "short", "focus", "long", "focus"]);
    expect(state.completedFocus).toBe(4);
  });
});

describe("cleanSettings", () => {
  it("keeps valid minutes and falls back to defaults for anything else", () => {
    expect({ ...timer.cleanSettings({ focus: "50", short: 0, long: "abc", longEvery: 3 }) }).toEqual({
      focus: 50,
      short: 5,
      long: 15,
      longEvery: 3,
    });
    expect({ ...timer.cleanSettings(undefined) }).toEqual({ ...timer.DEFAULT_SETTINGS });
  });
});
