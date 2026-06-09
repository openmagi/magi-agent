import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./customize-api.ts", import.meta.url), "utf8");

describe("customize-api local runtime contract", () => {
  it("targets the local /v1/app/customize endpoint via the agent fetch hook", () => {
    expect(source).toContain("useAgentFetch");
    expect(source).toContain("/v1/app/customize");
    expect(source).not.toContain("/api/bots/");
    expect(source).not.toContain("useAuthFetch");
  });

  it("declares the catalog/overrides interfaces from the backend contract", () => {
    expect(source).toContain("interface RecipeItem");
    expect(source).toContain("interface HarnessPresetItem");
    expect(source).toContain("interface HookItem");
    expect(source).toContain("interface ToolItem");
    expect(source).toContain("interface CustomizeCatalog");
    expect(source).toContain("interface CustomizeOverrides");
    expect(source).toContain("interface CustomizeResponse");
  });

  it("preserves the camelCase catalog vs snake_case overrides asymmetry", () => {
    expect(source).toContain("harnessPresets");
    expect(source).toContain("harness_presets");
    expect(source).toContain("custom_rules");
  });

  it("exposes the useCustomize loading/error/reload contract", () => {
    expect(source).toContain("export function useCustomize");
    expect(source).toContain("reload");
    expect(source).toContain("loading");
    expect(source).toContain("error");
  });
});
