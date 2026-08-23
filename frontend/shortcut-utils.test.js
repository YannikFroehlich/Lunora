import { beforeEach, describe, expect, it, vi } from "vitest";

import { eventToShortcut, findShortcutConflict, mergeShortcuts, shortcutMatches } from "./shortcut-utils.js";

describe("note shortcut utilities", () => {
  beforeEach(() => {
    vi.stubGlobal("navigator", { platform: "Win32" });
  });

  it("normalizes control combinations to Mod", () => {
    expect(eventToShortcut({ key: "b", ctrlKey: true, metaKey: false, altKey: false, shiftKey: false })).toBe("Mod+B");
  });

  it("matches normalized shortcuts", () => {
    expect(shortcutMatches({ key: "s", ctrlKey: true, metaKey: false, altKey: false, shiftKey: false }, "Mod+S")).toBe(true);
  });

  it("merges saved overrides while preserving labels", () => {
    const result = mergeShortcuts({ save: { label: "Speichern", shortcut: "Mod+S" } }, { save: "Alt+S" });
    expect(result.save).toEqual({ label: "Speichern", shortcut: "Alt+S" });
  });

  it("detects duplicate assignments", () => {
    const commands = { save: { shortcut: "Mod+S" }, bold: { shortcut: "Mod+B" } };
    expect(findShortcutConflict(commands, "bold", "Mod+S")).toBe("save");
  });
});
