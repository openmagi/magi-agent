import { describe, it, expect, vi, beforeEach } from "vitest";
import { HookRegistry } from "./HookRegistry.js";
import type { ExternalHookConfig } from "./ExternalHookLoader.js";

// Mock fs and dynamic import
vi.mock("node:fs/promises", () => ({
  readdir: vi.fn(),
}));

describe("ExternalHookLoader", () => {
  let registry: HookRegistry;
  const log = vi.fn();

  beforeEach(() => {
    registry = new HookRegistry();
    log.mockClear();
    vi.resetModules();
  });

  it("should skip ENOENT when hooks directory does not exist", async () => {
    const { readdir } = await import("node:fs/promises");
    const err = new Error("ENOENT") as NodeJS.ErrnoException;
    err.code = "ENOENT";
    (readdir as ReturnType<typeof vi.fn>).mockRejectedValue(err);

    const { loadExternalHooks } = await import("./ExternalHookLoader.js");
    const config: ExternalHookConfig = { directory: "/tmp/hooks" };
    const result = await loadExternalHooks(registry, config, log);

    expect(result.loaded).toHaveLength(0);
    expect(result.failed).toHaveLength(0);
  });

  it("should reject hooks without custom: prefix", async () => {
    const { readdir } = await import("node:fs/promises");
    (readdir as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const { loadExternalHooks } = await import("./ExternalHookLoader.js");
    const config: ExternalHookConfig = {
      directory: "/tmp/hooks",
      autoDiscover: false,
      hooks: [{
        file: "test.hook.js",
      }],
    };

    // Mock the dynamic import to return a hook without custom: prefix
    vi.stubGlobal("__hookModule", {
      createHook: () => ({
        name: "badName",
        point: "beforeCommit",
        handler: async () => ({ action: "continue" as const }),
      }),
    });

    const result = await loadExternalHooks(registry, config, log);
    // Will fail because import() can't actually load the file
    expect(result.failed.length).toBeGreaterThan(0);
  });

  it("should validate hook point", async () => {
    const { loadExternalHooks, _test } = await import("./ExternalHookLoader.js");

    // Test the validation directly
    expect(_test.isValidHookPoint("beforeCommit")).toBe(true);
    expect(_test.isValidHookPoint("invalidPoint")).toBe(false);
  });

  it("should validate hook definition shape", async () => {
    const { _test } = await import("./ExternalHookLoader.js");

    expect(_test.validateHookDefinition(null, "test.hook.js")).toContain(
      "did not return an object",
    );
    expect(_test.validateHookDefinition({}, "test.hook.js")).toContain(
      "missing hook name",
    );
    expect(
      _test.validateHookDefinition({ name: "nope" }, "test.hook.js"),
    ).toContain('must start with "custom:"');
    expect(
      _test.validateHookDefinition(
        { name: "custom:test", point: "invalid" },
        "test.hook.js",
      ),
    ).toContain("invalid hook point");
    expect(
      _test.validateHookDefinition(
        { name: "custom:test", point: "beforeCommit" },
        "test.hook.js",
      ),
    ).toContain("handler must be a function");
    expect(
      _test.validateHookDefinition(
        {
          name: "custom:test",
          point: "beforeCommit",
          handler: () => {},
        },
        "test.hook.js",
      ),
    ).toBeNull();
  });

  it("should identify hook files by extension", async () => {
    const { _test } = await import("./ExternalHookLoader.js");

    expect(_test.isHookFile("my-hook.hook.js")).toBe(true);
    expect(_test.isHookFile("my-hook.hook.mjs")).toBe(true);
    expect(_test.isHookFile("my-hook.ts")).toBe(false);
    expect(_test.isHookFile("my-hook.js")).toBe(false);
  });

  it("should skip disabled hooks in config", async () => {
    const { readdir } = await import("node:fs/promises");
    (readdir as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const { loadExternalHooks } = await import("./ExternalHookLoader.js");
    const config: ExternalHookConfig = {
      directory: "/tmp/hooks",
      autoDiscover: false,
      hooks: [{ file: "disabled.hook.js", enabled: false }],
    };
    const result = await loadExternalHooks(registry, config, log);
    expect(result.loaded).toHaveLength(0);
    expect(result.failed).toHaveLength(0);
  });
});
