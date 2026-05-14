/**
 * Cron durable-flag lifecycle + validation tests (§2.2 of
 * docs/plans/2026-04-20-cc-learnings-port-plan.md).
 *
 * Covers:
 *   1. durable=true persists to index.json + survives Agent restart
 *   2. durable=false (default) lives in Session.meta.crons only —
 *      never written to index.json
 *   3. Agent restart hydrates durable crons only
 *   4. Session.close() drops non-durable crons, durable crons remain
 *   5. Subagent session + durable=true → rejected with specific error
 *   6. Session with no delivery channel + durable=true → rejected
 *   7. Session meta schema v1 → v2 stamp (migration framework)
 *   8. Legacy index.json (no `durable` field) hydrates as durable=true
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CronScheduler } from "../cron/CronScheduler.js";
import { makeCronCreateTool } from "./CronCreate.js";
import { Session, type SessionMeta } from "../Session.js";
import type { Agent, AgentConfig } from "../Agent.js";
import type { Tool, ToolContext } from "../Tool.js";
import type { CronCreateInput, CronCreateOutput } from "./CronCreate.js";
import {
  applyMigrations,
  sessionMigrations,
  CURRENT_SESSION_META_SCHEMA_VERSION,
  type SessionMetaShape,
} from "../migrations/index.js";

function silentLogger(): void {
  // no-op
}

/** Minimal ToolContext used by CronCreate.execute tests. */
function makeCtx(turnId = "turn-1", workspaceRoot = "/tmp"): ToolContext {
  return {
    botId: "bot-test",
    sessionKey: "agent:main:app:general:1",
    turnId,
    workspaceRoot,
    abortSignal: new AbortController().signal,
    emitProgress: () => {},
    askUser: async () => {
      throw new Error("askUser not wired");
    },
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

/** Build a CronScheduler-backed stub Agent that Session can read from. */
function makeStubAgent(workspaceRoot: string, crons: CronScheduler): Agent {
  const config: AgentConfig = {
    botId: "bot-test",
    userId: "user-test",
    workspaceRoot,
    gatewayToken: "test",
    apiProxyUrl: "http://localhost",
    chatProxyUrl: "http://localhost",
    redisUrl: "redis://localhost",
    model: "claude-opus-4-7",
  };
  return {
    config,
    sessionsDir: path.join(workspaceRoot, "core-agent", "sessions"),
    crons,
  } as unknown as Agent;
}

function makeSession(
  agent: Agent,
  overrides: Partial<SessionMeta> = {},
): Session {
  const now = Date.now();
  const meta: SessionMeta = {
    sessionKey: "agent:main:app:general:1",
    botId: agent.config.botId,
    channel: { type: "app", channelId: "general" },
    createdAt: now,
    lastActivityAt: now,
    ...overrides,
  };
  return new Session(meta, agent);
}

async function invokeCronCreate(
  tool: Tool<CronCreateInput, CronCreateOutput>,
  input: CronCreateInput,
  ctx: ToolContext,
): Promise<ReturnType<typeof tool.execute>> {
  return tool.execute(input, ctx);
}

describe("Cron durable flag", () => {
  let root: string;

  beforeEach(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), "cron-durable-"));
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("durable=true persists to index.json", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const session = makeSession(agent);
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => session.meta.channel,
      getSession: () => session,
    });

    const result = await invokeCronCreate(
      tool,
      {
        expression: "@hourly",
        prompt: "daily digest",
        durable: true,
      },
      makeCtx(),
    );
    expect(result.status).toBe("ok");
    const cron = result.output?.cron;
    expect(cron?.durable).toBe(true);

    const indexPath = path.join(root, "core-agent", "crons", "index.json");
    const raw = await fs.readFile(indexPath, "utf8");
    const parsed = JSON.parse(raw);
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].cronId).toBe(cron?.cronId);
    expect(parsed[0].durable).toBe(true);
    // Durable crons must NOT be registered on Session.meta.crons —
    // that list is exclusively for session-scoped lifecycle.
    expect(session.meta.crons ?? []).toHaveLength(0);
  });

  it("rejects script cron mode unless the script cron flag is enabled", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const session = makeSession(agent);
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => session.meta.channel,
      getSession: () => session,
    });

    const previous = process.env.CORE_AGENT_SCRIPT_CRON;
    delete process.env.CORE_AGENT_SCRIPT_CRON;
    try {
      const result = await invokeCronCreate(
        tool,
        {
          expression: "@hourly",
          prompt: "ignored for script",
          mode: "script",
          scriptPath: "jobs/check.sh",
        },
        makeCtx(),
      );

      expect(result.status).toBe("error");
      expect(result.errorCode).toBe("script_cron_disabled");
      expect(scheduler.list()).toHaveLength(0);
    } finally {
      if (previous === undefined) {
        delete process.env.CORE_AGENT_SCRIPT_CRON;
      } else {
        process.env.CORE_AGENT_SCRIPT_CRON = previous;
      }
    }
  });

  it("creates script cron records when script cron mode is enabled", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const session = makeSession(agent);
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => session.meta.channel,
      getSession: () => session,
    });

    const previous = process.env.CORE_AGENT_SCRIPT_CRON;
    process.env.CORE_AGENT_SCRIPT_CRON = "1";
    try {
      const result = await invokeCronCreate(
        tool,
        {
          expression: "@hourly",
          prompt: "script fallback label",
          mode: "script",
          scriptPath: "jobs/check.sh",
          timeoutMs: 600_000,
          quietOnEmptyStdout: false,
          deliveryPolicy: "always",
        },
        makeCtx(),
      );

      expect(result.status).toBe("ok");
      expect(result.output?.cron).toMatchObject({
        mode: "script",
        scriptPath: "jobs/check.sh",
        timeoutMs: 300_000,
        quietOnEmptyStdout: false,
        deliveryPolicy: "always",
      });
    } finally {
      if (previous === undefined) {
        delete process.env.CORE_AGENT_SCRIPT_CRON;
      } else {
        process.env.CORE_AGENT_SCRIPT_CRON = previous;
      }
    }
  });

  it("durable=false lives only on Session.meta.crons and is NOT in index.json", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const session = makeSession(agent);
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => session.meta.channel,
      getSession: () => session,
    });

    const result = await invokeCronCreate(
      tool,
      { expression: "*/5 * * * *", prompt: "probe" }, // durable omitted → false
      makeCtx(),
    );
    expect(result.status).toBe("ok");
    const cron = result.output?.cron;
    expect(cron?.durable).toBe(false);
    expect(cron?.sessionKey).toBe(session.meta.sessionKey);
    expect(session.meta.crons).toEqual([cron?.cronId]);

    // index.json must not exist — nothing durable to persist.
    const indexPath = path.join(root, "core-agent", "crons", "index.json");
    await expect(fs.readFile(indexPath, "utf8")).rejects.toMatchObject({
      code: "ENOENT",
    });
  });

  it("Agent restart hydrates only durable crons", async () => {
    const s1 = new CronScheduler(root);
    const agent1 = makeStubAgent(root, s1);
    const session1 = makeSession(agent1);
    const tool1 = makeCronCreateTool({
      scheduler: s1,
      botId: agent1.config.botId,
      userId: agent1.config.userId,
      getSourceChannel: () => session1.meta.channel,
      getSession: () => session1,
    });

    // One durable + one session-scoped.
    await invokeCronCreate(
      tool1,
      { expression: "@hourly", prompt: "durable-one", durable: true },
      makeCtx(),
    );
    await invokeCronCreate(
      tool1,
      { expression: "*/5 * * * *", prompt: "ephemeral" },
      makeCtx(),
    );
    expect(s1.list()).toHaveLength(2);

    // Simulate a pod restart — brand-new scheduler instance.
    const s2 = new CronScheduler(root);
    await s2.hydrate();
    const hydrated = s2.list();
    expect(hydrated).toHaveLength(1);
    expect(hydrated[0]?.durable).toBe(true);
    expect(hydrated[0]?.prompt).toBe("durable-one");
  });

  it("Session.close drops non-durable crons, durable crons remain", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const session = makeSession(agent);
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => session.meta.channel,
      getSession: () => session,
    });

    const dur = await invokeCronCreate(
      tool,
      { expression: "@hourly", prompt: "keep", durable: true },
      makeCtx(),
    );
    const eph = await invokeCronCreate(
      tool,
      { expression: "*/2 * * * *", prompt: "drop" },
      makeCtx(),
    );
    expect(scheduler.list()).toHaveLength(2);

    await session.close();

    const remaining = scheduler.list();
    expect(remaining).toHaveLength(1);
    expect(remaining[0]?.cronId).toBe(dur.output?.cron.cronId);
    expect(scheduler.get(eph.output!.cron.cronId)).toBeNull();
    // idempotent — second close is a no-op.
    await session.close();
    expect(scheduler.list()).toHaveLength(1);
  });

  it("subagent session creating durable=true is rejected", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const subagentSession = makeSession(agent, { role: "subagent" });
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => subagentSession.meta.channel,
      getSession: () => subagentSession,
    });

    const result = await invokeCronCreate(
      tool,
      { expression: "@hourly", prompt: "orphan attempt", durable: true },
      makeCtx(),
    );
    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("durable_subagent_rejected");
    expect(result.errorMessage).toBe(
      "durable=true requires a non-subagent session",
    );
    // Scheduler untouched.
    expect(scheduler.list()).toHaveLength(0);
    // index.json must not have been written.
    await expect(
      fs.readFile(path.join(root, "core-agent", "crons", "index.json"), "utf8"),
    ).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("session with no resolvable channel + durable=true is rejected", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => null,
      getSession: () => null, // truly no session
    });

    const result = await invokeCronCreate(
      tool,
      { expression: "@hourly", prompt: "no channel", durable: true },
      makeCtx(),
    );
    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("durable_no_channel");
    expect(result.errorMessage).toBe(
      "durable=true requires a session with an attached delivery channel (telegram/discord/app)",
    );
    expect(scheduler.list()).toHaveLength(0);
  });

  it("non-durable create still requires a deliverable channel (unchanged semantics)", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => null,
      getSession: () => null,
    });
    const result = await invokeCronCreate(
      tool,
      { expression: "@hourly", prompt: "no channel ephemeral" },
      makeCtx(),
    );
    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("no_delivery_channel");
  });

  it("session meta schema migration stamps v1 → v2", async () => {
    const target = path.join(root, "meta.json");
    const v1: SessionMetaShape = {
      schemaVersion: 1,
      contexts: [
        {
          contextId: "default",
          sessionKey: "agent:main:app:general:7",
          title: "default",
          createdAt: 1,
          lastActivityAt: 2,
          archived: false,
        },
      ],
      activeContextId: "default",
    };
    await fs.writeFile(target, JSON.stringify(v1, null, 2), "utf8");

    const parsed = JSON.parse(
      await fs.readFile(target, "utf8"),
    ) as SessionMetaShape;
    const migrated = await applyMigrations(parsed, sessionMigrations, {
      workspaceRoot: root,
      log: silentLogger,
      targetPath: target,
    });
    expect(migrated.schemaVersion).toBe(CURRENT_SESSION_META_SCHEMA_VERSION);
    expect(CURRENT_SESSION_META_SCHEMA_VERSION).toBeGreaterThanOrEqual(2);
    // Shape preserved exactly — contexts + activeContextId pass through.
    expect(migrated.contexts).toHaveLength(1);
    expect(migrated.contexts[0]!.contextId).toBe("default");
    expect(migrated.activeContextId).toBe("default");
    // Persistent side-effect: the stamped version is on disk.
    const onDisk = JSON.parse(await fs.readFile(target, "utf8")) as SessionMetaShape;
    expect(onDisk.schemaVersion).toBe(CURRENT_SESSION_META_SCHEMA_VERSION);
  });

  it("legacy index.json (no durable field) hydrates as durable=true", async () => {
    // Write a pre-durable-flag index.json by hand. Missing `durable`
    // is the only way to represent "crons that were created before
    // this PR landed" — they were unconditionally persisted, so they
    // must hydrate as durable to preserve behaviour.
    const indexDir = path.join(root, "core-agent", "crons");
    await fs.mkdir(indexDir, { recursive: true });
    const legacy = [
      {
        cronId: "01LEGACYLEGACYLEGACYLEGACY0",
        botId: "bot-test",
        userId: "user-test",
        expression: "@hourly",
        prompt: "legacy cron",
        deliveryChannel: { type: "app", channelId: "ch" },
        enabled: true,
        createdAt: 1_700_000_000_000,
        nextFireAt: 2_000_000_000_000,
        consecutiveFailures: 0,
        // NOTE: no `durable` field here — matches the on-disk shape
        // before this change landed.
      },
    ];
    await fs.writeFile(
      path.join(indexDir, "index.json"),
      JSON.stringify(legacy),
      "utf8",
    );

    const scheduler = new CronScheduler(root);
    await scheduler.hydrate();
    const list = scheduler.list();
    expect(list).toHaveLength(1);
    expect(list[0]?.cronId).toBe("01LEGACYLEGACYLEGACYLEGACY0");
    expect(list[0]?.durable).toBe(true);
  });

  it("durable cron deletion rewrites index.json", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const session = makeSession(agent);
    const tool = makeCronCreateTool({
      scheduler,
      botId: agent.config.botId,
      userId: agent.config.userId,
      getSourceChannel: () => session.meta.channel,
      getSession: () => session,
    });
    const created = await invokeCronCreate(
      tool,
      { expression: "@hourly", prompt: "x", durable: true },
      makeCtx(),
    );
    expect(await scheduler.delete(created.output!.cron.cronId)).toBe(true);
    const raw = await fs.readFile(
      path.join(root, "core-agent", "crons", "index.json"),
      "utf8",
    );
    expect(JSON.parse(raw)).toEqual([]);
  });

  it("registerSessionCron is idempotent; close handles double-invocation", async () => {
    const scheduler = new CronScheduler(root);
    const agent = makeStubAgent(root, scheduler);
    const session = makeSession(agent);
    session.registerSessionCron("same-id");
    session.registerSessionCron("same-id");
    expect(session.meta.crons).toEqual(["same-id"]);
    // close handles missing ids without throwing.
    await expect(session.close()).resolves.toBeUndefined();
    await expect(session.close()).resolves.toBeUndefined();
    expect(session.meta.crons).toEqual([]);
  });
});
