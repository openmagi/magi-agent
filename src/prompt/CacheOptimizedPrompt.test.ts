import { describe, it, expect } from "vitest";
import {
  splitForCacheOptimization,
  toCombinedString,
  toSystemBlocks,
} from "./CacheOptimizedPrompt.js";

function assembleTestPrompt(opts?: {
  withIdentity?: boolean;
  withWorkspace?: boolean;
  withMemory?: boolean;
}): string {
  const parts: string[] = [];

  if (opts?.withIdentity) {
    parts.push(
      '<agent-identity revision="abc123">',
      "# SOUL",
      "You are a helpful assistant.",
      "# IDENTITY",
      "Agent Kevin",
      "</agent-identity>",
    );
  }

  parts.push(
    "[Session: sess-1]",
    "[Turn: turn-42]",
    "[Time: 2026-05-12T10:00:00.000Z]",
    "[Channel: web]",
  );

  parts.push(
    "",
    "<output-rules>",
    "CRITICAL: The user can only see your TEXT output.",
    "</output-rules>",
  );

  if (opts?.withMemory) {
    parts.push(
      "",
      '<memory-context source="qmd" tier="L0">',
      "some recalled memory content",
      "</memory-context>",
    );
  }

  if (opts?.withWorkspace) {
    parts.push(
      "",
      '<workspace_snapshot refreshedAt="2026-05-12T10:00:00.000Z">',
      "src/",
      "  index.ts",
      "  utils.ts",
      "</workspace_snapshot>",
    );
  }

  return parts.join("\n");
}

describe("CacheOptimizedPrompt", () => {
  describe("splitForCacheOptimization", () => {
    it("puts agent-identity fence into static block", () => {
      const system = assembleTestPrompt({ withIdentity: true });
      const result = splitForCacheOptimization(system);
      expect(result.staticSystem).toContain("<agent-identity");
      expect(result.staticSystem).toContain("You are a helpful assistant.");
      expect(result.staticSystem).toContain("</agent-identity>");
    });

    it("puts output-rules into static block", () => {
      const system = assembleTestPrompt({ withIdentity: true });
      const result = splitForCacheOptimization(system);
      expect(result.staticSystem).toContain("<output-rules>");
      expect(result.staticSystem).toContain("CRITICAL:");
    });

    it("puts session/turn/time headers into dynamic block", () => {
      const system = assembleTestPrompt({ withIdentity: true });
      const result = splitForCacheOptimization(system);
      expect(result.dynamicSystem).toContain("[Session: sess-1]");
      expect(result.dynamicSystem).toContain("[Turn: turn-42]");
      expect(result.dynamicSystem).toContain("[Time: 2026-05-12T10:00:00.000Z]");
      expect(result.dynamicSystem).toContain("[Channel: web]");
    });

    it("does NOT put turn/time headers into static block", () => {
      const system = assembleTestPrompt({ withIdentity: true });
      const result = splitForCacheOptimization(system);
      expect(result.staticSystem).not.toContain("[Turn:");
      expect(result.staticSystem).not.toContain("[Time:");
      expect(result.staticSystem).not.toContain("[Session:");
    });

    it("puts workspace_snapshot into semi-static block", () => {
      const system = assembleTestPrompt({
        withIdentity: true,
        withWorkspace: true,
      });
      const result = splitForCacheOptimization(system);
      expect(result.semiStaticSystem).toContain("<workspace_snapshot");
      expect(result.semiStaticSystem).toContain("index.ts");
      expect(result.semiStaticSystem).toContain("</workspace_snapshot>");
    });

    it("puts memory-context into dynamic block", () => {
      const system = assembleTestPrompt({
        withIdentity: true,
        withMemory: true,
      });
      const result = splitForCacheOptimization(system);
      expect(result.dynamicSystem).toContain("<memory-context");
      expect(result.dynamicSystem).toContain("some recalled memory content");
    });

    it("handles prompt with no fenced blocks (header-only)", () => {
      const system = [
        "[Session: sess-1]",
        "[Turn: turn-1]",
        "[Time: 2026-05-12T10:00:00.000Z]",
        "[Channel: web]",
      ].join("\n");
      const result = splitForCacheOptimization(system);
      expect(result.staticSystem).toBe("");
      expect(result.semiStaticSystem).toBe("");
      expect(result.dynamicSystem).toContain("[Session: sess-1]");
    });

    it("consecutive turns: static/semiStatic stable, dynamic changes", () => {
      const system1 = assembleTestPrompt({
        withIdentity: true,
        withWorkspace: true,
      });
      const system2 = system1
        .replace("[Turn: turn-42]", "[Turn: turn-43]")
        .replace(
          "[Time: 2026-05-12T10:00:00.000Z]",
          "[Time: 2026-05-12T10:01:00.000Z]",
        );

      const r1 = splitForCacheOptimization(system1);
      const r2 = splitForCacheOptimization(system2);

      expect(r1.staticSystem).toBe(r2.staticSystem);
      expect(r1.semiStaticSystem).toBe(r2.semiStaticSystem);
      expect(r1.dynamicSystem).not.toBe(r2.dynamicSystem);
    });

    it("classifies agent_self_model as static", () => {
      const system = [
        "<agent_self_model>",
        "You are a Magi agent.",
        "</agent_self_model>",
        "[Turn: turn-1]",
      ].join("\n");
      const result = splitForCacheOptimization(system);
      expect(result.staticSystem).toContain("<agent_self_model>");
      expect(result.dynamicSystem).toContain("[Turn: turn-1]");
    });

    it("classifies runtime-evidence-policy as static", () => {
      const system = [
        "<runtime-evidence-policy>",
        "evidence rules",
        "</runtime-evidence-policy>",
      ].join("\n");
      const result = splitForCacheOptimization(system);
      expect(result.staticSystem).toContain("<runtime-evidence-policy>");
    });

    it("classifies repo_map as semi-static", () => {
      const system = [
        "[Turn: turn-1]",
        "<repo_map>",
        "src/index.ts",
        "  export function main",
        "</repo_map>",
      ].join("\n");
      const result = splitForCacheOptimization(system);
      expect(result.semiStaticSystem).toContain("<repo_map>");
      expect(result.semiStaticSystem).toContain("export function main");
    });
  });

  describe("toCombinedString", () => {
    it("joins all three blocks with double newlines", () => {
      const prompt = {
        staticSystem: "STATIC CONTENT",
        semiStaticSystem: "SEMI-STATIC CONTENT",
        dynamicSystem: "DYNAMIC CONTENT",
      };
      const combined = toCombinedString(prompt);
      expect(combined).toBe(
        "STATIC CONTENT\n\nSEMI-STATIC CONTENT\n\nDYNAMIC CONTENT",
      );
    });

    it("skips empty blocks", () => {
      const prompt = {
        staticSystem: "STATIC",
        semiStaticSystem: "",
        dynamicSystem: "DYNAMIC",
      };
      expect(toCombinedString(prompt)).toBe("STATIC\n\nDYNAMIC");
    });

    it("roundtrip: combined string preserves all content", () => {
      const system = assembleTestPrompt({
        withIdentity: true,
        withWorkspace: true,
        withMemory: true,
      });
      const split = splitForCacheOptimization(system);
      const combined = toCombinedString(split);
      for (const keyword of [
        "<agent-identity",
        "[Turn: turn-42]",
        "<workspace_snapshot",
        "<memory-context",
        "<output-rules>",
      ]) {
        expect(combined).toContain(keyword);
      }
    });
  });

  describe("toSystemBlocks", () => {
    it("produces blocks with cache_control on static and semiStatic", () => {
      const prompt = {
        staticSystem: "STATIC",
        semiStaticSystem: "SEMI",
        dynamicSystem: "DYN",
      };
      const blocks = toSystemBlocks(prompt);
      expect(blocks).toHaveLength(3);
      expect(blocks[0]!.text).toBe("STATIC");
      expect(blocks[0]!.cache_control).toEqual({ type: "ephemeral" });
      expect(blocks[1]!.text).toBe("SEMI");
      expect(blocks[1]!.cache_control).toEqual({ type: "ephemeral" });
      expect(blocks[2]!.text).toBe("DYN");
      expect(blocks[2]!.cache_control).toBeUndefined();
    });

    it("omits empty blocks", () => {
      const prompt = {
        staticSystem: "STATIC",
        semiStaticSystem: "",
        dynamicSystem: "DYN",
      };
      const blocks = toSystemBlocks(prompt);
      expect(blocks).toHaveLength(2);
      expect(blocks[0]!.text).toBe("STATIC");
      expect(blocks[1]!.text).toBe("DYN");
    });

    it("all blocks have type text", () => {
      const prompt = {
        staticSystem: "A",
        semiStaticSystem: "B",
        dynamicSystem: "C",
      };
      const blocks = toSystemBlocks(prompt);
      for (const block of blocks) {
        expect(block.type).toBe("text");
      }
    });
  });
});
