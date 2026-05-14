/**
 * MagiConfig.test — covers loading, parsing, defaults, env-var
 * substitution, and the tools section.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

import {
  loadMagiConfig,
  resetMagiConfig,
} from "./MagiConfig.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "magiconfig-test-"));
  resetMagiConfig();
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
  resetMagiConfig();
});

describe("MagiConfig", () => {
  it("returns defaults when no config file exists", () => {
    const config = loadMagiConfig(tmpDir);

    expect(config.tools.disable_builtin).toEqual([]);
    expect(config.tools.directory).toBe("./tools");
    expect(config.tools.global_directory).toBe("~/.magi/tools");
    expect(config.tools.packages).toEqual([]);
    expect(config.tools.overrides).toEqual({});
    expect(config.classifier.custom_dimensions).toEqual({});
  });

  it("parses tools section", () => {
    const configContent = `
tools:
  disable_builtin:
    - Bash
    - Edit
  directory: ./custom-tools
  global_directory: ~/.magi/custom-global
  packages:
    - "@magi-tools/sql"
  overrides:
    MyTool:
      enabled: false
      permission: net
      timeoutMs: 5000
`;
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      configContent,
      "utf-8",
    );

    const config = loadMagiConfig(tmpDir);

    expect(config.tools.disable_builtin).toEqual(["Bash", "Edit"]);
    expect(config.tools.directory).toBe("./custom-tools");
    expect(config.tools.global_directory).toBe("~/.magi/custom-global");
    expect(config.tools.packages).toEqual(["@magi-tools/sql"]);
    expect(config.tools.overrides.MyTool).toEqual({
      enabled: false,
      permission: "net",
      timeoutMs: 5000,
    });
  });

  it("parses classifier section", () => {
    const configContent = `
classifier:
  custom_dimensions:
    safety:
      phase: final_answer
      prompt: "Check for safety issues"
      output_schema:
        safe: "boolean"
`;
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      configContent,
      "utf-8",
    );

    const config = loadMagiConfig(tmpDir);

    expect(config.classifier.custom_dimensions.safety).toEqual({
      phase: "final_answer",
      prompt: "Check for safety issues",
      output_schema: { safe: "boolean" },
    });
  });

  it("resolves environment variables", () => {
    process.env.TEST_TOOLS_DIR = "/custom/tools/path";

    const configContent = `
tools:
  directory: "\${TEST_TOOLS_DIR}"
`;
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      configContent,
      "utf-8",
    );

    const config = loadMagiConfig(tmpDir);
    expect(config.tools.directory).toBe("/custom/tools/path");

    delete process.env.TEST_TOOLS_DIR;
  });

  it("caches config on second load", () => {
    const config1 = loadMagiConfig(tmpDir);
    const config2 = loadMagiConfig(tmpDir);
    expect(config1).toBe(config2); // same reference
  });

  it("reloads after resetMagiConfig", () => {
    const config1 = loadMagiConfig(tmpDir);
    resetMagiConfig();
    const config2 = loadMagiConfig(tmpDir);
    expect(config1).not.toBe(config2); // different reference
    expect(config1).toEqual(config2); // same content
  });

  it("handles empty config file gracefully", () => {
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      "",
      "utf-8",
    );

    const config = loadMagiConfig(tmpDir);
    expect(config.tools.disable_builtin).toEqual([]);
  });

  it("throws on invalid YAML", () => {
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      ":\n  :\n    [[[invalid",
      "utf-8",
    );

    expect(() => loadMagiConfig(tmpDir)).toThrow("Invalid YAML");
  });

  it("ignores non-string disable_builtin entries", () => {
    const configContent = `
tools:
  disable_builtin:
    - ValidTool
    - 123
    - true
`;
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      configContent,
      "utf-8",
    );

    const config = loadMagiConfig(tmpDir);
    expect(config.tools.disable_builtin).toEqual(["ValidTool"]);
  });

  it("ignores overrides with non-object values", () => {
    const configContent = `
tools:
  overrides:
    GoodTool:
      enabled: true
    BadTool: "not an object"
`;
    fs.writeFileSync(
      path.join(tmpDir, "magi.config.yaml"),
      configContent,
      "utf-8",
    );

    const config = loadMagiConfig(tmpDir);
    expect(config.tools.overrides.GoodTool).toEqual({ enabled: true });
    expect(config.tools.overrides.BadTool).toBeUndefined();
  });
});
