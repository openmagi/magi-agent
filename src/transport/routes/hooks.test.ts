import { describe, it, expect, vi, beforeEach } from "vitest";
import { HookRegistry } from "../../hooks/HookRegistry.js";
import type { RegisteredHook, HookPoint } from "../../hooks/types.js";

/**
 * Unit tests for hook CRUD routes. Since routes use the http
 * req/res pattern, we test the HookRegistry methods directly
 * that the routes exercise.
 */
describe("hooks routes — HookRegistry CRUD", () => {
  let registry: HookRegistry;

  function makeHook(overrides?: Partial<RegisteredHook>): RegisteredHook {
    return {
      name: overrides?.name ?? "test:hook",
      point: (overrides?.point ?? "beforeCommit") as HookPoint,
      handler: vi.fn().mockResolvedValue({ action: "continue" }),
      priority: overrides?.priority ?? 50,
      blocking: overrides?.blocking ?? true,
      source: overrides?.source ?? "custom",
      enabled: overrides?.enabled ?? true,
      ...(overrides?.failOpen !== undefined ? { failOpen: overrides.failOpen } : {}),
    };
  }

  beforeEach(() => {
    registry = new HookRegistry();
  });

  describe("listDetailed", () => {
    it("should list all hooks with stats", () => {
      registry.register(makeHook({ name: "builtin:a", source: "builtin" }));
      registry.register(makeHook({ name: "custom:b", source: "custom" }));

      const list = registry.listDetailed();
      expect(list).toHaveLength(2);
      expect(list[0]!.name).toBe("builtin:a");
      expect(list[0]!.source).toBe("builtin");
      expect(list[1]!.name).toBe("custom:b");
      expect(list[1]!.source).toBe("custom");
    });

    it("should filter by hook point", () => {
      registry.register(makeHook({ name: "a", point: "beforeCommit" }));
      registry.register(makeHook({ name: "b", point: "beforeToolUse" }));

      const list = registry.listDetailed("beforeCommit");
      expect(list).toHaveLength(1);
      expect(list[0]!.name).toBe("a");
    });

    it("should include stats fields", () => {
      registry.register(makeHook({ name: "a" }));
      const list = registry.listDetailed();
      expect(list[0]!.stats).toEqual({
        totalRuns: 0,
        timeouts: 0,
        errors: 0,
        blocks: 0,
        avgDurationMs: 0,
        lastRunAt: 0,
      });
    });
  });

  describe("enable/disable", () => {
    it("should disable a hook", () => {
      registry.register(makeHook({ name: "my-hook", enabled: true }));
      registry.disable("my-hook");

      const list = registry.listDetailed();
      expect(list[0]!.enabled).toBe(false);
    });

    it("should enable a disabled hook", () => {
      registry.register(makeHook({ name: "my-hook", enabled: false }));
      registry.enable("my-hook");

      const list = registry.listDetailed();
      expect(list[0]!.enabled).toBe(true);
    });
  });

  describe("unregister", () => {
    it("should remove custom hooks", () => {
      registry.register(makeHook({ name: "custom:removeme", source: "custom" }));
      const removed = registry.unregister("custom:removeme");
      expect(removed).toBe(true);
      expect(registry.list()).toHaveLength(0);
    });

    it("should refuse to remove builtin hooks", () => {
      registry.register(makeHook({ name: "builtin:keep", source: "builtin" }));
      const removed = registry.unregister("builtin:keep");
      expect(removed).toBe(false);
      expect(registry.list()).toHaveLength(1);
    });

    it("should return false for non-existent hook", () => {
      const removed = registry.unregister("nonexistent");
      expect(removed).toBe(false);
    });
  });

  describe("getStats", () => {
    it("should return empty stats for unknown hook", () => {
      const stats = registry.getStats("unknown");
      expect(stats.totalRuns).toBe(0);
      expect(stats.timeouts).toBe(0);
      expect(stats.errors).toBe(0);
      expect(stats.blocks).toBe(0);
    });
  });
});
