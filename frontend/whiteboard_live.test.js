import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

// whiteboard_format.js is a plain <script>; load it into a sandbox that has the Web Crypto API.
const source = readFileSync(new URL("../app/static/js/whiteboard_format.js", import.meta.url), "utf8");
const sandbox = { crypto: globalThis.crypto, TextEncoder };
sandbox.globalThis = sandbox;
runInNewContext(source, sandbox);
const format = sandbox.LunoraWhiteboardFormat;

// The board as the server sends it; WhiteboardLiveTests.test_hash_matches_the_editor computes the
// same hash in Python, so both sides prove they serialize a board alike.
const SERVER_BOARD = JSON.parse(
  '[{"id": "a", "type": "text", "color": "blue", "x": 10.0, "y": -0.0, "size": "m", "text": "Gr\\u00fc\\u00dfe \\"\\u00dc\\" \\u2028 \\ud83d\\ude00"}, {"id": "b", "type": "stroke", "color": "default", "width": 4, "points": [0.0, 0.0, 12.3, 5.5]}, {"id": "c", "type": "rect", "color": "red", "width": 2, "x": 1.0, "y": 2.0, "w": 30.0, "h": 40.0, "fill": true}]',
);
const SERVER_HASH = "a36b2bb342f000808b34ec4ec8fa7f9430d6ce7654e733a156b1c745d40cf3d6";

function element(id, x = 0) {
  return { id, type: "rect", color: "default", width: 2, x, y: 0, w: 10, h: 10, fill: false };
}

// A small seeded generator, so a failing case can be replayed.
function random(seed) {
  let state = seed;
  return () => {
    state = (state * 1103515245 + 12345) % 2147483648;
    return state / 2147483648;
  };
}

function shuffleSome(list, next) {
  const result = [...list];
  for (let i = 0; i < 3 && result.length > 1; i += 1) {
    const from = Math.floor(next() * result.length);
    const [item] = result.splice(from, 1);
    result.splice(Math.floor(next() * (result.length + 1)), 0, item);
  }
  return result;
}

function randomEdit(before, next, prefix) {
  let after = before.filter(() => next() > 0.2);
  after = after.map((item) => (next() < 0.2 ? { ...item, x: item.x + 1 } : item));
  for (let i = 0; i < 3; i += 1) {
    if (next() < 0.5) after.splice(Math.floor(next() * (after.length + 1)), 0, element(`${prefix}${i}`));
  }
  return next() < 0.5 ? shuffleSome(after, next) : after;
}

describe("whiteboard live editing", () => {
  it("diffs into operations that rebuild the edited board", () => {
    for (let seed = 1; seed <= 300; seed += 1) {
      const next = random(seed);
      const before = Array.from({ length: Math.floor(next() * 8) }, (_, i) => element(`e${i}`, i));
      const after = randomEdit(before, next, "n");
      expect(format.applyOps(before, format.diffElements(before, after))).toEqual(after);
    }
  });

  it("moves only what left its place", () => {
    const [a, b, c, d] = ["a", "b", "c", "d"].map((id) => element(id));
    expect(format.diffElements([a, b, c, d], [a, c, d, b])).toEqual([{ op: "move", id: "b", after: "d" }]);
    const changed = { ...c, x: 5 };
    expect(format.diffElements([a, b, c], [a, b, changed])).toEqual([{ op: "put", element: changed }]);
  });

  it("gives every editor the server's board when they change it at the same time", () => {
    for (let seed = 1; seed <= 200; seed += 1) {
      const next = random(seed);
      const start = Array.from({ length: 6 }, (_, i) => element(`e${i}`, i));
      const opsA = format.diffElements(start, randomEdit(start, next, "a"));
      const opsB = format.diffElements(start, randomEdit(start, next, "b"));
      // The server takes A's push first, then B's.
      const log = [...opsA, ...opsB];
      const server = format.applyOps(start, log);
      // Both confirm the log in server order; B, while its push is still on the way, shows A's
      // operations confirmed with its own pending on top, which already is the final board.
      const confirmed = format.applyOps(start, log);
      const editorBPending = format.applyOps(format.applyOps(start, opsA), opsB);
      expect(confirmed).toEqual(server);
      expect(editorBPending).toEqual(server);
      expect(new Set(server.map((item) => item.id)).size).toBe(server.length);
      const addedByB = opsB.filter((op) => op.op === "put" && op.element.id.startsWith("b"));
      addedByB.forEach((op) => expect(server).toContainEqual(op.element));
    }
  });

  it("places elements whose anchor is gone on top", () => {
    const [a, b] = [element("a"), element("b")];
    expect(format.applyOps([a], [{ op: "put", element: b, after: "missing" }]).map((item) => item.id)).toEqual([
      "a",
      "b",
    ]);
    expect(format.applyOps([a], [{ op: "move", id: "missing", after: null }])).toEqual([a]);
    expect(format.applyOps([a, b], [{ op: "put", element: b, after: null }]).map((item) => item.id)).toEqual([
      "b",
      "a",
    ]);
  });

  it("hashes a board exactly like the server", async () => {
    expect(await format.boardHash(SERVER_BOARD)).toBe(SERVER_HASH);
  });
});
