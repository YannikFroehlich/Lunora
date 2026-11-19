import { describe, expect, it } from "vitest";

import { checklistProgress, dueDateAttributes } from "./checklist-utils.js";

describe("dueDateAttributes", () => {
  const today = new Date(2026, 10, 19, 15);

  it("labels the date without this year and tells overdue, today, upcoming and done apart", () => {
    expect(dueDateAttributes("2026-11-18", false, today)).toEqual({
      "data-due-date": "2026-11-18",
      "data-due-label": "18.11.",
      "data-due-state": "overdue",
    });
    expect(dueDateAttributes("2026-11-19", false, today)["data-due-state"]).toBe("today");
    expect(dueDateAttributes("2027-01-02", false, today)["data-due-label"]).toBe("02.01.2027");
    expect(dueDateAttributes("2026-11-01", true, today)["data-due-state"]).toBe("done");
  });

  it("renders nothing without a valid date", () => {
    expect(dueDateAttributes(null, false, today)).toEqual({});
    expect(dueDateAttributes("morgen", false, today)).toEqual({});
  });
});

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
