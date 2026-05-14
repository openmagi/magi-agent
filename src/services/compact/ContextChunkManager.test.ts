import { describe, expect, it } from "vitest";
import {
  ContextChunkManager,
  type ContextChunk,
  splitHistoryByRecentTurns,
} from "./ContextChunkManager.js";
import type { LLMMessage } from "../../transport/LLMClient.js";

function chunk(
  id: string,
  priority: number,
  category: ContextChunk["category"],
  tokenCount: number,
  opts: Partial<Pick<ContextChunk, "content" | "shrinkable" | "minTokens">> = {},
): ContextChunk {
  return {
    id,
    priority,
    category,
    content: opts.content ?? `${id} `.repeat(tokenCount * 2),
    tokenCount,
    shrinkable: opts.shrinkable ?? false,
    ...(opts.minTokens !== undefined ? { minTokens: opts.minTokens } : {}),
  };
}

describe("ContextChunkManager.allocate", () => {
  it("force-includes mandatory system and tools chunks even when they exceed budget", () => {
    const manager = new ContextChunkManager();

    const result = manager.allocate(
      [
        chunk("system", 0, "system", 80),
        chunk("tools", 1, "tools", 50),
        chunk("memory", 2, "memory", 10),
      ],
      100,
    );

    expect(result.includedChunks.map((c) => c.id)).toEqual(["system", "tools"]);
    expect(result.usedTokens).toBe(130);
    expect(result.overBudget).toBe(true);
    expect(result.warnings.join("\n")).toContain("mandatory");
    expect(result.droppedChunks).toEqual([
      expect.objectContaining({ id: "memory", category: "memory" }),
    ]);
  });

  it("includes every chunk in priority order when the budget is sufficient", () => {
    const manager = new ContextChunkManager();

    const result = manager.allocate(
      [
        chunk("recent", 5, "history_recent", 10),
        chunk("system", 0, "system", 10),
        chunk("workspace", 3, "workspace", 10),
        chunk("tools", 1, "tools", 10),
      ],
      50,
    );

    expect(result.includedChunks.map((c) => c.id)).toEqual([
      "system",
      "tools",
      "workspace",
      "recent",
    ]);
    expect(result.droppedChunks).toEqual([]);
    expect(result.overBudget).toBe(false);
  });

  it("shrinks shrinkable chunks to their minimum before dropping lower priority chunks", () => {
    const manager = new ContextChunkManager();

    const result = manager.allocate(
      [
        chunk("system", 0, "system", 10),
        chunk("tools", 1, "tools", 10),
        chunk("memory", 2, "memory", 40, { shrinkable: true, minTokens: 20 }),
        chunk("reminder", 9, "reminder", 40),
      ],
      55,
    );

    const memory = result.includedChunks.find((c) => c.id === "memory");
    expect(memory?.tokenCount).toBeLessThanOrEqual(35);
    expect(memory?.tokenCount).toBeGreaterThanOrEqual(20);
    expect(result.includedChunks.map((c) => c.id)).toEqual([
      "system",
      "tools",
      "memory",
    ]);
    expect(result.droppedChunks).toEqual([
      expect.objectContaining({ id: "reminder", reason: "budget_exceeded" }),
    ]);
    expect(result.usedTokens).toBeLessThanOrEqual(55);
  });

  it("records dropped chunk metadata for debugging", () => {
    const manager = new ContextChunkManager();

    const result = manager.allocate(
      [
        chunk("system", 0, "system", 10),
        chunk("tools", 1, "tools", 10),
        chunk("old", 6, "history_old", 100),
      ],
      30,
    );

    expect(result.droppedChunks).toEqual([
      {
        id: "old",
        priority: 6,
        category: "history_old",
        tokenCount: 100,
        reason: "budget_exceeded",
      },
    ]);
  });
});

describe("splitHistoryByRecentTurns", () => {
  it("splits the last three user turns into history_recent and older turns into history_old", () => {
    const messages: LLMMessage[] = [
      { role: "user", content: "u1" },
      { role: "assistant", content: "a1" },
      { role: "user", content: "u2" },
      { role: "assistant", content: "a2" },
      { role: "user", content: "u3" },
      { role: "assistant", content: "a3" },
      { role: "user", content: "u4" },
      { role: "assistant", content: "a4" },
      { role: "user", content: "u5" },
      { role: "assistant", content: "a5" },
    ];

    const split = splitHistoryByRecentTurns(messages, 3);

    expect(split.old.map((m) => m.content)).toEqual(["u1", "a1", "u2", "a2"]);
    expect(split.recent.map((m) => m.content)).toEqual([
      "u3",
      "a3",
      "u4",
      "a4",
      "u5",
      "a5",
    ]);
  });
});

describe("ContextChunkManager system chunk extraction", () => {
  it("maps memoryInjector and workspaceAwareness output into memory and workspace chunks", () => {
    const manager = new ContextChunkManager();
    const system = [
      "<memory-continuity-policy hidden=\"true\">policy</memory-continuity-policy>",
      "<memory-root source=\"hipocampus-root\" tier=\"L2\">root</memory-root>",
      "CORE SYSTEM",
      "<workspace_snapshot refreshedAt=\"2026-05-12T00:00:00.000Z\">workspace</workspace_snapshot>",
    ].join("\n\n");

    const chunks = manager.systemChunks(system);

    expect(chunks.map((c) => [c.category, c.id])).toEqual([
      ["memory", "memory_context"],
      ["system", "system_prompt"],
      ["workspace", "workspace_snapshot"],
    ]);
    expect(chunks.find((c) => c.category === "memory")?.content).toContain(
      "<memory-root",
    );
    expect(chunks.find((c) => c.category === "workspace")?.content).toContain(
      "<workspace_snapshot",
    );
  });
});
