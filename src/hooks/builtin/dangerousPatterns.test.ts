/**
 * dangerousPatterns unit tests — T2-09 (Phase 3).
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  makeDangerousPatternsHook,
  resolveRulesFromConfig,
  DEFAULT_DANGEROUS_PATTERNS,
} from "./dangerousPatterns.js";
import type { HookContext, HookHandler, HookResult, HookArgs } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { AgentEvent } from "../../transport/SseWriter.js";

interface TestCtx {
  ctx: HookContext;
  emitted: AgentEvent[];
  logs: Array<{ level: string; msg: string; data?: object }>;
}

function makeCtx(turnId: string = "turn-dp"): TestCtx {
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
  return await fs.mkdtemp(path.join(os.tmpdir(), "dangerous-patterns-test-"));
}

async function writeConfig(root: string, body: string): Promise<void> {
  await fs.writeFile(path.join(root, "agent.config.yaml"), body, "utf8");
}

type DPHandler = HookHandler<"beforeToolUse">;
type DPResult = HookResult<HookArgs["beforeToolUse"]> | void;

async function runHook(
  handler: DPHandler,
  ctx: HookContext,
  args: HookArgs["beforeToolUse"],
): Promise<DPResult> {
  return (await handler(args, ctx)) as DPResult;
}

const ORIGINAL_ENV = process.env.CORE_AGENT_DANGEROUS_PATTERNS;

describe("resolveRulesFromConfig", () => {
  it("returns null when key is absent", () => {
    expect(resolveRulesFromConfig({})).toBeNull();
    expect(resolveRulesFromConfig(null)).toBeNull();
  });
  it("returns empty list when explicitly set to []", () => {
    expect(resolveRulesFromConfig({ dangerous_patterns: [] })).toEqual([]);
  });
  it("filters out entries missing scope or match", () => {
    const rules = resolveRulesFromConfig({
      dangerous_patterns: [
        { match: "", scope: "bash" },
        { match: "ok", scope: "weird" },
        { match: "rm", scope: "bash" },
      ],
    });
    expect(rules).toEqual([
      { match: "rm", scope: "bash", kind: "substring", action: "ask" },
    ]);
  });
});

describe("dangerousPatterns hook", () => {
  let root: string;

  beforeEach(async () => {
    root = await mkTempWorkspace();
    delete process.env.CORE_AGENT_DANGEROUS_PATTERNS;
  });

  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
    if (ORIGINAL_ENV === undefined) {
      delete process.env.CORE_AGENT_DANGEROUS_PATTERNS;
    } else {
      process.env.CORE_AGENT_DANGEROUS_PATTERNS = ORIGINAL_ENV;
    }
  });

  it("case 1: no config → default rules match 'rm -rf /' and ask", async () => {
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx, emitted } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "Bash",
      toolUseId: "t1",
      input: { command: "rm -rf / --no-preserve-root" },
    });
    expect(result).toBeDefined();
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "ask",
    });
    expect(emitted.some((e) => e.type === "rule_check" && (e as { verdict?: string }).verdict === "violation")).toBe(true);
  });

  it("case 2: bash command not matching any rule → continue", async () => {
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "Bash",
      toolUseId: "t2",
      input: { command: "ls -la" },
    });
    expect(result).toEqual({ action: "continue" });
  });

  it("case 3: FileWrite to .env → matched by default regex, ask", async () => {
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "FileWrite",
      toolUseId: "t3",
      input: { path: ".env", content: "X=1" },
    });
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "ask",
    });
  });

  it("case 4: FileRead on a normal path → continue", async () => {
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "FileRead",
      toolUseId: "t4",
      input: { path: "src/index.ts" },
    });
    expect(result).toEqual({ action: "continue" });
  });

  it("case 5: config overrides defaults with empty list → no matches", async () => {
    await writeConfig(root, "dangerous_patterns: []\n");
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "Bash",
      toolUseId: "t5",
      input: { command: "rm -rf / --no-preserve-root" },
    });
    expect(result).toEqual({ action: "continue" });
  });

  it("case 6: action: deny rule → returns deny decision", async () => {
    await writeConfig(
      root,
      "dangerous_patterns:\n  - match: \"drop table\"\n    scope: \"bash\"\n    action: \"deny\"\n",
    );
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "Bash",
      toolUseId: "t6",
      input: { command: "psql -c 'drop table users'" },
    });
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "deny",
    });
    const reason = (result as { reason?: string }).reason ?? "";
    expect(reason).toContain("[DANGEROUS_PATTERN]");
    expect(reason).toContain("drop table");
  });

  it("case 7: scope mismatch (bash rule + FileWrite tool) → no match", async () => {
    await writeConfig(
      root,
      "dangerous_patterns:\n  - match: \"secrets/\"\n    scope: \"bash\"\n",
    );
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "FileWrite",
      toolUseId: "t7",
      input: { path: "secrets/api-key.txt", content: "sk-..." },
    });
    expect(result).toEqual({ action: "continue" });
  });

  it("case 8: env=off → noop (continue) even for dangerous command", async () => {
    process.env.CORE_AGENT_DANGEROUS_PATTERNS = "off";
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "Bash",
      toolUseId: "t8",
      input: { command: "rm -rf /" },
    });
    expect(result).toEqual({ action: "continue" });
  });

  it("bonus: invalid regex rule is skipped with warn log, others still evaluated", async () => {
    await writeConfig(
      root,
      "dangerous_patterns:\n  - match: \"(unclosed\"\n    scope: \"bash\"\n    kind: \"regex\"\n  - match: \"sudo\"\n    scope: \"bash\"\n",
    );
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx, logs } = makeCtx();
    const result = await runHook(hook.handler, ctx, {
      toolName: "Bash",
      toolUseId: "tb",
      input: { command: "sudo rm file" },
    });
    expect(result).toMatchObject({
      action: "permission_decision",
      decision: "ask",
    });
    expect(logs.some((l) => l.level === "warn" && l.msg.includes("invalid regex"))).toBe(true);
  });

  it("bonus: default set has expected 7 rules", () => {
    expect(DEFAULT_DANGEROUS_PATTERNS.length).toBe(12);
  });

  it("default rules ask before git push and deny destructive git reset", async () => {
    const hook = makeDangerousPatternsHook({ workspaceRoot: root });
    const { ctx } = makeCtx();
    const push = await runHook(hook.handler, ctx, {
      toolName: "Bash",
      toolUseId: "git-push",
      input: { command: "git push origin feature/runtime-execution-contract" },
    });
    expect(push).toMatchObject({
      action: "permission_decision",
      decision: "ask",
    });

    const reset = await runHook(hook.handler, ctx, {
      toolName: "Bash",
      toolUseId: "git-reset",
      input: { command: "git reset --hard HEAD~1" },
    });
    expect(reset).toMatchObject({
      action: "permission_decision",
      decision: "deny",
    });
  });
});
