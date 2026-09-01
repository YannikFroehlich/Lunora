import { describe, expect, it } from "vitest";

import { SLASH_MENU_ITEMS, filterSlashMenuItems } from "./slash-menu.js";

describe("filterSlashMenuItems", () => {
  it("gibt bei leerem Query alle Einträge zurück", () => {
    expect(filterSlashMenuItems(SLASH_MENU_ITEMS, "")).toEqual(SLASH_MENU_ITEMS);
    expect(filterSlashMenuItems(SLASH_MENU_ITEMS, "   ")).toEqual(SLASH_MENU_ITEMS);
  });

  it("filtert per Teilstring im Label", () => {
    const result = filterSlashMenuItems(SLASH_MENU_ITEMS, "tabelle");
    expect(result.map((item) => item.action)).toEqual(["insertTable"]);
  });

  it("filtert per Keyword-Alias", () => {
    const result = filterSlashMenuItems(SLASH_MENU_ITEMS, "photo");
    expect(result.map((item) => item.action)).toEqual(["image"]);
  });

  it("ist case-insensitive", () => {
    expect(filterSlashMenuItems(SLASH_MENU_ITEMS, "CODE").map((item) => item.action)).toEqual(["codeBlock"]);
  });

  it("gibt eine leere Liste zurück, wenn nichts passt", () => {
    expect(filterSlashMenuItems(SLASH_MENU_ITEMS, "xyz123")).toEqual([]);
  });
});
