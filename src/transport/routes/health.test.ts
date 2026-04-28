/**
 * /health + /healthz tests. Validates wire-format parity after the R5
 * route split.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { HttpServer } from "../HttpServer.js";
import { AuditLog } from "../../storage/AuditLog.js";
import { buildHealthPayload, readBuildInfo } from "./health.js";

interface FakeAgent {
  config: { botId: string; workspaceRoot: string };
  auditLog: AuditLog;
  listSessions(): Array<{ meta: { sessionKey: string } }>;
  sessionKeyIndex(): Map<string, string>;
  tools: {
    list(): Array<{ name: string; permission: string }>;
    skillReport(): {
      loaded: string[];
      issues: string[];
      runtimeHooks: unknown[];
    } | null;
  };
  hooks: {
    list(): Array<{
      name: string;
      point: string;
      priority?: number;
      blocking?: boolean;
    }>;
  };
  hipocampus?: {
    status(): Promise<{
      qmdReady: boolean;
      vectorEnabled: boolean;
      compactionConfigured: boolean;
      cooldownHours: number | null;
      rootMaxTokens: number | null;
      lastCompactionRun: string | null;
      rootMemory: { path: string | null; bytes: number; loaded: boolean };
    }>;
  };
  policy: {
    status(): Promise<{
      executableDirectives: string[];
      userDirectives: string[];
      advisoryDirectives: string[];
      warnings: string[];
    }>;
  };
  debugWorkflow: {
    status(): {
      enabled: boolean;
      activeTurns: number;
      latest: {
        sessionKey: string;
        turnId: string;
        classified: boolean;
        investigated: boolean;
        hypothesized: boolean;
        patched: boolean;
        verified: boolean;
        warnings: string[];
      } | null;
    };
  };
  getActiveTurn(): undefined;
}

function makeFakeAgent(workspaceRoot: string): FakeAgent {
  const botId = "bot-test";
  return {
    config: { botId, workspaceRoot },
    auditLog: new AuditLog(workspaceRoot, botId),
    listSessions: () => [],
    sessionKeyIndex: () => new Map(),
    tools: {
      list: () => [
        { name: "FileRead", permission: "read" },
        { name: "Bash", permission: "ask" },
      ],
      skillReport: () => ({
        loaded: ["plan", "coding-agent"],
        issues: [],
        runtimeHooks: [{ name: "skill:test:hook" }],
      }),
    },
    hooks: {
      list: () => [
        { name: "auditTool", point: "pre_tool", priority: 10, blocking: true },
      ],
    },
    hipocampus: {
      status: async () => ({
        qmdReady: true,
        vectorEnabled: true,
        compactionConfigured: true,
        cooldownHours: 3,
        rootMaxTokens: 3000,
        lastCompactionRun: "2026-04-25T00:00:00.000Z",
        rootMemory: {
          path: "memory/ROOT.md",
          bytes: 123,
          loaded: true,
        },
      }),
    },
    policy: {
      status: async () => ({
        executableDirectives: ["approval.explicit_consent_for_external_actions=true"],
        userDirectives: ["response.language=ko"],
        advisoryDirectives: [],
        warnings: [],
      }),
    },
    debugWorkflow: {
      status: () => ({
        enabled: true,
        activeTurns: 1,
        latest: {
          sessionKey: "session-test",
          turnId: "turn-test",
          classified: true,
          investigated: true,
          hypothesized: false,
          patched: false,
          verified: false,
          warnings: [],
        },
      }),
    },
    getActiveTurn: () => undefined,
  };
}

async function getJson(
  url: string,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method: "GET" }, (res) => {
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
    });
    req.on("error", reject);
    req.end();
  });
}

describe("HttpServer /health + /healthz", () => {
  let tmp: string;
  let helperPath: string;
  let server: HttpServer;
  let port: number;
  let oldReliableScript: string | undefined;
  let oldBuildSha: string | undefined;
  let oldImageRepo: string | undefined;
  let oldImageTag: string | undefined;
  let oldImageDigest: string | undefined;

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "core-agent-health-"));
    helperPath = path.join(tmp, "reliable-request.mjs");
    await fs.writeFile(helperPath, "process.stdout.write('{}')\n", "utf8");
    oldReliableScript = process.env.CORE_AGENT_RELIABLE_REQUEST_SCRIPT;
    oldBuildSha = process.env.CORE_AGENT_BUILD_SHA;
    oldImageRepo = process.env.CORE_AGENT_IMAGE_REPO;
    oldImageTag = process.env.CORE_AGENT_IMAGE_TAG;
    oldImageDigest = process.env.CORE_AGENT_EXPECTED_IMAGE_DIGEST;
    process.env.CORE_AGENT_RELIABLE_REQUEST_SCRIPT = helperPath;
    process.env.CORE_AGENT_BUILD_SHA = "sha-test";
    process.env.CORE_AGENT_IMAGE_REPO = "ghcr.io/test/core-agent";
    process.env.CORE_AGENT_IMAGE_TAG = "tag-test";
    process.env.CORE_AGENT_EXPECTED_IMAGE_DIGEST = "sha256:digest-test";
    const agent = makeFakeAgent(tmp) as unknown as ConstructorParameters<
      typeof HttpServer
    >[0]["agent"];
    server = new HttpServer({ port: 0, agent });
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
    setOrDeleteEnv("CORE_AGENT_IMAGE_REPO", oldImageRepo);
    setOrDeleteEnv("CORE_AGENT_IMAGE_TAG", oldImageTag);
    setOrDeleteEnv("CORE_AGENT_EXPECTED_IMAGE_DIGEST", oldImageDigest);
  });

  it("GET /health returns the lean payload", async () => {
    const r = await getJson(`http://127.0.0.1:${port}/health`);
    expect(r.status).toBe(200);
    const body = r.body as {
      ok: boolean;
      botId: string;
      runtime: string;
      version: string;
    };
    expect(body.ok).toBe(true);
    expect(body.botId).toBe("bot-test");
    expect(body.runtime).toBe("core-agent");
    expect(body.version).toBe("0.19.10");
  });

  it("GET /healthz returns tool + skill + hook counts plus policy status", async () => {
    const r = await getJson(`http://127.0.0.1:${port}/healthz`);
    expect(r.status).toBe(200);
    const body = r.body as {
      ok: boolean;
      botId: string;
      tools: Array<{ name: string; permission: string }>;
      skills: {
        loaded: number;
        issues: string[];
        runtimeHooks: number;
        executableRuntimeHooks: number;
      } | null;
      hooks: Array<{
        name: string;
        point: string;
        priority: number;
        blocking: boolean;
      }>;
      hipocampus: {
        qmdReady: boolean;
        vectorEnabled: boolean;
        compactionConfigured: boolean;
        cooldownHours: number | null;
        rootMaxTokens: number | null;
        lastCompactionRun: string | null;
        rootMemory: { path: string | null; bytes: number; loaded: boolean };
      } | null;
      policy: {
        executableDirectives: string[];
        userDirectives: string[];
        advisoryDirectives: string[];
        warnings: string[];
      } | null;
      transport_reliability: {
        helperPath: string;
        helperExists: boolean;
        helperWired: boolean;
        enabled: boolean;
        defaultBackoffSeconds: number[];
        maxAttempts: number;
      };
      debug_workflow: {
        enabled: boolean;
        activeTurns: number;
        latest: {
          sessionKey: string;
          turnId: string;
          classified: boolean;
          investigated: boolean;
          hypothesized: boolean;
          patched: boolean;
          verified: boolean;
          warnings: string[];
        } | null;
      };
      permission_arbiter: {
        enabled: true;
        bypassDeniedCount: number;
        lastDeniedReasons: string[];
      };
    };
    expect(body.ok).toBe(true);
    expect((body as { buildSha?: string }).buildSha).toBe("sha-test");
    expect((body as { imageTag?: string }).imageTag).toBe("tag-test");
    expect((body as { imageDigest?: string }).imageDigest).toBe("sha256:digest-test");
    expect((body as { features?: Record<string, boolean> }).features).toMatchObject({
      controlLedger: true,
      controlRequest: true,
      permissionArbiter: true,
      planApproval: true,
      structuredOutput: true,
      childHarness: true,
      transportReliability: true,
    });
    expect((body as { degradedReasons?: string[] }).degradedReasons).toEqual([]);
    expect(body.tools).toHaveLength(2);
    expect(body.tools[0]).toEqual({ name: "FileRead", permission: "read" });
    expect(body.skills).toEqual({
      loaded: 2,
      issues: [],
      runtimeHooks: 1,
      executableRuntimeHooks: 0,
    });
    expect(body.hooks).toHaveLength(1);
    expect(body.hooks[0]).toEqual({
      name: "auditTool",
      point: "pre_tool",
      priority: 10,
      blocking: true,
    });
    expect(body.hipocampus).toEqual({
      qmdReady: true,
      vectorEnabled: true,
      compactionConfigured: true,
      cooldownHours: 3,
      rootMaxTokens: 3000,
      lastCompactionRun: "2026-04-25T00:00:00.000Z",
      rootMemory: {
        path: "memory/ROOT.md",
        bytes: 123,
        loaded: true,
      },
    });
    expect(body.policy).toEqual({
      executableDirectives: ["approval.explicit_consent_for_external_actions=true"],
      userDirectives: ["response.language=ko"],
      advisoryDirectives: [],
      warnings: [],
    });
    expect(body.transport_reliability).toEqual({
      helperPath,
      helperExists: true,
      helperWired: true,
      enabled: true,
      defaultBackoffSeconds: [0, 10, 30],
      maxAttempts: 3,
    });
    expect(body.permission_arbiter).toMatchObject({
      enabled: true,
      bypassDeniedCount: expect.any(Number),
      lastDeniedReasons: expect.any(Array),
    });
    expect(body.debug_workflow).toEqual({
      enabled: true,
      activeTurns: 1,
      latest: {
        sessionKey: "session-test",
        turnId: "turn-test",
        classified: true,
        investigated: true,
        hypothesized: false,
        patched: false,
        verified: false,
        warnings: [],
      },
    });
  });

  it("returns 404 for unknown route", async () => {
    const r = await getJson(`http://127.0.0.1:${port}/does-not-exist`);
    expect(r.status).toBe(404);
  });
});

describe("buildHealthPayload", () => {
  it("treats empty build identity environment variables as missing", () => {
    const oldBuildSha = process.env.CORE_AGENT_BUILD_SHA;
    const oldBuiltBuildSha = process.env.CORE_AGENT_BUILT_BUILD_SHA;
    const oldBuiltImageTag = process.env.CORE_AGENT_BUILT_IMAGE_TAG;
    const oldBuiltImageDigest = process.env.CORE_AGENT_BUILT_IMAGE_DIGEST;
    const oldImageTag = process.env.CORE_AGENT_IMAGE_TAG;
    const oldImageDigest = process.env.CORE_AGENT_EXPECTED_IMAGE_DIGEST;
    const oldFallbackImageDigest = process.env.CORE_AGENT_IMAGE_DIGEST;
    process.env.CORE_AGENT_BUILD_SHA = "";
    process.env.CORE_AGENT_BUILT_BUILD_SHA = "";
    process.env.CORE_AGENT_BUILT_IMAGE_TAG = "";
    process.env.CORE_AGENT_BUILT_IMAGE_DIGEST = "";
    process.env.CORE_AGENT_IMAGE_TAG = "";
    process.env.CORE_AGENT_EXPECTED_IMAGE_DIGEST = "";
    process.env.CORE_AGENT_IMAGE_DIGEST = "";

    try {
      expect(readBuildInfo()).toMatchObject({
        buildSha: null,
        imageTag: null,
        imageDigest: null,
      });
    } finally {
      setOrDeleteEnv("CORE_AGENT_BUILD_SHA", oldBuildSha);
      setOrDeleteEnv("CORE_AGENT_BUILT_BUILD_SHA", oldBuiltBuildSha);
      setOrDeleteEnv("CORE_AGENT_BUILT_IMAGE_TAG", oldBuiltImageTag);
      setOrDeleteEnv("CORE_AGENT_BUILT_IMAGE_DIGEST", oldBuiltImageDigest);
      setOrDeleteEnv("CORE_AGENT_IMAGE_TAG", oldImageTag);
      setOrDeleteEnv("CORE_AGENT_EXPECTED_IMAGE_DIGEST", oldImageDigest);
      setOrDeleteEnv("CORE_AGENT_IMAGE_DIGEST", oldFallbackImageDigest);
    }
  });

  it("reports baked image identity separately from rollout expectation", () => {
    const oldBuildSha = process.env.CORE_AGENT_BUILD_SHA;
    const oldBuiltBuildSha = process.env.CORE_AGENT_BUILT_BUILD_SHA;
    const oldImageTag = process.env.CORE_AGENT_IMAGE_TAG;
    const oldBuiltImageTag = process.env.CORE_AGENT_BUILT_IMAGE_TAG;
    process.env.CORE_AGENT_BUILD_SHA = "expected-sha";
    process.env.CORE_AGENT_BUILT_BUILD_SHA = "baked-sha";
    process.env.CORE_AGENT_IMAGE_TAG = "expected-tag";
    process.env.CORE_AGENT_BUILT_IMAGE_TAG = "baked-tag";
    try {
      expect(readBuildInfo()).toMatchObject({
        buildSha: "baked-sha",
        imageTag: "baked-tag",
        builtImage: {
          buildSha: "baked-sha",
          imageTag: "baked-tag",
        },
        expectedImage: {
          buildSha: "expected-sha",
          imageTag: "expected-tag",
        },
      });
    } finally {
      setOrDeleteEnv("CORE_AGENT_BUILD_SHA", oldBuildSha);
      setOrDeleteEnv("CORE_AGENT_BUILT_BUILD_SHA", oldBuiltBuildSha);
      setOrDeleteEnv("CORE_AGENT_IMAGE_TAG", oldImageTag);
      setOrDeleteEnv("CORE_AGENT_BUILT_IMAGE_TAG", oldBuiltImageTag);
    }
  });

  it("marks healthz degraded when policy status is unavailable", async () => {
    const payload = await buildHealthPayload({
      botId: "bot-test",
      tools: [],
      hooks: [],
      skillReport: null,
      hipocampus: null,
      policyStatus: async () => {
        throw new Error("policy missing");
      },
      debugWorkflowStatus: () => ({ enabled: true }),
      transportStatus: () => ({
        helperPath: "/tmp/reliable-request.mjs",
        helperExists: true,
        helperWired: true,
        enabled: true,
        defaultBackoffSeconds: [0, 10, 30],
        maxAttempts: 3,
      }),
      buildInfo: {
        version: "0.19.10",
        buildSha: "abc123",
        imageRepo: "repo",
        imageTag: "tag",
        imageDigest: null,
        builtImage: {
          buildSha: "abc123",
          imageRepo: "repo",
          imageTag: "tag",
          imageDigest: null,
        },
        expectedImage: {
          buildSha: "abc123",
          imageRepo: "repo",
          imageTag: "tag",
          imageDigest: null,
        },
      },
      features: {
        controlLedger: true,
        controlRequest: true,
        permissionArbiter: true,
        planApproval: true,
        structuredOutput: true,
        childHarness: true,
        transportReliability: true,
      },
    });

    expect(payload.ok).toBe(false);
    expect(payload.degradedReasons).toContain("policy_status_unavailable");
  });

  it("reports helper existence instead of a hardcoded helperWired true", async () => {
    const payload = await buildHealthPayload({
      botId: "bot-test",
      tools: [],
      hooks: [],
      skillReport: null,
      hipocampus: null,
      policyStatus: async () => ({ enabled: true }),
      debugWorkflowStatus: () => ({ enabled: true }),
      transportStatus: () => ({
        helperPath: "/missing/reliable-request.mjs",
        helperExists: false,
        helperWired: false,
        enabled: true,
        defaultBackoffSeconds: [0, 10, 30],
        maxAttempts: 3,
      }),
      buildInfo: {
        version: "0.19.10",
        buildSha: "abc123",
        imageRepo: "repo",
        imageTag: "tag",
        imageDigest: null,
        builtImage: {
          buildSha: "abc123",
          imageRepo: "repo",
          imageTag: "tag",
          imageDigest: null,
        },
        expectedImage: {
          buildSha: "abc123",
          imageRepo: "repo",
          imageTag: "tag",
          imageDigest: null,
        },
      },
      features: {
        controlLedger: true,
        controlRequest: true,
        permissionArbiter: true,
        planApproval: true,
        structuredOutput: true,
        childHarness: true,
        transportReliability: true,
      },
    });

    expect(payload.ok).toBe(false);
    expect(payload.transport_reliability.helperExists).toBe(false);
    expect(payload.degradedReasons).toContain("transport_helper_missing");
  });
});

function setOrDeleteEnv(key: string, value: string | undefined): void {
  if (value === undefined) {
    delete process.env[key];
  } else {
    process.env[key] = value;
  }
}
