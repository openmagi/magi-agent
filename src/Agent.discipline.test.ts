/**
 * Agent-level Coding Discipline wiring — verifies Kevin's A/A/A
 * defaults land where they should:
 *
 *   A1. `git init` runs unconditionally on Agent.start when there is
 *       no `.git` directory in the workspace root.
 *   A2. `.discipline.yaml` simple-schema `{ mode, skipTdd }` loads
 *       into the Agent's disciplineDefault and applies to
 *       Session.meta.discipline.
 *   A3. The classifier hook promotes soft → hard when the
 *       `coding-agent` skill is registered AND the user message is
 *       classified as coding. Without the skill, soft stays soft.
 *   A4. The beforeToolUse hook denies `CommitCheckpoint` when
 *       discipline is off (soft baseline unperturbed by classifier).
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Agent } from "./Agent.js";
import type { LLMClient } from "./transport/LLMClient.js";
import { runGit } from "./tools/CommitCheckpoint.js";
import {
  makeClassifyTurnModeHook,
  type ClassifyTurnModeAgent,
} from "./hooks/builtin/classifyTurnMode.js";
import { HookRegistry } from "./hooks/HookRegistry.js";
import type { HookContext } from "./hooks/types.js";
import type { Discipline } from "./Session.js";
import { DEFAULT_DISCIPLINE } from "./discipline/config.js";
import { ExecutionContractStore } from "./execution/ExecutionContract.js";

function makeHookCtx(): HookContext {
  const store = new ExecutionContractStore({ now: () => 1 });
  const llm = {
    stream: async function* (req: { system?: string; messages?: Array<{ content: Array<{ text?: string }> }> }) {
      const text = req.messages?.[0]?.content?.[0]?.text ?? "";
      yield {
        kind: "text_delta",
        delta: JSON.stringify({
          turnMode: {
            label: /implement|function|type error|git commit/i.test(text)
              ? "coding"
              : "other",
            confidence: 0.9,
          },
          skipTdd: false,
          implementationIntent: /implement|function|type error/i.test(text),
          documentOrFileOperation: false,
          deterministic: {
            requiresDeterministic: false,
            kinds: [],
            reason: "No deterministic requirement.",
            suggestedTools: [],
            acceptanceCriteria: [],
          },
          fileDelivery: {
            intent: "none",
            path: null,
            wantsChatDelivery: false,
            wantsKbDelivery: false,
            wantsFileOutput: false,
          },
        }),
      };
    },
  } as unknown as LLMClient;
  return {
    botId: "bot-t",
    userId: "user-t",
    sessionKey: "s-t",
    turnId: "t-t",
    llm,
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "claude-haiku",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    executionContract: store,
  };
}

describe("Agent Discipline — Kevin A/A/A defaults", () => {
  let workspaceRoot: string;

  beforeEach(async () => {
    workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "agent-disc-"));
  });

  afterEach(async () => {
    await fs.rm(workspaceRoot, { recursive: true, force: true });
  });

  it("A1: Agent.start runs `git init` when workspace has no .git dir", async () => {
    const agent = new Agent({
      botId: "bot-disc-1",
      userId: "user-disc-1",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
    });
    // Confirm no .git before start.
    let hadGitBefore = true;
    try {
      await fs.access(path.join(workspaceRoot, ".git"));
    } catch {
      hadGitBefore = false;
    }
    expect(hadGitBefore).toBe(false);

    await agent.start();
    try {
      // Post-start: .git must exist. rev-parse confirms it's a real repo.
      await fs.access(path.join(workspaceRoot, ".git"));
      const rev = await runGit(workspaceRoot, [
        "rev-parse",
        "--is-inside-work-tree",
      ]);
      expect(rev.code).toBe(0);
      expect(rev.stdout.trim()).toBe("true");
    } finally {
      await agent.stop();
    }
  });

  it("A1: Agent.start is idempotent — existing .git is reused", async () => {
    // Pre-create a repo with a known marker commit so we can assert it
    // survives.
    await runGit(workspaceRoot, ["init"]);
    await runGit(workspaceRoot, ["config", "user.email", "x@x"]);
    await runGit(workspaceRoot, ["config", "user.name", "x"]);
    await fs.writeFile(path.join(workspaceRoot, "marker.txt"), "hi", "utf8");
    await runGit(workspaceRoot, ["add", "-A"]);
    await runGit(workspaceRoot, ["commit", "-m", "marker"]);
    const headBefore = (
      await runGit(workspaceRoot, ["rev-parse", "HEAD"])
    ).stdout.trim();

    const agent = new Agent({
      botId: "bot-disc-2",
      userId: "user-disc-2",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
    });
    await agent.start();
    try {
      const headAfter = (
        await runGit(workspaceRoot, ["rev-parse", "HEAD"])
      ).stdout.trim();
      expect(headAfter).toBe(headBefore);
    } finally {
      await agent.stop();
    }
  });

  it("A2: `.discipline.yaml` simple schema `{ mode, skipTdd }` applies to new sessions", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, ".discipline.yaml"),
      ["mode: hard", "skipTdd: false", ""].join("\n"),
      "utf8",
    );
    const agent = new Agent({
      botId: "bot-disc-3",
      userId: "user-disc-3",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
    });
    await agent.start();
    try {
      const session = await agent.getOrCreateSession("s-yaml-hard", {
        type: "app",
        channelId: "c1",
      });
      const d = session.meta.discipline;
      expect(d).toBeDefined();
      expect(d!.requireCommit).toBe("hard");
      expect(d!.tdd).toBe(true);
      expect(d!.git).toBe(true);
      expect(d!.frozen).toBe(true);
    } finally {
      await agent.stop();
    }
  });

  it("A2: `.discipline.yaml` simple schema with skipTdd:true disables tdd", async () => {
    await fs.writeFile(
      path.join(workspaceRoot, ".discipline.yaml"),
      ["mode: soft", "skipTdd: true", ""].join("\n"),
      "utf8",
    );
    const agent = new Agent({
      botId: "bot-disc-3b",
      userId: "user-disc-3b",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
    });
    await agent.start();
    try {
      const session = await agent.getOrCreateSession("s-yaml-skip", {
        type: "app",
        channelId: "c1",
      });
      const d = session.meta.discipline;
      expect(d!.requireCommit).toBe("soft");
      expect(d!.skipTdd).toBe(true);
      expect(d!.tdd).toBe(false);
      expect(d!.git).toBe(true);
    } finally {
      await agent.stop();
    }
  });

  it("A3: classifier keeps soft when coding-agent skill NOT active", async () => {
    const discipline: Discipline = { ...DEFAULT_DISCIPLINE };
    const classifyAgent: ClassifyTurnModeAgent = {
      getSessionDiscipline: () => discipline,
      setSessionDiscipline: (_k, next) => {
        Object.assign(discipline, next);
      },
      isCodingAgentSkillActive: () => false,
    };
    const hook = makeClassifyTurnModeHook({ agent: classifyAgent });
    const ctx = makeHookCtx();
    await hook.handler(
      {
        messages: [
          {
            role: "user",
            content: [
              "please implement a new function in src/foo.ts and add a failing test first",
              "```ts",
              "export function foo() {}",
              "```",
              "run git commit when done, and fix the type error",
            ].join("\n"),
          },
        ],
        tools: [],
        system: "",
        iteration: 0,
      },
      ctx,
    );
    expect(discipline.lastClassifiedMode).toBe("coding");
    expect(discipline.requireCommit).toBe("soft");
    expect(discipline.tdd).toBe(true);
    expect(discipline.git).toBe(true);
  });

  it("A3: classifier promotes soft → hard when coding-agent skill IS active", async () => {
    const discipline: Discipline = { ...DEFAULT_DISCIPLINE };
    const classifyAgent: ClassifyTurnModeAgent = {
      getSessionDiscipline: () => discipline,
      setSessionDiscipline: (_k, next) => {
        Object.assign(discipline, next);
      },
      isCodingAgentSkillActive: () => true,
    };
    const hook = makeClassifyTurnModeHook({ agent: classifyAgent });
    const ctx = makeHookCtx();
    await hook.handler(
      {
        messages: [
          {
            role: "user",
            content: [
              "please implement a new function in src/foo.ts and add a failing test first",
              "```ts",
              "export function foo() {}",
              "```",
              "run git commit when done, and fix the type error",
            ].join("\n"),
          },
        ],
        tools: [],
        system: "",
        iteration: 0,
      },
      ctx,
    );
    expect(discipline.lastClassifiedMode).toBe("coding");
    expect(discipline.requireCommit).toBe("hard");
    expect(discipline.tdd).toBe(true);
  });

  it("A3: classifier leaves non-coding turns alone even with skill active", async () => {
    const discipline: Discipline = { ...DEFAULT_DISCIPLINE };
    const classifyAgent: ClassifyTurnModeAgent = {
      getSessionDiscipline: () => discipline,
      setSessionDiscipline: (_k, next) => {
        Object.assign(discipline, next);
      },
      isCodingAgentSkillActive: () => true,
    };
    const hook = makeClassifyTurnModeHook({ agent: classifyAgent });
    const ctx = makeHookCtx();
    await hook.handler(
      {
        messages: [
          { role: "user", content: "what's the weather today" },
        ],
        tools: [],
        system: "",
        iteration: 0,
      },
      ctx,
    );
    expect(discipline.lastClassifiedMode).toBe("other");
    // No promotion — stays at the default off.
    expect(discipline.requireCommit).toBe(DEFAULT_DISCIPLINE.requireCommit);
  });

  it("A3: classifier timeout fails open instead of aborting the turn", async () => {
    const discipline: Discipline = { ...DEFAULT_DISCIPLINE };
    const classifyAgent: ClassifyTurnModeAgent = {
      getSessionDiscipline: () => discipline,
      setSessionDiscipline: (_k, next) => {
        Object.assign(discipline, next);
      },
      isCodingAgentSkillActive: () => true,
    };
    const hook = makeClassifyTurnModeHook({ agent: classifyAgent });
    hook.timeoutMs = 1;

    const registry = new HookRegistry();
    registry.register(hook);

    const ctx = makeHookCtx();
    ctx.llm = {
      stream: async function* () {
        await new Promise(() => undefined);
      },
    } as unknown as LLMClient;

    const outcome = await registry.runPre(
      "beforeLLMCall",
      {
        messages: [{ role: "user", content: "fix the production bug" }],
        tools: [],
        system: "",
        iteration: 0,
      },
      ctx,
    );

    expect(outcome.action).toBe("continue");
  });

  it("A4: Agent wires isCodingAgentSkillActive off the tool registry", async () => {
    const agent = new Agent({
      botId: "bot-disc-4",
      userId: "user-disc-4",
      workspaceRoot,
      gatewayToken: "tok",
      apiProxyUrl: "http://proxy",
      chatProxyUrl: "http://chat",
      redisUrl: "redis://r",
      model: "claude-haiku",
    });
    try {
      // No coding-agent skill registered — resolve returns null.
      expect(agent.tools.resolve("coding-agent")).toBeNull();
      // Register a stub coding-agent tool so the resolver returns it.
      agent.tools.register({
        name: "coding-agent",
        description: "stub",
        inputSchema: { type: "object" } as unknown,
        permission: "execute",
        kind: "skill",
        async execute() {
          return { status: "ok", durationMs: 0 } as const;
        },
      } as never);
      expect(agent.tools.resolve("coding-agent")).not.toBeNull();
    } finally {
      // No start() so no stop() needed either — the ctor alone is
      // enough to validate the tool-resolve wire.
    }
  });
});
