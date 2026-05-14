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
  registerSessionCron(cronId: string): void;
}

interface FakeAgent {
  config: { botId: string; userId: string; workspaceRoot: string };
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
    get(taskId: string): Promise<Record<string, unknown> | null>;
    stop(taskId: string, reason?: string): Promise<boolean>;
  };
  crons: {
    list(filter?: { includeInternal?: boolean; enabled?: boolean }): Array<
      Record<string, unknown>
    >;
    get(cronId: string): Record<string, unknown> | null;
    create(input: Record<string, unknown>): Promise<Record<string, unknown>>;
    update(cronId: string, patch: Record<string, unknown>): Promise<Record<string, unknown>>;
    delete(cronId: string): Promise<boolean>;
  };
  artifacts: {
    list(filter?: { kind?: string }): Promise<Array<Record<string, unknown>>>;
    getMeta(artifactId: string): Promise<Record<string, unknown>>;
    readL0(artifactId: string): Promise<string>;
    readL1(artifactId: string): Promise<string>;
    readL2(artifactId: string): Promise<string>;
  };
  tools: {
    list(): Array<{ name: string; permission: PermissionClass; kind?: string }>;
    skillReport(): {
      loaded: Array<{ name: string; path: string }>;
      issues: Array<{ path: string; message: string }>;
      runtimeHooks: Array<{ name: string; point: string }>;
    };
  };
  hipocampus: {
    status(): Promise<Record<string, unknown>>;
    compact(force?: boolean): Promise<Record<string, unknown>>;
    getQmdManager(): { reindex(): Promise<void> };
    recall(
      query: string,
      opts?: { limit?: number; collection?: string; minScore?: number },
    ): Promise<{
      root: { path: string; content: string; bytes: number } | null;
      results: Array<{ path: string; content: string; score: number; context?: string }>;
    }>;
  };
  reloadWorkspaceSkills(): Promise<{
    loaded: Array<{ name: string; path: string }>;
    issues: Array<{ path: string; message: string }>;
    runtimeHooks: Array<{ name: string; point: string }>;
  }>;
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
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          text: "write and deliver the report",
        },
        {
          kind: "tool_call",
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          toolUseId: "tool-doc",
          name: "DocumentWrite",
          input: { filename: "report.md", title: "Report" },
        },
        {
          kind: "tool_result",
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          toolUseId: "tool-doc",
          status: "ok",
          output: JSON.stringify({
            artifactId: "artifact-output-1",
            filename: "report.md",
            workspacePath: "outputs/report.md",
          }),
        },
        {
          kind: "tool_call",
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          toolUseId: "tool-test",
          name: "TestRun",
          input: { command: "npm run lint" },
        },
        {
          kind: "tool_result",
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          toolUseId: "tool-test",
          status: "ok",
          output: JSON.stringify({ exitCode: 0 }),
        },
        {
          kind: "tool_call",
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          toolUseId: "tool-deliver",
          name: "FileDeliver",
          input: { artifactId: "artifact-output-1", target: "chat" },
        },
        {
          kind: "tool_result",
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          toolUseId: "tool-deliver",
          status: "ok",
          output: JSON.stringify({
            deliveries: [
              {
                target: "chat",
                status: "sent",
                externalId: "att-1",
                marker: "[attachment:att-1:report.md]",
                attemptCount: 1,
              },
            ],
          }),
        },
        {
          kind: "assistant_text",
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          text: "Report delivered. [attachment:att-1:report.md]",
        },
        {
          kind: "turn_committed",
          ts: 1_700_000_000_000,
          turnId: "turn-0",
          inputTokens: 20,
          outputTokens: 10,
        },
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
    registerSessionCron: (cronId) => {
      const list = session.meta.crons ?? (session.meta.crons = []);
      list.push(cronId);
    },
  };
  const tasks = [
    {
      taskId: "task-1",
      sessionKey: "agent:main:app:web:default",
      parentTurnId: "turn-parent",
      status: "running",
      persona: "researcher",
      prompt: "collect market data and return the full result",
      resultText: "market data result",
      startedAt: 1_700_000_030_000,
    },
  ];
  const crons = [
    {
      cronId: "cron-session",
      botId: "bot-test",
      userId: "user-test",
      expression: "*/5 * * * *",
      enabled: true,
      durable: false,
      internal: false,
      createdAt: 1_700_000_000_000,
      nextFireAt: 1_700_000_300_000,
      consecutiveFailures: 0,
      deliveryChannel: { type: "app", channelId: "web" },
      prompt:
        "check queue and write a long non-truncated operational summary with the next steps",
    },
    {
      cronId: "internal:hipocampus",
      botId: "",
      userId: "",
      expression: "0 * * * *",
      enabled: true,
      durable: true,
      internal: true,
      createdAt: 1_700_000_000_000,
      nextFireAt: 1_700_000_400_000,
      consecutiveFailures: 0,
      deliveryChannel: { type: "internal", channelId: "" },
      prompt: "",
    },
  ];
  return {
    config: { botId: "bot-test", userId: "user-test", workspaceRoot },
    auditLog: new AuditLog(workspaceRoot, "bot-test"),
    listSessions: () => [session],
    getSession: (sessionKey) =>
      sessionKey === session.meta.sessionKey ? session : undefined,
    sessionKeyIndex: () => new Map(),
    backgroundTasks: {
      list: async (filter = {}) => ({
        tasks: tasks.filter((task) =>
          filter.status ? task.status === filter.status : true,
        ),
      }),
      get: async (taskId) => tasks.find((task) => task.taskId === taskId) ?? null,
      stop: async (taskId, reason) => {
        const task = tasks.find((item) => item.taskId === taskId);
        if (!task || task.status !== "running") return false;
        task.status = "aborted";
        task.finishedAt = 1_700_000_040_000;
        if (reason) task.error = `stopped: ${reason}`;
        return true;
      },
    },
    crons: {
      list: (filter = {}) =>
        crons
          .filter((cron) => (filter.includeInternal ? true : cron.internal !== true))
          .filter((cron) =>
            filter.enabled === undefined ? true : cron.enabled === filter.enabled,
          ),
      get: (cronId) => crons.find((cron) => cron.cronId === cronId) ?? null,
      create: async (input) => {
        const cron = {
          cronId: "cron-created",
          botId: String(input.botId ?? ""),
          userId: String(input.userId ?? ""),
          expression: String(input.expression ?? ""),
          prompt: String(input.prompt ?? ""),
          description:
            typeof input.description === "string" ? input.description : undefined,
          deliveryChannel: input.deliveryChannel as { type: string; channelId: string },
          enabled: true,
          durable: input.durable === true,
          internal: false,
          createdAt: 1_700_000_050_000,
          nextFireAt: 1_700_000_060_000,
          consecutiveFailures: 0,
          sessionKey:
            typeof input.sessionKey === "string" ? input.sessionKey : undefined,
          mode: typeof input.mode === "string" ? input.mode : undefined,
          scriptPath:
            typeof input.scriptPath === "string" ? input.scriptPath : undefined,
          timeoutMs:
            typeof input.timeoutMs === "number" ? input.timeoutMs : undefined,
          quietOnEmptyStdout:
            typeof input.quietOnEmptyStdout === "boolean"
              ? input.quietOnEmptyStdout
              : undefined,
          deliveryPolicy:
            typeof input.deliveryPolicy === "string" ? input.deliveryPolicy : undefined,
        };
        crons.push(cron);
        return cron;
      },
      update: async (cronId, patch) => {
        const cron = crons.find((item) => item.cronId === cronId);
        if (!cron) throw new Error(`cron not found: ${cronId}`);
        Object.assign(cron, patch);
        return cron;
      },
      delete: async (cronId) => {
        const index = crons.findIndex((cron) => cron.cronId === cronId);
        if (index < 0) return false;
        if (crons[index]?.internal) throw new Error("internal crons cannot be deleted");
        crons.splice(index, 1);
        return true;
      },
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
      getMeta: async (artifactId) => {
        if (artifactId !== "artifact-1") throw new Error(`artifact not found: ${artifactId}`);
        return {
          artifactId: "artifact-1",
          kind: "doc",
          title: "Plan",
          slug: "plan",
          path: "artifacts/artifact-1/plan.md",
          sizeBytes: 42,
          createdAt: 1_700_000_010_000,
          updatedAt: 1_700_000_020_000,
        };
      },
      readL0: async (artifactId) => {
        if (artifactId !== "artifact-1") throw new Error(`artifact not found: ${artifactId}`);
        return "# Plan\nFull artifact body\n";
      },
      readL1: async (artifactId) => {
        if (artifactId !== "artifact-1") throw new Error(`artifact not found: ${artifactId}`);
        return "Artifact overview";
      },
      readL2: async (artifactId) => {
        if (artifactId !== "artifact-1") throw new Error(`artifact not found: ${artifactId}`);
        return "---\ntitle: Plan\n---";
      },
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
    hipocampus: {
      status: async () => ({
        qmdReady: true,
        vectorEnabled: false,
        compactionConfigured: true,
        cooldownHours: 24,
        rootMaxTokens: 12_000,
        lastCompactionRun: null,
        rootMemory: {
          path: "memory/ROOT.md",
          bytes: 20,
          loaded: true,
        },
      }),
      compact: async (force = false) => ({
        skipped: false,
        compacted: true,
        force,
        stats: { daily: ["memory/daily/2026-05-07.md"], weekly: [], monthly: [] },
      }),
      getQmdManager: () => ({
        reindex: async () => {
          await fs.writeFile(path.join(workspaceRoot, "memory", ".reindexed"), "1", "utf8");
        },
      }),
      recall: async (query) => ({
        root: {
          path: "memory/ROOT.md",
          content: `Root memory for ${query}`,
          bytes: 20,
        },
        results: [
          {
            path: "memory/daily/2026-05-07.md",
            content: "Alpha rollout note",
            score: 0.91,
            context: "daily",
          },
        ],
      }),
    },
    reloadWorkspaceSkills: async () => ({
      loaded: [{ name: "plan", path: "skills/superpowers/plan" }],
      issues: [],
      runtimeHooks: [{ name: "skill:plan", point: "beforeTurnStart" }],
    }),
    hooks: { list: () => [] },
    getActiveTurn: () => undefined,
  };
}

function requestJson(
  url: string,
  token?: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const bodyText = opts.body !== undefined ? JSON.stringify(opts.body) : undefined;
    const req = http.request(
      url,
      {
        method: opts.method ?? "GET",
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(bodyText !== undefined
            ? {
                "Content-Type": "application/json",
                "Content-Length": Buffer.byteLength(bodyText),
              }
            : {}),
        },
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
    if (bodyText !== undefined) req.write(bodyText);
    req.end();
  });
}

function requestRaw(
  url: string,
  token?: string,
): Promise<{ status: number; headers: http.IncomingHttpHeaders; body: string }> {
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
          resolve({
            status: res.statusCode ?? 0,
            headers: res.headers,
            body: Buffer.concat(chunks).toString("utf8"),
          });
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
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "magi-app-runtime-"));
    await fs.mkdir(path.join(tmp, "src"), { recursive: true });
    await fs.writeFile(path.join(tmp, "README.md"), "# Workspace\n", "utf8");
    await fs.writeFile(path.join(tmp, "src", "index.ts"), "export {};\n", "utf8");
    await fs.mkdir(path.join(tmp, "memory", "daily"), { recursive: true });
    await fs.writeFile(path.join(tmp, "memory", "ROOT.md"), "# Root\n", "utf8");
    await fs.writeFile(
      path.join(tmp, "memory", "daily", "2026-05-07.md"),
      "# Daily\nAlpha rollout note\n",
      "utf8",
    );
    await fs.mkdir(path.join(tmp, "knowledge", "reports"), { recursive: true });
    await fs.writeFile(
      path.join(tmp, "knowledge", "reports", "runtime-proof.md"),
      "# Runtime Proof\nVerification evidence and delivery state live in the runtime.\n",
      "utf8",
    );
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
      crons: {
        count: number;
        internalCount: number;
        items: Array<{ prompt: string; promptPreview: string }>;
      };
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
    expect(body.crons.items[0]?.prompt).toContain(
      "long non-truncated operational summary",
    );
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

  it("projects turn evidence and delivery state for runtime proof UI", async () => {
    const res = await requestJson(
      `http://127.0.0.1:${port}/v1/app/evidence?sessionKey=${encodeURIComponent(
        "agent:main:app:web:default",
      )}`,
      "local-token",
    );

    expect(res.status).toBe(200);
    const body = res.body as {
      turns: Array<{
        turnId: string;
        classification: { work: boolean; verification: boolean };
        tools: Array<{ name: string; status?: string }>;
        verification: Array<{ tool: string; command?: string }>;
        deliveries: Array<{ target: string; status: string; marker?: string }>;
      }>;
    };
    const turn = body.turns.find((item) => item.turnId === "turn-0");
    expect(turn).toMatchObject({
      classification: { work: true, verification: true },
      tools: [
        { name: "DocumentWrite", status: "ok" },
        { name: "TestRun", status: "ok" },
        { name: "FileDeliver", status: "ok" },
      ],
      verification: [{ tool: "TestRun", command: "npm run lint" }],
      deliveries: [
        {
          target: "chat",
          status: "sent",
          marker: "[attachment:att-1:report.md]",
        },
      ],
    });
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

  it("lists workspace files and reads bounded file content", async () => {
    const list = await requestJson(
      `http://127.0.0.1:${port}/v1/app/workspace?path=.`,
      "local-token",
    );

    expect(list.status).toBe(200);
    const listBody = list.body as {
      path: string;
      entries: Array<{ name: string; type: string; path: string }>;
    };
    expect(listBody.path).toBe(".");
    expect(listBody.entries).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: "README.md", type: "file", path: "README.md" }),
        expect.objectContaining({ name: "src", type: "directory", path: "src" }),
      ]),
    );

    const file = await requestJson(
      `http://127.0.0.1:${port}/v1/app/workspace/file?path=README.md`,
      "local-token",
    );

    expect(file.status).toBe(200);
    expect(file.body).toEqual(
      expect.objectContaining({
        ok: true,
        path: "README.md",
        content: "# Workspace\n",
        truncated: false,
      }),
    );
  });

  it("writes editable workspace prompt and memory files without leaving the workspace", async () => {
    const promptWrite = await requestJson(
      `http://127.0.0.1:${port}/v1/app/workspace/file`,
      "local-token",
      {
        method: "PUT",
        body: {
          path: "SOUL.md",
          content: "# Soul\nRun as a local work agent.\n",
        },
      },
    );

    expect(promptWrite.status).toBe(200);
    expect(promptWrite.body).toEqual(
      expect.objectContaining({
        ok: true,
        path: "SOUL.md",
        sizeBytes: 34,
      }),
    );
    await expect(fs.readFile(path.join(tmp, "SOUL.md"), "utf8")).resolves.toBe(
      "# Soul\nRun as a local work agent.\n",
    );

    const memoryWrite = await requestJson(
      `http://127.0.0.1:${port}/v1/app/workspace/file`,
      "local-token",
      {
        method: "PUT",
        body: {
          path: "memory/ROOT.md",
          content: "# Root\nEdited from the app.\n",
        },
      },
    );

    expect(memoryWrite.status).toBe(200);
    await expect(fs.readFile(path.join(tmp, "memory", "ROOT.md"), "utf8")).resolves.toBe(
      "# Root\nEdited from the app.\n",
    );

    const escape = await requestJson(
      `http://127.0.0.1:${port}/v1/app/workspace/file`,
      "local-token",
      {
        method: "PUT",
        body: {
          path: "../outside.md",
          content: "no",
        },
      },
    );

    expect(escape.status).toBe(400);
    expect(escape.body).toEqual({ error: "invalid_path" });
  });

  it("downloads workspace files for local app delivery", async () => {
    const download = await requestRaw(
      `http://127.0.0.1:${port}/v1/app/workspace/download?path=README.md`,
      "local-token",
    );

    expect(download.status).toBe(200);
    expect(download.headers["content-disposition"]).toContain("README.md");
    expect(download.body).toBe("# Workspace\n");
  });

  it("rejects workspace path traversal", async () => {
    const res = await requestJson(
      `http://127.0.0.1:${port}/v1/app/workspace?path=..%2F..`,
      "local-token",
    );

    expect(res.status).toBe(400);
    expect(res.body).toEqual({ error: "invalid_path" });
  });

  it("lists and searches Hipocampus memory files", async () => {
    const list = await requestJson(
      `http://127.0.0.1:${port}/v1/app/memory`,
      "local-token",
    );

    expect(list.status).toBe(200);
    const listBody = list.body as {
      status: { qmdReady: boolean };
      files: Array<{ path: string; sizeBytes: number }>;
    };
    expect(listBody.status.qmdReady).toBe(true);
    expect(listBody.files.map((file) => file.path)).toEqual(
      expect.arrayContaining(["memory/ROOT.md", "memory/daily/2026-05-07.md"]),
    );

    const search = await requestJson(
      `http://127.0.0.1:${port}/v1/app/memory/search?q=alpha&limit=3`,
      "local-token",
    );

    expect(search.status).toBe(200);
    expect(search.body).toEqual(
      expect.objectContaining({
        ok: true,
        query: "alpha",
        root: expect.objectContaining({ path: "memory/ROOT.md" }),
        results: [
          expect.objectContaining({
            path: "memory/daily/2026-05-07.md",
            score: 0.91,
          }),
        ],
      }),
    );
  });

  it("runs memory compaction and qmd reindex from the app surface", async () => {
    const compact = await requestJson(
      `http://127.0.0.1:${port}/v1/app/memory/compact`,
      "local-token",
      { method: "POST", body: { force: true } },
    );

    expect(compact.status).toBe(200);
    expect(compact.body).toEqual(
      expect.objectContaining({
        ok: true,
        result: expect.objectContaining({ compacted: true, force: true }),
      }),
    );

    const reindex = await requestJson(
      `http://127.0.0.1:${port}/v1/app/memory/reindex`,
      "local-token",
      { method: "POST" },
    );

    expect(reindex.status).toBe(200);
    expect(reindex.body).toEqual({ ok: true });
    await expect(fs.readFile(path.join(tmp, "memory", ".reindexed"), "utf8")).resolves.toBe("1");
  });

  it("deletes selected Hipocampus memory files without allowing path escapes", async () => {
    const deleted = await requestJson(
      `http://127.0.0.1:${port}/v1/app/memory/files`,
      "local-token",
      {
        method: "DELETE",
        body: { paths: ["memory/daily/2026-05-07.md"] },
      },
    );

    expect(deleted.status).toBe(200);
    expect(deleted.body).toEqual({
      ok: true,
      deleted: ["memory/daily/2026-05-07.md"],
    });
    await expect(
      fs.stat(path.join(tmp, "memory", "daily", "2026-05-07.md")),
    ).rejects.toMatchObject({ code: "ENOENT" });
    await expect(fs.readFile(path.join(tmp, "memory", ".reindexed"), "utf8")).resolves.toBe("1");

    const escape = await requestJson(
      `http://127.0.0.1:${port}/v1/app/memory/files`,
      "local-token",
      {
        method: "DELETE",
        body: { paths: ["../outside.md"] },
      },
    );

    expect(escape.status).toBe(400);
    expect(escape.body).toEqual({ error: "invalid_path" });
  });

  it("lists, searches, reads, and writes local workspace knowledge files", async () => {
    const list = await requestJson(
      `http://127.0.0.1:${port}/v1/app/knowledge`,
      "local-token",
    );

    expect(list.status).toBe(200);
    expect(list.body).toEqual(
      expect.objectContaining({
        ok: true,
        collections: [
          expect.objectContaining({
            name: "reports",
            documentCount: 1,
          }),
        ],
      }),
    );

    const search = await requestJson(
      `http://127.0.0.1:${port}/v1/app/knowledge/search?q=${encodeURIComponent("delivery evidence")}&collection=reports`,
      "local-token",
    );
    expect(search.status).toBe(200);
    expect(search.body).toEqual(
      expect.objectContaining({
        ok: true,
        results: [
          expect.objectContaining({
            path: "knowledge/reports/runtime-proof.md",
          }),
        ],
      }),
    );

    const put = await requestJson(
      `http://127.0.0.1:${port}/v1/app/knowledge/file`,
      "local-token",
      {
        method: "PUT",
        body: {
          path: "notes/local-kb.md",
          content: "# Local KB\nOpen-source Magi stores KB files in the workspace.",
        },
      },
    );
    expect(put.status).toBe(200);
    expect(put.body).toEqual(
      expect.objectContaining({
        ok: true,
        path: "knowledge/notes/local-kb.md",
      }),
    );

    const file = await requestJson(
      `http://127.0.0.1:${port}/v1/app/knowledge/file?path=${encodeURIComponent("knowledge/notes/local-kb.md")}`,
      "local-token",
    );
    expect(file.status).toBe(200);
    expect(file.body).toEqual(
      expect.objectContaining({
        ok: true,
        path: "knowledge/notes/local-kb.md",
        content: "# Local KB\nOpen-source Magi stores KB files in the workspace.",
      }),
    );
  });

  it("opens and downloads artifacts by id", async () => {
    const content = await requestJson(
      `http://127.0.0.1:${port}/v1/app/artifacts/artifact-1/content?tier=l0`,
      "local-token",
    );

    expect(content.status).toBe(200);
    expect(content.body).toEqual(
      expect.objectContaining({
        ok: true,
        artifact: expect.objectContaining({ artifactId: "artifact-1", title: "Plan" }),
        tier: "l0",
        content: "# Plan\nFull artifact body\n",
      }),
    );

    const download = await requestRaw(
      `http://127.0.0.1:${port}/v1/app/artifacts/artifact-1/download`,
      "local-token",
    );

    expect(download.status).toBe(200);
    expect(download.headers["content-disposition"]).toContain("plan.md");
    expect(download.body).toBe("# Plan\nFull artifact body\n");
  });

  it("returns and stops individual background tasks", async () => {
    const output = await requestJson(
      `http://127.0.0.1:${port}/v1/app/tasks/task-1/output`,
      "local-token",
    );

    expect(output.status).toBe(200);
    expect(output.body).toEqual(
      expect.objectContaining({
        ok: true,
        taskId: "task-1",
        status: "running",
        resultText: "market data result",
      }),
    );

    const stopped = await requestJson(
      `http://127.0.0.1:${port}/v1/app/tasks/task-1/stop`,
      "local-token",
      { method: "POST", body: { reason: "user cancelled" } },
    );

    expect(stopped.status).toBe(200);
    expect(stopped.body).toEqual(
      expect.objectContaining({
        ok: true,
        taskId: "task-1",
        stopped: true,
        task: expect.objectContaining({ status: "aborted" }),
      }),
    );
  });

  it("creates, updates, and deletes app crons", async () => {
    const created = await requestJson(
      `http://127.0.0.1:${port}/v1/app/crons`,
      "local-token",
      {
        method: "POST",
        body: {
          expression: "@daily",
          prompt: "write the daily note",
          description: "Daily note",
          sessionKey: "agent:main:app:web:default",
        },
      },
    );

    expect(created.status).toBe(200);
    expect(created.body).toEqual(
      expect.objectContaining({
        ok: true,
        cron: expect.objectContaining({
          cronId: "cron-created",
          deliveryChannel: { type: "app", channelId: "web" },
          promptPreview: "write the daily note",
        }),
      }),
    );
    const sessions = await requestJson(
      `http://127.0.0.1:${port}/v1/app/sessions`,
      "local-token",
    );
    expect(
      (sessions.body as { sessions: Array<{ crons: string[] }> }).sessions[0]?.crons,
    ).toContain("cron-created");

    const scriptCreated = await requestJson(
      `http://127.0.0.1:${port}/v1/app/crons`,
      "local-token",
      {
        method: "POST",
        body: {
          expression: "@hourly",
          prompt: "run local health script",
          mode: "script",
          scriptPath: "jobs/health.sh",
          timeoutMs: 120_000,
          quietOnEmptyStdout: false,
          deliveryPolicy: "always",
          sessionKey: "agent:main:app:web:default",
        },
      },
    );

    expect(scriptCreated.status).toBe(200);
    expect(scriptCreated.body).toEqual(
      expect.objectContaining({
        ok: true,
        cron: expect.objectContaining({
          mode: "script",
          scriptPath: "jobs/health.sh",
          timeoutMs: 120_000,
          quietOnEmptyStdout: false,
          deliveryPolicy: "always",
        }),
      }),
    );

    const updated = await requestJson(
      `http://127.0.0.1:${port}/v1/app/crons/cron-created`,
      "local-token",
      {
        method: "PUT",
        body: { enabled: false, description: "Paused daily note" },
      },
    );

    expect(updated.status).toBe(200);
    expect(updated.body).toEqual(
      expect.objectContaining({
        ok: true,
        cron: expect.objectContaining({
          cronId: "cron-created",
          enabled: false,
          description: "Paused daily note",
        }),
      }),
    );

    const deleted = await requestJson(
      `http://127.0.0.1:${port}/v1/app/crons/cron-created`,
      "local-token",
      { method: "DELETE" },
    );

    expect(deleted.status).toBe(200);
    expect(deleted.body).toEqual({ ok: true, cronId: "cron-created", deleted: true });
  });

  it("reloads workspace skills from the app auth surface", async () => {
    const res = await requestJson(
      `http://127.0.0.1:${port}/v1/app/skills/reload`,
      "local-token",
      { method: "POST" },
    );

    expect(res.status).toBe(200);
    expect(res.body).toEqual(
      expect.objectContaining({
        ok: true,
        loaded: [{ name: "plan", path: "skills/superpowers/plan" }],
        issues: [],
        runtimeHooks: [{ name: "skill:plan", point: "beforeTurnStart" }],
      }),
    );
  });
});
