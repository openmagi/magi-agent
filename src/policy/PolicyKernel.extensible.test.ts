import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ClassifierDimensionDef } from "../hooks/builtin/classifierExtensions.js";

// Mock fs for config loading tests
vi.mock("node:fs/promises", () => ({
  default: {
    readFile: vi.fn(),
  },
  readFile: vi.fn(),
}));

describe("PolicyKernel extensible hooks integration", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  describe("loadAgentConfigExtensions", () => {
    it("should return empty defaults when config file missing", async () => {
      const fs = await import("node:fs/promises");
      const err = new Error("ENOENT") as NodeJS.ErrnoException;
      err.code = "ENOENT";
      (fs.default.readFile as ReturnType<typeof vi.fn>).mockRejectedValue(err);

      const { loadAgentConfigExtensions } = await import("./PolicyKernel.js");
      const result = await loadAgentConfigExtensions({ root: "/tmp/test" } as never);

      expect(result.extensions.disableBuiltinHooks).toEqual([]);
      expect(result.extensions.customHooks).toBeUndefined();
      expect(result.warnings).toHaveLength(0);
    });

    it("should parse disable_builtin_hooks list", async () => {
      const fs = await import("node:fs/promises");
      (fs.default.readFile as ReturnType<typeof vi.fn>).mockResolvedValue(
        `disable_builtin_hooks:\n  - factGroundingVerifier\n  - responseLanguageGate\n`,
      );

      const { loadAgentConfigExtensions } = await import("./PolicyKernel.js");
      const result = await loadAgentConfigExtensions({ root: "/tmp/test" } as never);

      expect(result.extensions.disableBuiltinHooks).toEqual([
        "factGroundingVerifier",
        "responseLanguageGate",
      ]);
    });

    it("should parse custom_hooks config", async () => {
      const fs = await import("node:fs/promises");
      (fs.default.readFile as ReturnType<typeof vi.fn>).mockResolvedValue(
        `custom_hooks:\n  directory: ./my-hooks\n  auto_discover: false\n  hooks:\n    - file: my-hook.hook.js\n      priority: 10\n`,
      );

      const { loadAgentConfigExtensions } = await import("./PolicyKernel.js");
      const result = await loadAgentConfigExtensions({ root: "/tmp/test" } as never);

      expect(result.extensions.customHooks).toBeDefined();
      expect(result.extensions.customHooks!.directory).toBe("./my-hooks");
      expect(result.extensions.customHooks!.autoDiscover).toBe(false);
      expect(result.extensions.customHooks!.hooks).toHaveLength(1);
      expect(result.extensions.customHooks!.hooks![0]!.file).toBe("my-hook.hook.js");
      expect(result.extensions.customHooks!.hooks![0]!.priority).toBe(10);
    });
  });

  describe("loadClassifierDimensions", () => {
    it("should return empty when config file missing", async () => {
      const fs = await import("node:fs/promises");
      const err = new Error("ENOENT") as NodeJS.ErrnoException;
      err.code = "ENOENT";
      (fs.default.readFile as ReturnType<typeof vi.fn>).mockRejectedValue(err);

      const { loadClassifierDimensions } = await import("./PolicyKernel.js");
      const result = await loadClassifierDimensions({ root: "/tmp/test" } as never);

      expect(result.dimensions).toHaveLength(0);
    });

    it("should parse request-phase classifier dimensions", async () => {
      const fs = await import("node:fs/promises");
      (fs.default.readFile as ReturnType<typeof vi.fn>).mockResolvedValue(
        `classifier_dimensions:\n  request:\n    - name: urgency\n      schema:\n        level: string\n        score: number\n      instructions: Rate request urgency 1-5.\n`,
      );

      const { loadClassifierDimensions } = await import("./PolicyKernel.js");
      const result = await loadClassifierDimensions({ root: "/tmp/test" } as never);

      expect(result.dimensions).toHaveLength(1);
      const dim = result.dimensions[0] as ClassifierDimensionDef;
      expect(dim.name).toBe("urgency");
      expect(dim.phase).toBe("request");
      expect(dim.schema).toEqual({ level: "string", score: "number" });
      expect(dim.instructions).toBe("Rate request urgency 1-5.");
    });

    it("should parse final_answer-phase dimensions", async () => {
      const fs = await import("node:fs/promises");
      (fs.default.readFile as ReturnType<typeof vi.fn>).mockResolvedValue(
        `classifier_dimensions:\n  final_answer:\n    - name: confidence\n      schema:\n        level: string\n      instructions: Rate answer confidence.\n`,
      );

      const { loadClassifierDimensions } = await import("./PolicyKernel.js");
      const result = await loadClassifierDimensions({ root: "/tmp/test" } as never);

      expect(result.dimensions).toHaveLength(1);
      expect(result.dimensions[0]!.phase).toBe("finalAnswer");
    });

    it("should warn on missing schema", async () => {
      const fs = await import("node:fs/promises");
      (fs.default.readFile as ReturnType<typeof vi.fn>).mockResolvedValue(
        `classifier_dimensions:\n  request:\n    - name: bad\n      instructions: some\n`,
      );

      const { loadClassifierDimensions } = await import("./PolicyKernel.js");
      const result = await loadClassifierDimensions({ root: "/tmp/test" } as never);

      expect(result.dimensions).toHaveLength(0);
      expect(result.warnings.some((w) => w.includes("missing schema"))).toBe(true);
    });
  });
});
