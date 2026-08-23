import { describe, expect, it } from "vitest";

import { normalizeLinkHref } from "./link-utils.js";


describe("normalizeLinkHref", () => {
  it("ergänzt bei normalen Domains HTTPS", () => {
    expect(normalizeLinkHref("youtube.com")).toBe("https://youtube.com");
    expect(normalizeLinkHref(" www.youtube.com/watch?v=1 ")).toBe("https://www.youtube.com/watch?v=1");
  });

  it("behält vorhandene Protokolle bei", () => {
    expect(normalizeLinkHref("http://example.com")).toBe("http://example.com");
    expect(normalizeLinkHref("mailto:test@example.com")).toBe("mailto:test@example.com");
  });

  it("erkennt eine allein eingegebene E-Mail-Adresse", () => {
    expect(normalizeLinkHref("test@example.com")).toBe("mailto:test@example.com");
  });
});
