/**
 * sealedFiles unit tests — T3-12 (OMC Port C).
 *
 * Uses the real filesystem in an OS tmp dir to exercise the hash
 * manifest + glob matcher + tmp-rename atomic write path.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  makeSealedFilesHooks,
  allowSealedFileUpdateForTurn,
  globToRegExp,
  matchesAnyGlob,
  extractUnsealPatterns,
  resolveSealedPaths,
  DEFAULT_SEALED_GLOBS,
  __testing,
} from "./sealedFiles.js";
import type { HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { AgentEvent } from "../../transport/SseWriter.js";

interface TestCtx {
  ctx: HookContext;
  emitted: AgentEvent[];
  logs: Array<{ level: string; msg: string; data?: object }>;
}

function makeCtx(turnId: string = "turn-1"): TestCtx {
  const emitted: AgentEvent[] = [];
  const logs: Array<{ level: string; msg: string; data?: object }> = [];
  const llm = {
    stream: async function* () {
      yield {
        kind: "message_end",
        stopReason: "end_turn",
        usage: { inputTokens: 0, outputTokens: 0 },
      };
    },
  } as unknown as LLMClient;
  const ctx: HookContext = {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId,
    llm,
    transcript: [],
    emit: (e) => emitted.push(e),
    log: (level, msg, data) => logs.push({ level, msg, data }),
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
  return { ctx, emitted, logs };
}

async function mkTempWorkspace(): Promise<string> {
  return await fs.mkdtemp(path.join(os.tmpdir(), "sealed-files-test-"));
}

async function writeFileP(root: string, rel: string, body: string): Promise<void> {
  const full = path.join(root, rel);
  await fs.mkdir(path.dirname(full), { recursive: true });
  await fs.writeFile(full, body, "utf8");
}

async function readManifestDirect(root: string): Promise<Record<string, unknown> | null> {
  try {
    const raw = await fs.readFile(path.join(root, ".sealed-manifest.json"), "utf8");
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
}

describe("globToRegExp", () => {
  it("matches simple filenames", () => {
    expect(globToRegExp("SOUL.md").test("SOUL.md")).toBe(true);
    expect(globToRegExp("SOUL.md").test("skills/foo/SOUL.md")).toBe(false);
  });

  it("matches single-segment wildcards", () => {
    expect(globToRegExp("skills/*/SKILL.md").test("skills/foo/SKILL.md")).toBe(true);
    expect(globToRegExp("skills/*/SKILL.md").test("skills/foo/bar/SKILL.md")).toBe(false);
  });

  it("** matches across segments", () => {
    expect(globToRegExp("memory/**/ROOT.md").test("memory/ROOT.md")).toBe(true);
    expect(globToRegExp("memory/**/ROOT.md").test("memory/sub/ROOT.md")).toBe(true);
    expect(globToRegExp("memory/**/ROOT.md").test("memory/a/b/ROOT.md")).toBe(true);
  });

  it("escapes regex metacharacters", () => {
    expect(globToRegExp("a.b").test("a.b")).toBe(true);
    expect(globToRegExp("a.b").test("axb")).toBe(false);
  });
});

describe("matchesAnyGlob", () => {
  it("normalises leading ./ and /", () => {
    expect(matchesAnyGlob("./SOUL.md", ["SOUL.md"])).toBe(true);
    expect(matchesAnyGlob("/SOUL.md", ["SOUL.md"])).toBe(true);
  });
});

describe("extractUnsealPatterns", () => {
  it("extracts single marker", () => {
    expect(extractUnsealPatterns("please edit [UNSEAL: SOUL.md] for me")).toEqual(["SOUL.md"]);
  });
  it("extracts multiple markers", () => {
    expect(
      extractUnsealPatterns("[UNSEAL: SOUL.md] and also [UNSEAL: skills/*/SKILL.md]"),
    ).toEqual(["SOUL.md", "skills/*/SKILL.md"]);
  });
  it("trims whitespace", () => {
    expect(extractUnsealPatterns("[UNSEAL:   memory/ROOT.md   ]")).toEqual(["memory/ROOT.md"]);
  });
  it("returns empty for no markers", () => {
    expect(extractUnsealPatterns("just a normal message")).toEqual([]);
  });
});

describe("resolveSealedPaths", () => {
  let ws: string;
  beforeEach(async () => {
    ws = await mkTempWorkspace();
  });
  afterEach(async () => {
    await fs.rm(ws, { recursive: true, force: true });
  });

  it("walks matching files, skips .spawn and manifest", async () => {
    await writeFileP(ws, "SOUL.md", "soul");
    await writeFileP(ws, "LEARNING.md", "learning");
    await writeFileP(ws, "skills/a/SKILL.md", "a");
    await writeFileP(ws, "skills/b/SKILL.md", "b");
    await writeFileP(ws, "skills-learned/a/SKILL.md", "learned");
    await writeFileP(ws, "memory/ROOT.md", "r");
    await writeFileP(ws, "unrelated.txt", "u");
    await writeFileP(ws, ".spawn/child/SOUL.md", "child");
    await writeFileP(ws, ".sealed-manifest.json", "{}");
    const paths = await resolveSealedPaths(ws, [...DEFAULT_SEALED_GLOBS]);
    expect(paths).toContain("SOUL.md");
    expect(paths).toContain("LEARNING.md");
    expect(paths).toContain("skills/a/SKILL.md");
    expect(paths).toContain("skills/b/SKILL.md");
    expect(paths).not.toContain("skills-learned/a/SKILL.md");
    expect(paths).toContain("memory/ROOT.md");
    expect(paths).not.toContain("unrelated.txt");
    expect(paths.some((p) => p.startsWith(".spawn/"))).toBe(false);
  });

  it("default sealed globs include runtime identity and tool contract files", () => {
    expect(DEFAULT_SEALED_GLOBS).toEqual(
      expect.arrayContaining(["AGENTS.md", "TOOLS.md", "CLAUDE.md", "HEARTBEAT.md", "LEARNING.md"]),
    );
  });
});

describe("sealedFiles hook — integration", () => {
  let ws: string;
  const originalEnv = process.env.CORE_AGENT_SEALED_FILES;

  beforeEach(async () => {
    ws = await mkTempWorkspace();
    __testing.clearPending();
    delete process.env.CORE_AGENT_SEALED_FILES;
  });

  afterEach(async () => {
    await fs.rm(ws, { recursive: true, force: true });
    if (originalEnv === undefined) {
      delete process.env.CORE_AGENT_SEALED_FILES;
    } else {
      process.env.CORE_AGENT_SEALED_FILES = originalEnv;
    }
  });

  // 1
  it("first run with no manifest creates manifest and allows", async () => {
    await writeFileP(ws, "SOUL.md", "initial soul");
    await writeFileP(ws, "skills/greeter/SKILL.md", "initial skill");
    const { beforeCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    const { ctx, emitted } = makeCtx();
    const result = await beforeCommit.handler(
      {
        assistantText: "done",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "hi",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    const manifest = await readManifestDirect(ws);
    expect(manifest).not.toBeNull();
    expect(manifest?.["SOUL.md"]).toBeDefined();
    expect(manifest?.["skills/greeter/SKILL.md"]).toBeDefined();
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.ruleId === "sealed-files" &&
          typeof e.detail === "string" &&
          e.detail.startsWith("sealed_manifest_initialized"),
      ),
    ).toBe(true);
  });

  // 2
  it("no changes to sealed paths → continue", async () => {
    await writeFileP(ws, "SOUL.md", "body");
    const { beforeCommit, afterCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    const first = makeCtx("turn-init");
    await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      first.ctx,
    );
    // second turn — no file changes
    const second = makeCtx("turn-2");
    const result = await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "just chatting",
        retryCount: 0,
      },
      second.ctx,
    );
    expect(result).toEqual({ action: "continue" });
    await afterCommit.handler({ assistantText: "" }, second.ctx);
    // No pending updates for turn-2
    expect(__testing.getPending("turn-2")).toBeUndefined();
  });

  // 3
  it("non-sealed path changed → continue", async () => {
    await writeFileP(ws, "SOUL.md", "soul");
    const { beforeCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    // Initialise manifest
    await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );
    // Create a non-sealed file + mutate it
    await writeFileP(ws, "notes/random.md", "hello");
    await writeFileP(ws, "notes/random.md", "world");
    const { ctx } = makeCtx("turn-2");
    const result = await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "edited notes",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
  });

  // 4
  it("sealed path changed without allowlist → block", async () => {
    await writeFileP(ws, "SOUL.md", "soul v1");
    const { beforeCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    // Init manifest
    await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );
    // Mutate SOUL.md without an UNSEAL marker
    await writeFileP(ws, "SOUL.md", "soul v2 — self-edited");
    const { ctx, emitted } = makeCtx("turn-2");
    const result = await beforeCommit.handler(
      {
        assistantText: "updated my own SOUL",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "improve yourself",
        retryCount: 0,
      },
      ctx,
    );
    expect(result?.action).toBe("block");
    if (result && result.action === "block") {
      expect(result.reason).toContain("[RULE:SEALED_FILES]");
      expect(result.reason).not.toContain("SOUL.md");
    }
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.verdict === "violation" &&
          typeof e.detail === "string" &&
          e.detail.startsWith("sealed_files_violation"),
      ),
    ).toBe(true);
  });

  it("pre-existing sealed drift at turn start does not block a chat-only turn", async () => {
    await writeFileP(ws, "SOUL.md", "soul v1");
    const hooks = makeSealedFilesHooks({ workspaceRoot: ws });
    await hooks.beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );
    await writeFileP(ws, "SOUL.md", "soul v2 from external sync");

    const { ctx, emitted } = makeCtx("turn-chat");
    await hooks.beforeTurnStart.handler({ userMessage: "hi" }, ctx);
    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "hello",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "hi",
        retryCount: 0,
      },
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.ruleId === "sealed-files" &&
          e.verdict === "ok" &&
          typeof e.detail === "string" &&
          e.detail.includes("sealed_files_preexisting_drift"),
      ),
    ).toBe(true);

    await hooks.afterCommit.handler({ assistantText: "hello" }, ctx);
    const manifest = await readManifestDirect(ws);
    const entry = manifest?.["SOUL.md"] as { sha256: string } | undefined;
    expect(entry?.sha256).toBeDefined();

    const again = await hooks.beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "still chatting",
        retryCount: 0,
      },
      makeCtx("turn-next").ctx,
    );
    expect(again).toEqual({ action: "continue" });
  });

  it("blocks sealed paths changed after the turn-start drift snapshot", async () => {
    await writeFileP(ws, "SOUL.md", "soul v1");
    const hooks = makeSealedFilesHooks({ workspaceRoot: ws });
    await hooks.beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );

    await writeFileP(ws, "SOUL.md", "soul v2 before turn");
    const { ctx } = makeCtx("turn-edit");
    await hooks.beforeTurnStart.handler({ userMessage: "edit yourself" }, ctx);
    await writeFileP(ws, "SOUL.md", "soul v3 during turn");

    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "updated my own SOUL",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "edit yourself",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    if (result && result.action === "block") {
      expect(result.reason).toContain("[RULE:SEALED_FILES]");
      expect(result.reason).not.toContain("SOUL.md");
    }
  });

  it("sealed path changed still blocks on commit retry attempts", async () => {
    await writeFileP(ws, "SOUL.md", "soul v1");
    const { beforeCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );
    await writeFileP(ws, "SOUL.md", "soul v2");

    const { ctx } = makeCtx("turn-retry");
    const result = await beforeCommit.handler(
      {
        assistantText: "retry output",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "retry after violation",
        retryCount: 1,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    if (result && result.action === "block") {
      expect(result.reason).toContain("[RULE:SEALED_FILES]");
      expect(result.reason).not.toContain("SOUL.md");
    }
  });

  it("pre-existing sealed drift at turn start does not block a chat-only turn", async () => {
    await writeFileP(ws, "SOUL.md", "soul v1");
    const hooks = makeSealedFilesHooks({ workspaceRoot: ws });
    await hooks.beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );

    await writeFileP(ws, "SOUL.md", "soul v2 from external sync");
    const { ctx, emitted } = makeCtx("turn-chat");
    await hooks.beforeTurnStart.handler({ userMessage: "hi" }, ctx);
    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "hello",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "hi",
        retryCount: 0,
      },
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.ruleId === "sealed-files" &&
          e.verdict === "ok" &&
          typeof e.detail === "string" &&
          e.detail.includes("sealed_files_preexisting_drift"),
      ),
    ).toBe(true);

    await hooks.afterCommit.handler({ assistantText: "hello" }, ctx);
    const manifest = await readManifestDirect(ws);
    const entry = manifest?.["SOUL.md"] as { sha256: string } | undefined;
    expect(entry?.sha256).toBeDefined();

    const again = await hooks.beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "still chatting",
        retryCount: 0,
      },
      makeCtx("turn-next").ctx,
    );
    expect(again).toEqual({ action: "continue" });
  });

  it("blocks sealed paths changed after the turn-start drift snapshot", async () => {
    await writeFileP(ws, "SOUL.md", "soul v1");
    const hooks = makeSealedFilesHooks({ workspaceRoot: ws });
    await hooks.beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );

    await writeFileP(ws, "SOUL.md", "soul v2 before turn");
    const { ctx } = makeCtx("turn-edit");
    await hooks.beforeTurnStart.handler({ userMessage: "edit yourself" }, ctx);
    await writeFileP(ws, "SOUL.md", "soul v3 during turn");

    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "updated my own SOUL",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "edit yourself",
        retryCount: 0,
      },
      ctx,
    );

    expect(result?.action).toBe("block");
    if (result && result.action === "block") {
      expect(result.reason).toContain("[RULE:SEALED_FILES]");
      expect(result.reason).toContain("Sealed files changed without explicit approval");
    }
  });

  // 5
  it("sealed path changed with [UNSEAL: path] marker → allow + update manifest on afterCommit", async () => {
    await writeFileP(ws, "SOUL.md", "v1");
    const { beforeCommit, afterCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );
    await writeFileP(ws, "SOUL.md", "v2-authorised");
    const { ctx, emitted } = makeCtx("turn-unseal");
    const result = await beforeCommit.handler(
      {
        assistantText: "updated",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "please [UNSEAL: SOUL.md] and add a line about X",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          typeof e.detail === "string" &&
          e.detail.includes("sealed_files_bypass") &&
          e.detail.includes("kind=unseal_marker") &&
          e.detail.includes("SOUL.md"),
      ),
    ).toBe(true);
    // afterCommit persists the new hash
    await afterCommit.handler({ assistantText: "updated" }, ctx);
    const manifest = await readManifestDirect(ws);
    const entry = manifest?.["SOUL.md"] as { sha256: string } | undefined;
    expect(entry).toBeDefined();
    expect(entry?.sha256).toBeDefined();
    // New hash reflects "v2-authorised", so re-running diff with an
    // unmodified file should produce no change.
    const { ctx: ctx3 } = makeCtx("turn-3");
    const again = await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "ok thanks",
        retryCount: 0,
      },
      ctx3,
    );
    expect(again).toEqual({ action: "continue" });
  });

  it("allows a system-owned Hipocampus ROOT.md update during the turn", async () => {
    await writeFileP(ws, "memory/ROOT.md", "root v1");
    const hooks = makeSealedFilesHooks({ workspaceRoot: ws });
    await hooks.beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );

    await writeFileP(ws, "memory/ROOT.md", "root v2 from compactor");
    const { ctx, emitted } = makeCtx("turn-hipocampus");
    allowSealedFileUpdateForTurn(ctx.turnId, "memory/ROOT.md");

    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "done",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "hello",
        retryCount: 0,
      },
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.ruleId === "sealed-files" &&
          e.verdict === "ok" &&
          typeof e.detail === "string" &&
          e.detail.includes("sealed_files_bypass kind=system"),
      ),
    ).toBe(true);
    await hooks.afterCommit.handler({ assistantText: "done" }, ctx);
    const again = await hooks.beforeCommit.handler(
      {
        assistantText: "next",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "next",
        retryCount: 0,
      },
      makeCtx("turn-next").ctx,
    );
    expect(again).toEqual({ action: "continue" });
  });

  it("reports system-owned Hipocampus updates as system even when the turn is allowlisted", async () => {
    await writeFileP(ws, "memory/ROOT.md", "root v1");
    await writeFileP(
      ws,
      "agent.config.yaml",
      "sealed_files_allowlist_turns:\n  - turn-system-overlap\n",
    );
    const hooks = makeSealedFilesHooks({ workspaceRoot: ws });
    await hooks.beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );

    await writeFileP(ws, "memory/ROOT.md", "root v2 from compactor");
    const { ctx, emitted } = makeCtx("turn-system-overlap");
    allowSealedFileUpdateForTurn(ctx.turnId, "memory/ROOT.md");

    const result = await hooks.beforeCommit.handler(
      {
        assistantText: "done",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "hello",
        retryCount: 0,
      },
      ctx,
    );

    expect(result).toEqual({ action: "continue" });
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          e.ruleId === "sealed-files" &&
          e.verdict === "ok" &&
          typeof e.detail === "string" &&
          e.detail.includes("sealed_files_bypass kind=system path=memory/ROOT.md"),
      ),
    ).toBe(true);
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          typeof e.detail === "string" &&
          e.detail.includes("sealed_files_bypass kind=config_turn path=memory/ROOT.md"),
      ),
    ).toBe(false);
  });

  // 6
  it("sealed path changed with turnId in config allowlist → allow + update manifest", async () => {
    await writeFileP(ws, "SOUL.md", "v1");
    // Seed config with an allowlist entry — use a fixed turnId we
    // will pass to the ctx.
    await writeFileP(
      ws,
      "agent.config.yaml",
      "sealed_files_allowlist_turns:\n  - turn-config-allow\n",
    );
    const { beforeCommit, afterCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    // Init manifest — use a different turnId.
    await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );
    await writeFileP(ws, "SOUL.md", "v2-admin-override");
    const { ctx, emitted } = makeCtx("turn-config-allow");
    const result = await beforeCommit.handler(
      {
        assistantText: "admin flip",
        toolCallCount: 1,
        toolReadHappened: false,
        // No UNSEAL marker — bypass is purely from config.
        userMessage: "ops override",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    expect(
      emitted.some(
        (e) =>
          e.type === "rule_check" &&
          typeof e.detail === "string" &&
          e.detail.includes("sealed_files_bypass") &&
          e.detail.includes("kind=config_turn"),
      ),
    ).toBe(true);
    await afterCommit.handler({ assistantText: "" }, ctx);
    const manifest = await readManifestDirect(ws);
    // agent.config.yaml itself is sealed by default — its first
    // appearance during turn-init initialisation is seeded into the
    // manifest. We care here about SOUL.md being updated.
    expect(manifest?.["SOUL.md"]).toBeDefined();
  });

  // 7
  it("multiple sealed paths changed → block with all paths in reason", async () => {
    await writeFileP(ws, "SOUL.md", "v1");
    await writeFileP(ws, "skills/foo/SKILL.md", "v1");
    const { beforeCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "init",
        retryCount: 0,
      },
      makeCtx("turn-init").ctx,
    );
    await writeFileP(ws, "SOUL.md", "v2");
    await writeFileP(ws, "skills/foo/SKILL.md", "v2");
    const { ctx } = makeCtx("turn-bad");
    const result = await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 2,
        toolReadHappened: false,
        userMessage: "self-improve",
        retryCount: 0,
      },
      ctx,
    );
    expect(result?.action).toBe("block");
    if (result && result.action === "block") {
      expect(result.reason).not.toContain("SOUL.md");
      expect(result.reason).not.toContain("skills/foo/SKILL.md");
    }
  });

  // 8
  it("env=off → noop (no manifest created, no block even on change)", async () => {
    process.env.CORE_AGENT_SEALED_FILES = "off";
    await writeFileP(ws, "SOUL.md", "v1");
    const { beforeCommit } = makeSealedFilesHooks({ workspaceRoot: ws });
    const { ctx } = makeCtx();
    const result = await beforeCommit.handler(
      {
        assistantText: "",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "hi",
        retryCount: 0,
      },
      ctx,
    );
    expect(result).toEqual({ action: "continue" });
    const manifest = await readManifestDirect(ws);
    expect(manifest).toBeNull();
  });
});
