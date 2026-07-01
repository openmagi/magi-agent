import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./packs-panel.tsx", import.meta.url), "utf8");
const hub = readFileSync(new URL("./customize-hub.tsx", import.meta.url), "utf8");

describe("PacksPanel: PR-P3 pack contents view", () => {
  it("loads the installed-pack inventory via getPacks", () => {
    expect(src).toContain("getPacks");
    expect(src).toContain('from "@/lib/packs-api"');
  });

  it("groups a pack's provides under friendly category labels (not raw types)", () => {
    expect(src).toContain("PROVIDE_CATEGORY");
    expect(src).toContain("groupProvides");
    // The 13 ProvidesType kinds map to operator vocabulary.
    expect(src).toContain('tool: "Tools"');
    expect(src).toContain('validator: "Rules"');
    expect(src).toContain('control_plane: "Behaviors"');
  });

  it("splits first-party vs user packs and shows an enabled/disabled badge", () => {
    expect(src).toContain('p.origin === "first_party"');
    expect(src).toContain('p.origin === "user"');
    expect(src).toContain("pack.enabled");
  });

  it("renders each pack's provided refs so contents are visible", () => {
    expect(src).toContain("pack.provides.length");
    expect(src).toContain("e.ref");
  });

  it("is mounted in the hub's Packs section", () => {
    expect(hub).toContain("<PacksPanel />");
    expect(hub).toContain('from "./packs-panel"');
  });
});
