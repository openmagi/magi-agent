import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import { afterEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import { makeKnowledgeSearchTool, type KnowledgeSearchRunner } from "./KnowledgeSearch.js";

const roots: string[] = [];

async function makeRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "knowledge-search-"));
  roots.push(root);
  return root;
}

function ctx(root: string): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "s-1",
    turnId: "t-1",
    workspaceRoot: root,
    askUser: async () => ({ selectedId: "ok" }),
    emitProgress: () => {},
    abortSignal: AbortSignal.timeout(5_000),
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("KnowledgeSearch", () => {
  it("uses the workspace knowledge directory without an external kb-search.sh", async () => {
    const root = await makeRoot();
    await fs.mkdir(path.join(root, "knowledge", "reports"), { recursive: true });
    await fs.writeFile(
      path.join(root, "knowledge", "reports", "runtime-proof.md"),
      [
        "# Runtime Proof",
        "",
        "Magi records acceptance criteria, verification evidence, and delivery state.",
      ].join("\n"),
      "utf8",
    );
    const tool = makeKnowledgeSearchTool({ name: "KnowledgeSearch" });

    const result = await tool.execute(
      { mode: "search", collection: "reports", query: "verification delivery", limit: 5 },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    const payload = JSON.parse(result.output ?? "{}") as {
      results: Array<{ path: string; object_key_converted: string; snippet: string }>;
    };
    expect(payload.results[0]).toMatchObject({
      path: "knowledge/reports/runtime-proof.md",
      object_key_converted: "knowledge/reports/runtime-proof.md",
    });
    expect(payload.results[0]?.snippet).toContain("verification evidence");
  });

  it("lists and reads local workspace knowledge documents", async () => {
    const root = await makeRoot();
    await fs.mkdir(path.join(root, "knowledge", "guides"), { recursive: true });
    await fs.writeFile(
      path.join(root, "knowledge", "guides", "local-models.md"),
      "# Local Models\nUse Ollama or LM Studio.",
      "utf8",
    );
    const tool = makeKnowledgeSearchTool({ name: "knowledge-search" });

    const documents = await tool.execute(
      { mode: "documents", collection: "guides" },
      ctx(root),
    );
    expect(documents.status).toBe("ok");
    expect(JSON.parse(documents.output ?? "{}")).toMatchObject({
      documents: [
        {
          filename: "local-models.md",
          collection: "guides",
          object_key_converted: "knowledge/guides/local-models.md",
        },
      ],
    });

    const content = await tool.execute(
      { mode: "get", objectKey: "knowledge/guides/local-models.md" },
      ctx(root),
    );
    expect(content.status).toBe("ok");
    expect(content.output).toContain("Use Ollama or LM Studio.");
  });

  it("runs collection-scoped search through kb-search.sh", async () => {
    const root = await makeRoot();
    const calls: string[][] = [];
    const runner: KnowledgeSearchRunner = async (args) => {
      calls.push(args);
      return {
        exitCode: 0,
        signal: null,
        stdout: "{\"results\":[{\"title\":\"르챔버 20년도\"}]}",
        stderr: "",
        truncated: false,
      };
    };
    const tool = makeKnowledgeSearchTool({ name: "knowledge-search", runner });

    const result = await tool.execute(
      { collection: "Downloads", query: "르챔버 매출", limit: 20 },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toBe("{\"results\":[{\"title\":\"르챔버 20년도\"}]}");
    expect(calls).toEqual([["Downloads", "르챔버 매출", "20"]]);
  });

  it("keeps unscoped search query and limit in the correct kb-search.sh slots", async () => {
    const root = await makeRoot();
    const calls: string[][] = [];
    const runner: KnowledgeSearchRunner = async (args) => {
      calls.push(args);
      return {
        exitCode: 0,
        signal: null,
        stdout: "{\"query\":\"hipocampus memory\",\"results\":[]}",
        stderr: "",
        truncated: false,
      };
    };
    const tool = makeKnowledgeSearchTool({ name: "KnowledgeSearch", runner });

    const result = await tool.execute(
      { mode: "search", query: "hipocampus memory", limit: 3 },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(calls[0]).toEqual(["", "hipocampus memory", "3"]);
  });

  it("adds document-name matches when full-text KB search returns no rows", async () => {
    const root = await makeRoot();
    const calls: string[][] = [];
    const runner: KnowledgeSearchRunner = async (args) => {
      calls.push(args);
      if (args[0] === "Downloads") {
        return {
          exitCode: 0,
          signal: null,
          stdout: "{\"query\":\"르챔버 매출\",\"results\":[]}",
          stderr: "",
          truncated: false,
        };
      }
      return {
        exitCode: 0,
        signal: null,
        stdout: JSON.stringify({
          documents: [{
            filename: "르챔버 20년도.xlsx",
            canonical_title: "르챔버 20년도",
            status: "ready",
            object_key_converted: "bot/Downloads/converted/르챔버_20년도.md",
          }],
        }),
        stderr: "",
        truncated: false,
      };
    };
    const tool = makeKnowledgeSearchTool({ name: "knowledge-search", runner });

    const result = await tool.execute(
      { collection: "Downloads", query: "르챔버 매출", limit: 10 },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(JSON.parse(result.output ?? "{}")).toMatchObject({
      results: [],
      document_matches: [{
        filename: "르챔버 20년도.xlsx",
        object_key_converted: "bot/Downloads/converted/르챔버_20년도.md",
      }],
    });
    expect(calls).toEqual([
      ["Downloads", "르챔버 매출", "10"],
      ["--documents", "Downloads"],
    ]);
  });

  it("runs manifest and document inspection modes", async () => {
    const root = await makeRoot();
    const calls: string[][] = [];
    const runner: KnowledgeSearchRunner = async (args) => {
      calls.push(args);
      return {
        exitCode: 0,
        signal: null,
        stdout: "{\"ok\":true}",
        stderr: "",
        truncated: false,
      };
    };
    const tool = makeKnowledgeSearchTool({ name: "KnowledgeSearch", runner });

    await tool.execute({ mode: "documents", collection: "Downloads" }, ctx(root));
    await tool.execute({ mode: "manifest", collection: "Downloads" }, ctx(root));
    await tool.execute({ mode: "get", objectKey: "bot/Downloads/converted/report.md" }, ctx(root));

    expect(calls).toEqual([
      ["--documents", "Downloads"],
      ["--manifest", "Downloads"],
      ["--get", "bot/Downloads/converted/report.md"],
    ]);
  });

  it("repairs Unicode-normalized object keys before fetching converted content", async () => {
    const root = await makeRoot();
    const decomposedKey = "bot/Downloads/converted/르챔버_14년도.md";
    const composedKey = decomposedKey.normalize("NFC");
    const calls: string[][] = [];
    const runner: KnowledgeSearchRunner = async (args) => {
      calls.push(args);
      if (args[0] === "--get" && args[1] === composedKey) {
        return {
          exitCode: 1,
          signal: null,
          stdout: "{\"message\":\"resource not found\"}",
          stderr: "",
          truncated: false,
        };
      }
      if (args[0] === "--documents") {
        return {
          exitCode: 0,
          signal: null,
          stdout: JSON.stringify({
            documents: [{
              filename: "르챔버 14년도.xlsx",
              object_key_converted: decomposedKey,
            }],
          }),
          stderr: "",
          truncated: false,
        };
      }
      return {
        exitCode: 0,
        signal: null,
        stdout: "converted markdown",
        stderr: "",
        truncated: false,
      };
    };
    const tool = makeKnowledgeSearchTool({ name: "KnowledgeSearch", runner });

    const result = await tool.execute(
      { mode: "get", collection: "Downloads", objectKey: composedKey },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toBe("converted markdown");
    expect(calls).toEqual([
      ["--get", composedKey],
      ["--documents", "Downloads"],
      ["--get", decomposedKey],
    ]);
  });

  it("caps large converted document fetches before they can overflow the next LLM prompt", async () => {
    const root = await makeRoot();
    const runner: KnowledgeSearchRunner = async () => ({
      exitCode: 0,
      signal: null,
      stdout: "x".repeat(80_000),
      stderr: "",
      truncated: false,
    });
    const tool = makeKnowledgeSearchTool({ name: "knowledge-search", runner });

    const result = await tool.execute(
      { mode: "get", objectKey: "bot/Downloads/converted/large.md" },
      ctx(root),
    );

    expect(result.status).toBe("ok");
    expect(result.output?.length).toBeLessThan(40_000);
    expect(result.output).toContain("[KB content truncated");
    expect(result.metadata).toMatchObject({ truncated: true });
  });

  it("returns a clear validation error when search query is missing", async () => {
    const root = await makeRoot();
    const tool = makeKnowledgeSearchTool({
      name: "knowledge-search",
      runner: async () => {
        throw new Error("runner should not be called");
      },
    });

    expect(tool.validate?.({})).toBe("`query` is required for knowledge search");
    const result = await tool.execute({}, ctx(root));

    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("`query` is required");
  });
});
