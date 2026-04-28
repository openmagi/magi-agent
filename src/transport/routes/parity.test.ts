import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { HttpServer } from "../HttpServer.js";
import { AuditLog } from "../../storage/AuditLog.js";
import { ControlEventLedger } from "../../control/ControlEventLedger.js";
import type { ControlRequestRecord } from "../../control/ControlEvents.js";

const TOKEN = "test-token";
const SESSION_KEY = "agent:main:app:general:parity-route";

class FakeAgent {
  readonly config: { botId: string; workspaceRoot: string };
  readonly auditLog: AuditLog;
  readonly controlEvents: ControlEventLedger;

  constructor(readonly workspaceRoot: string) {
    this.config = { botId: "bot-test", workspaceRoot };
    this.auditLog = new AuditLog(workspaceRoot, "bot-test");
    this.controlEvents = new ControlEventLedger({
      rootDir: workspaceRoot,
      sessionKey: SESSION_KEY,
    });
  }

  listSessions(): Array<{ meta: { sessionKey: string }; controlEvents: ControlEventLedger }> {
    return [{ meta: { sessionKey: SESSION_KEY }, controlEvents: this.controlEvents }];
  }

  sessionKeyIndex(): Map<string, string> {
    return new Map();
  }

  getSession(sessionKey: string): { meta: { sessionKey: string }; controlEvents: ControlEventLedger } | undefined {
    if (sessionKey !== SESSION_KEY) return undefined;
    return { meta: { sessionKey }, controlEvents: this.controlEvents };
  }

  tools = {
    list: () => [{ name: "Bash", permission: "ask" }],
    skillReport: () => ({
      loaded: ["runtime-hook-canary"],
      issues: [],
      runtimeHooks: [
        { name: "skill:runtime-hook-canary:beforeToolUse", action: "command" },
      ],
    }),
  };

  hooks = {
    list: () => [
      { name: "completionEvidenceGate", point: "beforeCommit", priority: 10, blocking: true },
    ],
  };

  hipocampus = { status: async () => ({ qmdReady: true }) };
  policy = { status: async () => ({ enabled: true }) };
  debugWorkflow = { status: () => ({ enabled: true, activeTurns: 0, latest: null }) };
  getActiveTurn(): undefined {
    return undefined;
  }
}

async function getJson(
  url: string,
  token = TOKEN,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      url,
      {
        method: "GET",
        headers: token ? { authorization: `Bearer ${token}` } : {},
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const txt = Buffer.concat(chunks).toString("utf8");
          let body: unknown = txt;
          try {
            body = JSON.parse(txt);
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

describe("GET /v1/parity/evidence", () => {
  let tmp: string;
  let server: HttpServer;
  let port: number;
  let oldReliableScript: string | undefined;
  let oldBuildSha: string | undefined;
  let oldBuiltBuildSha: string | undefined;
  let oldImageRepo: string | undefined;
  let oldBuiltImageRepo: string | undefined;
  let oldImageTag: string | undefined;
  let oldBuiltImageTag: string | undefined;
  let oldDigest: string | undefined;
  let oldBuiltDigest: string | undefined;

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "parity-route-"));
    const helperPath = path.join(tmp, "reliable-request.mjs");
    await fs.writeFile(helperPath, "process.stdout.write('{}')\n", "utf8");
    oldReliableScript = process.env.CORE_AGENT_RELIABLE_REQUEST_SCRIPT;
    oldBuildSha = process.env.CORE_AGENT_BUILD_SHA;
    oldBuiltBuildSha = process.env.CORE_AGENT_BUILT_BUILD_SHA;
    oldImageRepo = process.env.CORE_AGENT_IMAGE_REPO;
    oldBuiltImageRepo = process.env.CORE_AGENT_BUILT_IMAGE_REPO;
    oldImageTag = process.env.CORE_AGENT_IMAGE_TAG;
    oldBuiltImageTag = process.env.CORE_AGENT_BUILT_IMAGE_TAG;
    oldDigest = process.env.CORE_AGENT_EXPECTED_IMAGE_DIGEST;
    oldBuiltDigest = process.env.CORE_AGENT_BUILT_IMAGE_DIGEST;
    process.env.CORE_AGENT_RELIABLE_REQUEST_SCRIPT = helperPath;
    process.env.CORE_AGENT_BUILD_SHA = "sha-test";
    process.env.CORE_AGENT_BUILT_BUILD_SHA = "sha-test";
    process.env.CORE_AGENT_IMAGE_REPO = "ghcr.io/test/core-agent";
    process.env.CORE_AGENT_BUILT_IMAGE_REPO = "ghcr.io/test/core-agent";
    process.env.CORE_AGENT_IMAGE_TAG = "tag-test";
    process.env.CORE_AGENT_BUILT_IMAGE_TAG = "tag-test";
    process.env.CORE_AGENT_EXPECTED_IMAGE_DIGEST = "sha256:digest-test";
    process.env.CORE_AGENT_BUILT_IMAGE_DIGEST = "sha256:digest-test";

    const agent = new FakeAgent(tmp);
    const toolRequest: ControlRequestRecord = {
      requestId: "cr_tool",
      kind: "tool_permission",
      state: "pending",
      sessionKey: SESSION_KEY,
      source: "turn",
      prompt: "Allow Bash?",
      createdAt: 1,
      expiresAt: 999_999,
    };
    const planRequest: ControlRequestRecord = {
      requestId: "cr_plan",
      kind: "plan_approval",
      state: "pending",
      sessionKey: SESSION_KEY,
      source: "plan",
      prompt: "Approve plan?",
      createdAt: 2,
      expiresAt: 999_999,
    };
    await agent.controlEvents.append({ type: "control_request_created", request: toolRequest });
    await agent.controlEvents.append({
      type: "control_request_resolved",
      requestId: "cr_tool",
      decision: "approved",
      updatedInput: { command: "printf changed" },
    });
    await agent.controlEvents.append({ type: "control_request_created", request: planRequest });
    await agent.controlEvents.append({
      type: "control_request_resolved",
      requestId: "cr_plan",
      decision: "approved",
    });
    await agent.controlEvents.append({
      type: "plan_lifecycle",
      planId: "plan-1",
      state: "verification_pending",
      requestId: "cr_plan",
    });
    await agent.controlEvents.append({
      type: "verification",
      turnId: "turn-1",
      status: "missing",
    });
    await agent.controlEvents.append({
      type: "retry",
      turnId: "turn-1",
      reason: "structured_output_invalid",
      attempt: 1,
      maxAttempts: 2,
      visibleToUser: true,
    });
    await agent.controlEvents.append({
      type: "structured_output",
      turnId: "turn-1",
      status: "retry_exhausted",
    });
    await agent.controlEvents.append({
      type: "child_started",
      taskId: "child-1",
      parentTurnId: "turn-2",
    });
    await agent.controlEvents.append({
      type: "child_permission_decision",
      taskId: "child-1",
      decision: "deny",
    });
    await agent.controlEvents.append({
      type: "child_completed",
      taskId: "child-1",
    });
    await agent.controlEvents.append({
      type: "stop_reason",
      turnId: "turn-1",
      reason: "end_turn",
    });

    server = new HttpServer({
      port: 0,
      agent: agent as unknown as ConstructorParameters<typeof HttpServer>[0]["agent"],
      bearerToken: TOKEN,
    });
    await server.start();
    const anyServer = server as unknown as { server: http.Server };
    const addr = anyServer.server.address();
    port = typeof addr === "object" && addr ? addr.port : 0;
  });

  afterEach(async () => {
    await server.stop();
    await fs.rm(tmp, { recursive: true, force: true });
    setOrDeleteEnv("CORE_AGENT_RELIABLE_REQUEST_SCRIPT", oldReliableScript);
    setOrDeleteEnv("CORE_AGENT_BUILD_SHA", oldBuildSha);
    setOrDeleteEnv("CORE_AGENT_BUILT_BUILD_SHA", oldBuiltBuildSha);
    setOrDeleteEnv("CORE_AGENT_IMAGE_REPO", oldImageRepo);
    setOrDeleteEnv("CORE_AGENT_BUILT_IMAGE_REPO", oldBuiltImageRepo);
    setOrDeleteEnv("CORE_AGENT_IMAGE_TAG", oldImageTag);
    setOrDeleteEnv("CORE_AGENT_BUILT_IMAGE_TAG", oldBuiltImageTag);
    setOrDeleteEnv("CORE_AGENT_EXPECTED_IMAGE_DIGEST", oldDigest);
    setOrDeleteEnv("CORE_AGENT_BUILT_IMAGE_DIGEST", oldBuiltDigest);
  });

  it("requires bearer auth", async () => {
    const r = await getJson(
      `http://127.0.0.1:${port}/v1/parity/evidence?sessionKey=${encodeURIComponent(SESSION_KEY)}`,
      "",
    );
    expect(r.status).toBe(401);
  });

  it("requires an explicit session key so parity evidence cannot be mixed across sessions", async () => {
    const r = await getJson(`http://127.0.0.1:${port}/v1/parity/evidence`);
    expect(r.status).toBe(400);
    expect(r.body).toEqual({ error: "session_key_required" });
  });

  it("returns a ready deploy-train parity verdict for a covered canary session", async () => {
    const r = await getJson(
      `http://127.0.0.1:${port}/v1/parity/evidence?sessionKey=${encodeURIComponent(SESSION_KEY)}`,
    );
    expect(r.status).toBe(200);
    const body = r.body as {
      ok: boolean;
      ready: boolean;
      report: {
        runtime: { ready: boolean };
        control: { ready: boolean; eventCount: number; missing: string[] };
      };
      sessions: Array<{ sessionKey: string; eventCount: number; lastSeq: number }>;
    };
    expect(body.ok).toBe(true);
    expect(body.ready).toBe(true);
    expect(body.report.runtime.ready).toBe(true);
    expect(body.report.control.ready).toBe(true);
    expect(body.report.control.eventCount).toBe(12);
    expect(body.report.control.missing).toEqual([]);
    expect(body.sessions).toEqual([
      { sessionKey: SESSION_KEY, eventCount: 12, lastSeq: 12 },
    ]);
  });

  it("returns 404 for an unknown requested session", async () => {
    const r = await getJson(
      `http://127.0.0.1:${port}/v1/parity/evidence?sessionKey=missing`,
    );
    expect(r.status).toBe(404);
    expect(r.body).toEqual({ error: "session_not_found" });
  });
});

function setOrDeleteEnv(key: string, value: string | undefined): void {
  if (value === undefined) {
    delete process.env[key];
  } else {
    process.env[key] = value;
  }
}
