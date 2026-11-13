import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

// whiteboard_format.js is a plain <script> (not bundled), so load it into a sandbox and read its global.
const source = readFileSync(new URL("../app/static/js/whiteboard_format.js", import.meta.url), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
runInNewContext(source, sandbox);
const format = sandbox.LunoraWhiteboardFormat;

describe("whiteboard stroke simplification", () => {
  it("drops points on a straight line but keeps the ends", () => {
    expect(format.simplifyStroke([0, 0, 1, 0.01, 2, 0, 3, -0.01, 10, 0], 0.5)).toEqual([0, 0, 10, 0]);
  });

  it("keeps corners", () => {
    expect(format.simplifyStroke([0, 0, 5, 0, 10, 0, 10, 5, 10, 10], 0.5)).toEqual([0, 0, 10, 0, 10, 10]);
  });

  it("keeps a single dot and rounds to one decimal", () => {
    expect(format.simplifyStroke([1.26, 2.04], 0.5)).toEqual([1.3, 2]);
  });

  it("clamps coordinates to the drawing area", () => {
    expect(format.simplifyStroke([0, 0, 1e9, -1e9], 0.5)).toEqual([0, 0, 100000, -100000]);
  });

  it("drops points that barely moved", () => {
    expect(format.simplifyStroke([0, 0, 0.1, 0.1, 0.2, 0, 5, 5, 5, 10], 0.5)).toEqual([0, 0, 5, 5, 5, 10]);
  });

  it("fits a wild stroke into the point limit quickly", () => {
    const points = [];
    for (let i = 0; i < 50000; i += 1) points.push(i, i % 2 ? 3 : -3);
    const started = Date.now();
    const result = format.simplifyStroke(points, 0.5);
    expect(Date.now() - started).toBeLessThan(2000);
    expect(result.length / 2).toBeLessThanOrEqual(format.LIMITS.strokePoints);
    expect(result.slice(0, 2)).toEqual([0, -3]);
    expect(result.slice(-2)).toEqual([49999, 3]);
  });
});

describe("whiteboard stroke geometry", () => {
  it("draws a dot, a line and a smoothed curve", () => {
    expect(format.strokePath([])).toBe("");
    expect(format.strokePath([1, 2])).toBe("M1 2L1 2");
    expect(format.strokePath([0, 0, 10, 0])).toBe("M0 0L10 0");
    expect(format.strokePath([0, 0, 10, 0, 10, 10])).toBe("M0 0Q10 0 10 5L10 10");
  });

  it("hits a stroke within the tolerance only", () => {
    const points = [0, 0, 10, 0, 10, 10];
    expect(format.hitsStroke(points, 5, 2, 5, 2, 3)).toBe(true);
    expect(format.hitsStroke(points, 12, 9, 12, 9, 3)).toBe(true);
    expect(format.hitsStroke(points, 5, 5, 5, 5, 3)).toBe(false);
    expect(format.hitsStroke([4, 4], 5, 5, 5, 5, 2)).toBe(true);
    expect(format.hitsStroke([4, 4], 8, 8, 8, 8, 2)).toBe(false);
  });

  it("hits a stroke the eraser swept across between two samples", () => {
    const points = [0, 0, 10, 0, 10, 10];
    expect(format.hitsStroke(points, 5, -50, 5, 50, 1)).toBe(true);
    expect(format.hitsStroke(points, 20, -50, 20, 50, 1)).toBe(false);
    expect(format.hitsStroke([4, 4], 4, -50, 4, 50, 1)).toBe(true);
  });

  it("measures the board and counts its points", () => {
    const doc = {
      version: 1,
      elements: [
        { id: "a", type: "stroke", points: [0, 5, 10, -5] },
        { id: "b", type: "stroke", points: [-3, 0] },
      ],
    };
    expect(format.documentBounds(doc)).toEqual({ x1: -3, y1: -5, x2: 10, y2: 5 });
    expect(format.pointCount(doc)).toBe(3);
    expect(format.documentBounds(format.emptyDocument())).toBeNull();
  });

  it("normalizes a broken document to an empty board", () => {
    expect(format.normalizeDocument(null)).toEqual({ version: 1, elements: [] });
    expect(format.normalizeDocument({ elements: "x" }).elements).toEqual([]);
  });
});

describe("whiteboard shapes and text", () => {
  const rect = { id: "r", type: "rect", color: "default", width: 2, x: 0, y: 0, w: 100, h: 50, fill: false };
  const ellipse = { id: "e", type: "ellipse", color: "default", width: 2, x: 0, y: 0, w: 100, h: 50, fill: true };
  const arrow = { id: "a", type: "arrow", color: "default", width: 4, x1: 10, y1: 20, x2: -30, y2: 5 };
  const text = { id: "t", type: "text", color: "default", x: 10, y: 10, size: "m", text: "ab\nabcd" };

  it("measures every element type", () => {
    expect(format.elementBounds(rect)).toEqual({ x1: 0, y1: 0, x2: 100, y2: 50 });
    expect(format.elementBounds(arrow)).toEqual({ x1: -30, y1: 5, x2: 10, y2: 20 });
    // Two lines, the longer one decides the width; a custom measure replaces the estimate.
    expect(format.elementBounds(text, (line, size) => line.length * size)).toEqual({ x1: 10, y1: 10, x2: 106, y2: 70 });
    expect(format.documentBounds({ elements: [rect, arrow] })).toEqual({ x1: -30, y1: 0, x2: 100, y2: 50 });
  });

  it("hits an outlined rectangle on its border only, a filled ellipse anywhere inside", () => {
    expect(format.hitsElement(rect, 50, 1, 50, 1, 3)).toBe(true);
    expect(format.hitsElement(rect, 50, 25, 50, 25, 3)).toBe(false);
    expect(format.hitsElement({ ...rect, fill: true }, 50, 25, 50, 25, 3)).toBe(true);
    expect(format.hitsElement(ellipse, 50, 25, 50, 25, 3)).toBe(true);
    // The corner of the bounding box lies outside the ellipse itself.
    expect(format.hitsElement(ellipse, 2, 2, 2, 2, 3)).toBe(false);
    expect(format.hitsElement(arrow, -10, 12.5, -10, 12.5, 2)).toBe(true);
    expect(format.hitsElement(text, 20, 20, 20, 20, 0)).toBe(true);
    expect(format.hitsElement(text, 200, 20, 200, 20, 0)).toBe(false);
  });

  it("hits a shape the eraser swept across", () => {
    expect(format.hitsElement(rect, 50, -20, 50, 20, 1)).toBe(true);
    expect(format.hitsElement(rect, 150, -20, 150, 20, 1)).toBe(false);
  });

  it("moves elements, rounds them and keeps their keys", () => {
    const moved = format.moveElement(arrow, 0.04, 1.26);
    expect(moved).toEqual({ ...arrow, x1: 10, y1: 21.3, x2: -30, y2: 6.3 });
    expect(Object.keys(format.moveElement(rect, 1, 1)).sort()).toEqual(Object.keys(rect).sort());
    expect(format.moveElement({ id: "s", type: "stroke", points: [0, 0, 1, 1] }, 1, 2).points).toEqual([1, 2, 2, 3]);
    expect(format.moveElement(text, -10, 0).x).toBe(0);
  });

  it("spans a box in any drag direction and squares it on request", () => {
    expect(format.normalizeBox(10, 10, 0, 30, false)).toEqual({ x: 0, y: 10, w: 10, h: 20 });
    expect(format.normalizeBox(10, 10, 0, 30, true)).toEqual({ x: -10, y: 10, w: 20, h: 20 });
  });

  it("puts the arrow tip at the end point", () => {
    expect(format.arrowHead(0, 0, 100, 0, 2)).toMatch(/^M\S+ \S+L100 0L\S+ \S+$/);
    expect(format.arrowHead(0, 0, 0, 0, 2)).toContain("L0 0L");
  });
});
