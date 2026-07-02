import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(
  new URL("./prebuilt-components-panel.tsx", import.meta.url),
  "utf8",
);
const hub = readFileSync(new URL("./customize-hub.tsx", import.meta.url), "utf8");

describe("PrebuiltComponentsPanel: PR-P4 always-on components surface", () => {
  it("loads the prebuilt-components inventory read-only", () => {
    expect(src).toContain("getPrebuiltComponents");
    expect(src).toContain('from "@/lib/prebuilt-components-api"');
  });

  it("renders always-on badges and the enforcing subsystem (no toggles)", () => {
    expect(src).toContain("always-on");
    expect(src).toContain("Enforced by:");
    // Read-only: no toggle/checkbox/onToggle wiring.
    expect(src).not.toContain("onToggle");
    expect(src).not.toContain('type="checkbox"');
  });

  it("hides itself when unavailable or empty (fail-soft)", () => {
    expect(src).toContain("setComponents([])");
    expect(src).toContain("components.length === 0) return null");
  });

  it("is mounted under the Rules tab's policies sub-tab", () => {
    expect(hub).toContain("<PrebuiltComponentsPanel />");
    expect(hub).toContain('from "./prebuilt-components-panel"');
  });
});
