import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { parse } from "yaml";
import { HttpServer } from "../HttpServer.js";
import { AuditLog } from "../../storage/AuditLog.js";

interface FakeAgent {
  config: { botId: string; workspaceRoot: string };
  auditLog: AuditLog;
  listSessions(): [];
  sessionKeyIndex(): Map<string, string>;
  tools: { list(): []; skillReport(): null };
  hooks: { list(): [] };
  getActiveTurn(): undefined;
}

function makeFakeAgent(workspaceRoot: string): FakeAgent {
  return {
    config: { botId: "bot-test", workspaceRoot },
    auditLog: new AuditLog(workspaceRoot, "bot-test"),
    listSessions: () => [],
    sessionKeyIndex: () => new Map(),
    tools: { list: () => [], skillReport: () => null },
    hooks: { list: () => [] },
    getActiveTurn: () => undefined,
  };
}

function requestJson(
  url: string,
  opts: { method?: string; token?: string; body?: unknown } = {},
): Promise<{ status: number; body: Record<string, unknown> }> {
  return new Promise((resolve, reject) => {
    const rawBody = opts.body === undefined ? undefined : JSON.stringify(opts.body);
    const req = http.request(
      url,
      {
        method: opts.method ?? "GET",
        headers: {
          ...(opts.token ? { Authorization: `Bearer ${opts.token}` } : {}),
          ...(rawBody ? { "Content-Type": "application/json" } : {}),
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          resolve({
            status: res.statusCode ?? 0,
            body: text ? JSON.parse(text) as Record<string, unknown> : {},
          });
        });
      },
    );
    req.on("error", reject);
    if (rawBody) req.write(rawBody);
    req.end();
  });
}

describe("HttpServer /v1/app settings routes", () => {
  let tmp: string;
  let configPath: string;
  let server: HttpServer;
  let port: number;

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "magi-app-settings-"));
    configPath = path.join(tmp, "magi-agent.yaml");
    await fs.writeFile(
      configPath,
      [
        "llm:",
        "  provider: openai-compatible",
        "  model: llama3.1",
        "  baseUrl: http://127.0.0.1:11434/v1",
        "  apiKey: direct-secret",
        "server:",
        "  gatewayToken: server-secret",
        "workspace: ./workspace",
      ].join("\n"),
      "utf8",
    );
    vi.stubEnv("MAGI_AGENT_CONFIG_PATH", configPath);
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
    vi.unstubAllEnvs();
    await server.stop();
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("returns sanitized app config without raw secrets", async () => {
    const res = await requestJson(`http://127.0.0.1:${port}/v1/app/config`, {
      token: "local-token",
    });

    expect(res.status).toBe(200);
    expect(JSON.stringify(res.body)).not.toContain("direct-secret");
    expect(JSON.stringify(res.body)).not.toContain("server-secret");
    expect(res.body.config).toMatchObject({
      llm: {
        provider: "openai-compatible",
        model: "llama3.1",
        baseUrl: "http://127.0.0.1:11434/v1",
        apiKeySet: true,
      },
      server: { gatewayTokenSet: true },
    });
  });

  it("writes provider config using env var references instead of raw secrets", async () => {
    const res = await requestJson(`http://127.0.0.1:${port}/v1/app/config`, {
      method: "PUT",
      token: "local-token",
      body: {
        llm: {
          provider: "openai-compatible",
          model: "qwen2.5-coder:7b",
          baseUrl: "http://127.0.0.1:1234/v1",
          apiKeyEnvVar: "LOCAL_LLM_API_KEY",
          capabilities: {
            contextWindow: 131072,
            maxOutputTokens: 8192,
            supportsThinking: false,
            inputUsdPerMtok: 0,
            outputUsdPerMtok: 0,
          },
        },
        server: { gatewayTokenEnvVar: "MAGI_AGENT_SERVER_TOKEN" },
        workspace: "./workspace",
      },
    });

    expect(res.status).toBe(200);
    const written = await fs.readFile(configPath, "utf8");
    expect(written).toContain("apiKey: ${LOCAL_LLM_API_KEY}");
    expect(written).toContain("gatewayToken: ${MAGI_AGENT_SERVER_TOKEN}");
    expect(written).not.toContain("direct-secret");
    expect(parse(written)).toMatchObject({
      llm: {
        provider: "openai-compatible",
        model: "qwen2.5-coder:7b",
        baseUrl: "http://127.0.0.1:1234/v1",
        capabilities: { contextWindow: 131072 },
      },
    });
  });

  it("creates, lists, reads, and deletes workspace harness rules", async () => {
    const put = await requestJson(
      `http://127.0.0.1:${port}/v1/app/harness-rules/file-delivery.md`,
      {
        method: "PUT",
        token: "local-token",
        body: { content: "---\nid: file-delivery\n---\nDeliver files." },
      },
    );
    expect(put.status).toBe(200);

    const list = await requestJson(`http://127.0.0.1:${port}/v1/app/harness-rules`, {
      token: "local-token",
    });
    expect(list.body.rules).toEqual([
      { name: "file-delivery.md", sizeBytes: 40 },
    ]);

    const get = await requestJson(
      `http://127.0.0.1:${port}/v1/app/harness-rules/file-delivery.md`,
      { token: "local-token" },
    );
    expect(get.body).toMatchObject({
      name: "file-delivery.md",
      content: "---\nid: file-delivery\n---\nDeliver files.",
    });

    const blocked = await requestJson(
      `http://127.0.0.1:${port}/v1/app/harness-rules/..%2Fescape.md`,
      { method: "PUT", token: "local-token", body: { content: "bad" } },
    );
    expect(blocked.status).toBe(400);

    const del = await requestJson(
      `http://127.0.0.1:${port}/v1/app/harness-rules/file-delivery.md`,
      { method: "DELETE", token: "local-token" },
    );
    expect(del.status).toBe(200);
  });
});
