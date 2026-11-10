import { describe, expect, it } from "vitest";

import { editingLabel } from "./presence-utils.js";

describe("editingLabel", () => {
  it("nennt nur, wer gerade schreibt", () => {
    expect(editingLabel([{ name: "Mira", editing: false }])).toBe("");
    expect(editingLabel([{ name: "Mira", editing: true }, { name: "Tom" }])).toBe("Mira bearbeitet gerade");
  });

  it("fasst mehrere zusammen", () => {
    const two = [
      { name: "Mira", editing: true },
      { name: "Tom", editing: true },
    ];
    expect(editingLabel(two)).toBe("Mira und Tom bearbeiten gerade");
    expect(editingLabel([...two, { name: "Lea", editing: true }])).toBe("3 Personen bearbeiten gerade");
  });
});
