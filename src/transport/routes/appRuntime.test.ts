import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { HttpServer } from "../HttpServer.js";
import { AuditLog } from "../../storage/AuditLog.js";
import type { PermissionClass } from "../../Tool.js";

interface FakeSession {
  meta: {
    sessionKey: string;
    botId: string;
    channel: { type: string; channelId: string };
    persona?: string;
    role?: "subagent";
    createdAt: number;
    lastActivityAt: number;
    crons?: string[];
  };
  maxTurns: number;
  maxCostUsd: number;
  getPermissionMode(): string;
  getPrePlanMode(): string | null;
  budgetStats(): {
    turns: number;
    inputTokens: number;
    outputTokens: number;
    costUsd: number;
  };
  transcript: {
    readCommitted(): Promise<unknown[]>;
  };
}

interface FakeAgent {
  config: { botId: string; workspaceRoot: string };
  auditLog: AuditLog;
  listSessions(): FakeSession[];
  getSession(sessionKey: string): FakeSession | undefined;
  sessionKeyIndex(): Map<string, string>;
  backgroundTasks: {
    list(filter?: {
      status?: string;
      sessionKey?: string;
      limit?: number;
    }): Promise<{
      tasks: Array<Record<string, unknown>>;
      nextCursor?: string;
    }>;
  };
  crons: {
    list(filter?: { includeInternal?: boolean; enabled?: boolean }): Array<
      Record<string, unknown>
    >;
  };
  artifacts: {
    list(filter?: { kind?: string }): Promise<Array<Record<string, unknown>>>;
  };
  tools: {
    list(): Array<{ name: string; permission: PermissionClass; kind?: string }>;
    skillReport(): {
      loaded: Array<{ name: string; path: string }>;
      issues: Array<{ path: string; message: string }>;
      runtimeHooks: Array<{ name: string; point: string }>;
    };
  };
  hooks: { list(): [] };
  getActiveTurn(): undefined;
}

function makeFakeAgent(workspaceRoot: string): FakeAgent {
  const session: FakeSession = {
    meta: {
      sessionKey: "agent:main:app:web:default",
      botId: "bot-test",
      channel: { type: "app", channelId: "web" },
      persona: "main",
      createdAt: 1_700_000_000_000,
      lastActivityAt: 1_700_000_060_000,
      crons: ["cron-session"],
    },
    maxTurns: 1000,
    maxCostUsd: 0,
    getPermissionMode: () => "auto",
    getPrePlanMode: () => null,
    budgetStats: () => ({
      turns: 3,
      inputTokens: 120,
      outputTokens: 80,
      costUsd: 0.01,
    }),
    transcript: {
      readCommitted: async () => [
        {
          kind: "user_message",
          ts: 1_700_000_000_001,
          turnId: "turn-1",
          text: "hello",
        },
        {
          kind: "assistant_text",
          ts: 1_700_000_000_002,
          turnId: "turn-1",
          text: "hi",
        },
        {
          kind: "turn_committed",
          ts: 1_700_000_000_003,
          turnId: "turn-1",
          inputTokens: 10,
          outputTokens: 5,
        },
      ],
    },
  };
  return {
    config: { botId: "bot-test", workspaceRoot },
    auditLog: new AuditLog(workspaceRoot, "bot-test"),
    listSessions: () => [session],
    getSession: (sessionKey) =>
      sessionKey === session.meta.sessionKey ? session : undefined,
    sessionKeyIndex: () => new Map(),
    backgroundTasks: {
      list: async (filter = {}) => ({
        tasks: [
          {
            taskId: "task-1",
            sessionKey: "agent:main:app:web:default",
            status: "running",
            persona: "researcher",
            prompt: "collect market data and return the full result",
            startedAt: 1_700_000_030_000,
          },
        ].filter((task) =>
          filter.status ? task.status === filter.status : true,
        ),
      }),
    },
    crons: {
      list: () => [
        {
          cronId: "cron-session",
          expression: "*/5 * * * *",
          enabled: true,
          durable: false,
          internal: false,
          nextFireAt: 1_700_000_300_000,
          deliveryChannel: { type: "app", channelId: "web" },
          prompt: "check queue",
        },
        {
          cronId: "internal:hipocampus",
          expression: "0 * * * *",
          enabled: true,
          durable: true,
          internal: true,
          nextFireAt: 1_700_000_400_000,
          deliveryChannel: { type: "internal", channelId: "" },
          prompt: "",
        },
      ],
    },
    artifacts: {
      list: async () => [
        {
          artifactId: "artifact-1",
          kind: "doc",
          title: "Plan",
          slug: "plan",
          path: "artifacts/artifact-1/plan.md",
          sizeBytes: 42,
          createdAt: 1_700_000_010_000,
          updatedAt: 1_700_000_020_000,
        },
      ],
    },
    tools: {
      list: () => [
        { name: "FileRead", permission: "read" },
        { name: "DocumentWrite", permission: "write" },
        { name: "plan", permission: "meta", kind: "skill" },
      ],
      skillReport: () => ({
        loaded: [{ name: "plan", path: "skills/superpowers/plan" }],
        issues: [],
        runtimeHooks: [{ name: "skill:plan", point: "beforeTurnStart" }],
      }),
    },
    hooks: { list: () => [] },
    getActiveTurn: () => undefined,
  };
}

function requestJson(
  url: string,
  token?: string,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      url,
      {
        method: "GET",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          let body: unknown = text;
          try {
            body = JSON.parse(text);
          } catch {
            /* keep text */
          }
          resolve({ status: res.statusCode ?? 0, body });
        });
      },
    );
    req.on("error", reject);
    req.end();
  });
}

describe("HttpServer /v1/app runtime routes", () => {
  let tmp: string;
  let server: HttpServer;
  let port: number;

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "clawy-app-runtime-"));
    const agent = makeFakeAgent(tmp) as unknown as ConstructorParameters<
      typeof HttpServer
    >[0]["agent"];
    server = new HttpServer({ port: 0, agent, bearerToken: "local-token" });
    await server.start();
    const anyServer = server as unknown as { server: http.Server };
    const addr = anyServer.server.address();
    port = typeof addr === "object" && addr ? addr.port : 0;
  });

  afterEach(async () => {
    await server.stop();
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("requires bearer auth for app runtime inspection", async () => {
    const res = await requestJson(`http://127.0.0.1:${port}/v1/app/runtime`);

    expect(res.status).toBe(401);
    expect(res.body).toEqual({ error: "unauthorized" });
  });

  it("returns an aggregate read-only runtime snapshot", async () => {
    const res = await requestJson(
      `http://127.0.0.1:${port}/v1/app/runtime`,
      "local-token",
    );

    expect(res.status).toBe(200);
    const body = res.body as {
      ok: boolean;
      botId: string;
      sessions: { count: number; items: Array<{ sessionKey: string }> };
      tasks: { count: number; items: Array<{ taskId: string; promptPreview: string }> };
      crons: { count: number; internalCount: number };
      artifacts: { count: number; items: Array<{ artifactId: string; title: string }> };
      tools: { count: number; skillCount: number };
      skills: { loadedCount: number; runtimeHookCount: number };
    };
    expect(body.ok).toBe(true);
    expect(body.botId).toBe("bot-test");
    expect(body.sessions.count).toBe(1);
    expect(body.sessions.items[0]?.sessionKey).toBe("agent:main:app:web:default");
    expect(body.tasks.items[0]?.promptPreview).toBe(
      "collect market data and return the full result",
    );
    expect(body.crons.count).toBe(2);
    expect(body.crons.internalCount).toBe(1);
    expect(body.artifacts.items[0]?.title).toBe("Plan");
    expect(body.tools.skillCount).toBe(1);
    expect(body.skills.loadedCount).toBe(1);
    expect(body.skills.runtimeHookCount).toBe(1);
  });

  it("returns live session snapshots", async () => {
    const res = await requestJson(
      `http://127.0.0.1:${port}/v1/app/sessions`,
      "local-token",
    );

    expect(res.status).toBe(200);
    const body = res.body as {
      sessions: Array<{
        sessionKey: string;
        permissionMode: string;
        budget: { turns: number; inputTokens: number };
        maxTurns: number;
      }>;
    };
    expect(body.sessions).toHaveLength(1);
    expect(body.sessions[0]?.permissionMode).toBe("auto");
    expect(body.sessions[0]?.budget.turns).toBe(3);
    expect(body.sessions[0]?.maxTurns).toBe(1000);
  });

  it("returns a bounded committed transcript snapshot", async () => {
    const res = await requestJson(
      `http://127.0.0.1:${port}/v1/app/transcript?sessionKey=${encodeURIComponent(
        "agent:main:app:web:default",
      )}&limit=2`,
      "local-token",
    );

    expect(res.status).toBe(200);
    const body = res.body as {
      sessionKey: string;
      entries: Array<{ kind: string; turnId?: string; text?: string }>;
    };
    expect(body.sessionKey).toBe("agent:main:app:web:default");
    expect(body.entries.map((entry) => entry.kind)).toEqual([
      "assistant_text",
      "turn_committed",
    ]);
  });

  it("returns skill load state separately for the inspector", async () => {
    const res = await requestJson(
      `http://127.0.0.1:${port}/v1/app/skills`,
      "local-token",
    );

    expect(res.status).toBe(200);
    const body = res.body as {
      loaded: Array<{ name: string }>;
      issues: unknown[];
      runtimeHooks: Array<{ name: string }>;
    };
    expect(body.loaded.map((skill) => skill.name)).toEqual(["plan"]);
    expect(body.issues).toEqual([]);
    expect(body.runtimeHooks[0]?.name).toBe("skill:plan");
  });
});
