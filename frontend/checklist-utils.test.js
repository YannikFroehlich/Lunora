import { describe, expect, it } from "vitest";

import { checklistProgress } from "./checklist-utils.js";

describe("checklistProgress", () => {
  it("formats done of total", () => {
    expect(checklistProgress(2, 5)).toEqual({ done: 2, total: 5, label: "2/5", complete: false });
  });

  it("marks a fully checked list as complete", () => {
    expect(checklistProgress(3, 3).complete).toBe(true);
  });

  it("treats missing or invalid counts as no checklist", () => {
    expect(checklistProgress(0, 0)).toEqual({ done: 0, total: 0, label: "", complete: false });
    expect(checklistProgress(undefined, null).total).toBe(0);
  });

  it("clamps done into the valid range", () => {
    expect(checklistProgress(9, 4).done).toBe(4);
    expect(checklistProgress(-1, 4).done).toBe(0);
  });
});
