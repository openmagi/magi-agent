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
    resetMagiConfig();
    tmpDir = makeTmpDir();
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns defaults when config file does not exist", () => {
    const cfg = loadMagiConfig(tmpDir);
    expect(cfg.hooks.disable_builtin).toEqual([]);
    expect(cfg.hooks.directory).toBe("./hooks");
    expect(cfg.hooks.global_directory).toBe("~/.magi/hooks");
    expect(cfg.hooks.overrides).toEqual({});
    expect(cfg.classifier.custom_dimensions).toEqual({});
  });

  it("parses hooks section", () => {
    const yaml = `
hooks:
  disable_builtin:
    - "builtin:fact-grounding-verifier"
    - "builtin:sealed-files"
  directory: "./my-hooks"
  global_directory: "~/.my-agent/hooks"
  overrides:
    my-hook:
      enabled: true
      priority: 50
      blocking: false
      timeoutMs: 10000
`;
    fs.writeFileSync(path.join(tmpDir, "magi.config.yaml"), yaml);
    const cfg = loadMagiConfig(tmpDir);

    expect(cfg.hooks.disable_builtin).toEqual([
      "builtin:fact-grounding-verifier",
      "builtin:sealed-files",
    ]);
    expect(cfg.hooks.directory).toBe("./my-hooks");
    expect(cfg.hooks.global_directory).toBe("~/.my-agent/hooks");
    expect(cfg.hooks.overrides["my-hook"]).toEqual({
      enabled: true,
      priority: 50,
      blocking: false,
      timeoutMs: 10000,
    });
  });

  it("parses classifier custom dimensions", () => {
    const yaml = `
classifier:
  custom_dimensions:
    safety:
      phase: "final_answer"
      prompt: "Is this response safe?"
      output_schema:
        is_safe: "boolean"
        confidence: "number"
`;
    fs.writeFileSync(path.join(tmpDir, "magi.config.yaml"), yaml);
    const cfg = loadMagiConfig(tmpDir);

    const dim = cfg.classifier.custom_dimensions["safety"];
    expect(dim).toBeDefined();
    expect(dim?.phase).toBe("final_answer");
    expect(dim?.prompt).toBe("Is this response safe?");
    expect(dim?.output_schema).toEqual({
      is_safe: "boolean",
      confidence: "number",
    });
  });

  it("performs env var substitution", () => {
    process.env.TEST_MAGI_HOOK_DIR = "/custom/hooks";
    const yaml = `
hooks:
  directory: "\${TEST_MAGI_HOOK_DIR}"
`;
    fs.writeFileSync(path.join(tmpDir, "magi.config.yaml"), yaml);
    const cfg = loadMagiConfig(tmpDir);

    expect(cfg.hooks.directory).toBe("/custom/hooks");
    delete process.env.TEST_MAGI_HOOK_DIR;
  });

  it("replaces missing env vars with empty string", () => {
    delete process.env.NONEXISTENT_VAR_FOR_MAGI_TEST;
    const yaml = `
hooks:
  directory: "\${NONEXISTENT_VAR_FOR_MAGI_TEST}/hooks"
`;
    fs.writeFileSync(path.join(tmpDir, "magi.config.yaml"), yaml);
    const cfg = loadMagiConfig(tmpDir);

    expect(cfg.hooks.directory).toBe("/hooks");
  });

  it("caches result on second call with same dir", () => {
    const yaml = `
hooks:
  directory: "./cached-hooks"
`;
    fs.writeFileSync(path.join(tmpDir, "magi.config.yaml"), yaml);
    const cfg1 = loadMagiConfig(tmpDir);
    const cfg2 = loadMagiConfig(tmpDir);

    expect(cfg1).toBe(cfg2); // same reference
  });

  it("rejects invalid classifier dimension phase", () => {
    const yaml = `
classifier:
  custom_dimensions:
    bad:
      phase: "invalid"
      prompt: "test"
      output_schema: {}
`;
    fs.writeFileSync(path.join(tmpDir, "magi.config.yaml"), yaml);
    const cfg = loadMagiConfig(tmpDir);

    // Invalid phase dimensions are skipped
    expect(cfg.classifier.custom_dimensions["bad"]).toBeUndefined();
  });

  it("handles empty YAML file", () => {
    fs.writeFileSync(path.join(tmpDir, "magi.config.yaml"), "");
    const cfg = loadMagiConfig(tmpDir);

    // Should fall back to defaults
    expect(cfg.hooks.disable_builtin).toEqual([]);
    expect(cfg.classifier.custom_dimensions).toEqual({});
  });
});
