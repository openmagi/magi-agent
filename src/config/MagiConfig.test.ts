import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

import { loadMagiConfig, resetMagiConfig } from "./MagiConfig.js";

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "magi-config-test-"));
}

describe("MagiConfig", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = makeTmpDir();
    resetMagiConfig();
  });

  afterEach(() => {
    resetMagiConfig();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns defaults when no config file exists", () => {
    const config = loadMagiConfig(tmpDir);
    expect(config.hooks.directory).toBe("./hooks");
    expect(config.hooks.disable_builtin).toEqual([]);
    expect(config.hooks.overrides).toEqual({});
    expect(config.tools.directory).toBe("./tools");
    expect(config.tools.disable_builtin).toEqual([]);
    expect(config.tools.overrides).toEqual({});
    expect(config.classifier.custom_dimensions).toEqual({});
  });

  it("caches config on repeated calls", () => {
    const a = loadMagiConfig(tmpDir);
    const b = loadMagiConfig(tmpDir);
    expect(a).toBe(b);
  });

  it("resets cache", () => {
    const a = loadMagiConfig(tmpDir);
    resetMagiConfig();
    const b = loadMagiConfig(tmpDir);
    expect(a).not.toBe(b);
    expect(a).toEqual(b);
  });

  // --- Hooks section ---

  it("parses hooks section", () => {
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      `
hooks:
  disable_builtin:
    - factGroundingVerifier
  directory: ./my-hooks
  overrides:
    my-hook:
      enabled: true
      priority: 50
      blocking: false
`,
    );
    const config = loadMagiConfig(tmpDir);
    expect(config.hooks.disable_builtin).toEqual(["factGroundingVerifier"]);
    expect(config.hooks.directory).toBe("./my-hooks");
    expect(config.hooks.overrides["my-hook"]).toEqual({
      enabled: true,
      priority: 50,
      blocking: false,
    });
  });

  it("parses hooks global_directory", () => {
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      `
hooks:
  global_directory: /custom/hooks
`,
    );
    const config = loadMagiConfig(tmpDir);
    expect(config.hooks.global_directory).toBe("/custom/hooks");
  });

  // --- Tools section ---

  it("parses tools section", () => {
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      `
tools:
  disable_builtin:
    - Bash
  directory: ./my-tools
  packages:
    - "@magi-tools/weather"
  overrides:
    my-tool:
      enabled: true
      permission: write
`,
    );
    const config = loadMagiConfig(tmpDir);
    expect(config.tools.disable_builtin).toEqual(["Bash"]);
    expect(config.tools.directory).toBe("./my-tools");
    expect(config.tools.packages).toEqual(["@magi-tools/weather"]);
    expect(config.tools.overrides["my-tool"]).toEqual({
      enabled: true,
      permission: "write",
    });
  });

  it("parses tools global_directory", () => {
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      `
tools:
  global_directory: /custom/tools
`,
    );
    const config = loadMagiConfig(tmpDir);
    expect(config.tools.global_directory).toBe("/custom/tools");
  });

  // --- Classifier section ---

  it("parses classifier custom_dimensions", () => {
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      `
classifier:
  custom_dimensions:
    safety:
      phase: "final_answer"
      prompt: "Is this safe?"
      output_schema:
        is_safe: "boolean"
`,
    );
    const config = loadMagiConfig(tmpDir);
    const dim = config.classifier.custom_dimensions["safety"];
    expect(dim).toBeDefined();
    expect(dim!.phase).toBe("final_answer");
    expect(dim!.prompt).toBe("Is this safe?");
    expect(dim!.output_schema).toEqual({ is_safe: "boolean" });
  });

  // --- Env var substitution ---

  it("resolves env vars in values", () => {
    process.env.TEST_HOOK_DIR = "/env-hooks";
    process.env.TEST_TOOL_DIR = "/env-tools";
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      `
hooks:
  directory: "\${TEST_HOOK_DIR}"
tools:
  directory: "\${TEST_TOOL_DIR}"
`,
    );
    const config = loadMagiConfig(tmpDir);
    expect(config.hooks.directory).toBe("/env-hooks");
    expect(config.tools.directory).toBe("/env-tools");
    delete process.env.TEST_HOOK_DIR;
    delete process.env.TEST_TOOL_DIR;
  });

  it("handles empty YAML gracefully", () => {
    fs.writeFileSync(path.join(tmpDir, "magi.config.yaml"), "");
    const config = loadMagiConfig(tmpDir);
    expect(config.hooks.directory).toBe("./hooks");
    expect(config.tools.directory).toBe("./tools");
  });

  it("throws on invalid YAML syntax", () => {
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      "{{invalid yaml",
    );
    expect(() => loadMagiConfig(tmpDir)).toThrow("Invalid YAML");
  });
});
