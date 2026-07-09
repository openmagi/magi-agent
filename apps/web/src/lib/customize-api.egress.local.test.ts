import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("./customize-api.ts", import.meta.url), "utf8");

describe("customize-api: egress-guard allowlist + mode (U4b)", () => {
  it("exposes getEgressAllowlist targeting the GET endpoint", () => {
    expect(src).toContain("export async function getEgressAllowlist");
    expect(src).toContain("/v1/app/customize/egress-allowlist");
  });

  it("exposes putEgressAllowlist as a PUT sending {allowlist}", () => {
    expect(src).toContain("export async function putEgressAllowlist");
    expect(src).toMatch(
      /putEgressAllowlist[\s\S]{0,400}method: "PUT"[\s\S]{0,300}JSON\.stringify\(\{\s*allowlist\s*\}\)/,
    );
  });

  it("exposes putEgressMode as a PUT sending {mode} to the mode endpoint", () => {
    expect(src).toContain("export async function putEgressMode");
    expect(src).toContain("/v1/app/customize/egress-mode");
    expect(src).toMatch(
      /putEgressMode[\s\S]{0,400}method: "PUT"[\s\S]{0,300}JSON\.stringify\(\{\s*mode\s*\}\)/,
    );
  });

  it("constrains the mode argument to audit | block", () => {
    expect(src).toMatch(/putEgressMode[\s\S]{0,200}mode:\s*"audit"\s*\|\s*"block"/);
  });

  it("declares the egress_guard shape on CustomizeOverrides", () => {
    expect(src).toMatch(/egress_guard\?:\s*\{\s*allowlist:\s*string\[\];\s*mode:\s*string\s*\}/);
  });

  it("each helper throws on non-2xx (caller surfaces + reverts)", () => {
    for (const fn of ["getEgressAllowlist", "putEgressAllowlist", "putEgressMode"]) {
      const slice = src.slice(src.indexOf(`export async function ${fn}`));
      expect(slice).toMatch(/if \(!res\.ok\) throw new Error/);
    }
  });
});
