import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./packs-panel.tsx", import.meta.url), "utf8");
const hub = readFileSync(new URL("./customize-hub.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../../../lib/packs-api.ts", import.meta.url), "utf8");

describe("PacksPanel: PR-P3 pack contents view", () => {
  it("loads the installed-pack inventory via getPacks", () => {
    expect(src).toContain("getPacks");
    expect(src).toContain('from "@/lib/packs-api"');
  });

  it("groups a pack's provides under friendly category labels (not raw types)", () => {
    expect(src).toContain("PROVIDE_CATEGORY");
    expect(src).toContain("groupProvides");
    expect(src).toContain('tool: "Tools"');
    expect(src).toContain('validator: "Rules"');
    expect(src).toContain('control_plane: "Behaviors"');
  });

  it("splits first-party vs user packs", () => {
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

describe("PacksPanel: install/remove reframe (pack = install unit, not a toggle)", () => {
  it("reads installed/removed, not an enabled/disabled toggle", () => {
    expect(src).toMatch(/installed/);
    expect(src).toMatch(/removed/);
    expect(src).not.toMatch(/role="switch"/);
  });

  it("wires Remove/Install actions to setPackState (install=true / remove=false)", () => {
    expect(src).toContain("setPackState");
    expect(src).toContain("onSetState(pack.packId, true)");
    expect(src).toContain("onSetState(pack.packId, false)");
    expect(src).toContain("pack-remove-");
    expect(src).toContain("pack-install-");
    expect(src).toContain("disabled={busy}");
  });

  it("does not toggle the disclosure when the action button is clicked", () => {
    // The button lives inside <summary>; preventDefault + stopPropagation.
    expect(src).toContain("e.preventDefault()");
    expect(src).toContain("e.stopPropagation()");
  });

  it("is honest that install != active (activation is in Rules/Modes)", () => {
    expect(src).toMatch(/available/);
    expect(src).toMatch(/Modes/);
    // The hub section description makes the same point.
    expect(hub).toMatch(/Installing does not activate/);
  });

  it("contains no em-dash or en-dash (house style)", () => {
    // Escaped code points so the guard does not itself contain them
    // (U+2014 em-dash, U+2013 en-dash).
    expect(src).not.toMatch(/[\u2014\u2013]/);
  });
});

describe("packs-api setPackState", () => {
  it("posts {enabled} to the per-pack state endpoint with the id encoded", () => {
    expect(api).toContain("export async function setPackState");
    expect(api).toContain("/v1/app/packs/");
    expect(api).toContain("/state");
    expect(api).toContain('method: "POST"');
    expect(api).toMatch(/JSON\.stringify\(\{\s*enabled\s*\}\)/);
    expect(api).toContain("encodeURIComponent(packId)");
  });
});
