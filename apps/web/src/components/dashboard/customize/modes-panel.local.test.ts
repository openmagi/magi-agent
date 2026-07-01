import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./modes-panel.tsx", import.meta.url), "utf8");

describe("ModesPanel: PR-U3.3 intent-first, plain-language mode composer", () => {
  it("opens the new-mode editor with a 'describe the stance' intent lead-in", () => {
    // The lead-in only renders when creating (editor.modeId is null) so the
    // edit path stays compact.
    expect(src).toContain("Describe the stance you want the agent to take");
    expect(src).toMatch(/editor\.modeId \? null : \(/);
  });

  it("uses plain-language field labels instead of the internal field names", () => {
    expect(src).toContain("Name this stance");
    expect(src).toContain("How the agent should behave");
    expect(src).toContain("How strict are approvals?");
    expect(src).toContain("Turn tools off");
    expect(src).toContain("Turn extra tools on");
    expect(src).toContain("Rules active in this mode");
  });

  it("retires the raw System-prompt / Exclude / Scoped-policies visible labels", () => {
    // These were the pre-U3.3 labels that leaked the wire-shape field names.
    // (The element ids like `mode-exclude`/`mode-policies` stay; only the
    // visible label text changed.)
    expect(src).not.toContain("System prompt (soft)");
    expect(src).not.toContain("Exclude tools");
    expect(src).not.toContain("Scoped policies");
  });

  it("points the empty-rules hint at the renamed 'Rules' tab (PR-U1), not 'Policies'", () => {
    expect(src).toContain("Create one under <strong>Rules</strong>");
  });
});
