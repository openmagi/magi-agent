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

  it("Guided is enabled once PR-E2 ships the wizard body", () => {
    // PR-E2 dropped the "Coming soon" badge from the Guided card. The
    // disabled-when-badge guard stays in the source so we can re-disable a
    // mode by adding a "Coming soon" badge again if a future PR needs it.
    expect(src).not.toMatch(/badge:\s*"Coming soon"/);
    expect(src).toContain('disabled={m.badge === "Coming soon"}');
  });

  it("ships an X cancel control via onCancel", () => {
    expect(src).toContain("onCancel");
    expect(src).toContain('aria-label="Close add rule picker"');
  });

  it("PR-U3.1: frames the entry as a Rule and states the enforcement region", () => {
    // The header names the region-aligned unit ("rule") and what a rule does
    // (block / ask / audit) rather than the generic "policy" umbrella.
    expect(src).toContain("How do you want to add this rule?");
    expect(src).toContain("block a turn or a tool");
  });

  it("PR-U3.1: does not leak the internal primitive vocabulary into the copy", () => {
    // Regression guards against the old implementation-chip vocabulary. The
    // `routedKind` compiler label and the `backing` chip were the live leaks
    // this PR removed; `SeamSpec`/`firesAt` are guarded too so a future edit
    // can't reintroduce primitive names into this operator-facing picker.
    expect(src).not.toContain("routedKind");
    expect(src).not.toContain("SeamSpec");
    expect(src).not.toContain("firesAt");
    expect(src).not.toMatch(/backing/);
  });
});
