import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./add-policy-mode-picker.tsx", import.meta.url),
  "utf8",
);

describe("AddPolicyModePicker — 3-mode entry (NL / Guided / Raw)", () => {
  it("declares exactly the three modes", () => {
    expect(src).toContain('"nl"');
    expect(src).toContain('"guided"');
    expect(src).toContain('"raw"');
  });

  it("badges NL as Recommended", () => {
    expect(src).toContain("Recommended");
  });

  it("badges Guided as Coming soon (disabled in the picker for PR-E1)", () => {
    expect(src).toContain("Coming soon");
    expect(src).toContain('disabled={m.badge === "Coming soon"}');
  });

  it("ships an X cancel control via onCancel", () => {
    expect(src).toContain("onCancel");
    expect(src).toContain('aria-label="Close add policy picker"');
  });
});
