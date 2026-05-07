import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
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

function requestRaw(
  url: string,
): Promise<{ status: number; contentType: string; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method: "GET" }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
      res.on("end", () => {
        resolve({
          status: res.statusCode ?? 0,
          contentType: String(res.headers["content-type"] ?? ""),
          body: Buffer.concat(chunks).toString("utf8"),
        });
      });
    });
    req.on("error", reject);
    req.end();
  });
}

describe("HttpServer /app", () => {
  let tmp: string;
  let server: HttpServer;
  let port: number;

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "magi-app-route-"));
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

  it("serves the self-hosted app shell without requiring an API token", async () => {
    const res = await requestRaw(`http://127.0.0.1:${port}/app`);

    expect(res.status).toBe(200);
    expect(res.contentType).toContain("text/html");
    expect(res.body).toContain("Magi App");
    expect(res.body).toContain("model-override");
    expect(res.body).toContain("/app/app.js");
  });

  it("serves static app assets", async () => {
    const res = await requestRaw(`http://127.0.0.1:${port}/app/app.js`);

    expect(res.status).toBe(200);
    expect(res.contentType).toContain("text/javascript");
    expect(res.body).toContain("createSseParser");
    expect(res.body).toContain("loadRuntimeSnapshot");
    expect(res.body).toContain("modelOverride");
  });

  it("serves installable app assets", async () => {
    const manifest = await requestRaw(
      `http://127.0.0.1:${port}/app/manifest.webmanifest`,
    );
    const serviceWorker = await requestRaw(`http://127.0.0.1:${port}/app/sw.js`);

    expect(manifest.status).toBe(200);
    expect(manifest.contentType).toContain("application/manifest+json");
    expect(manifest.body).toContain("Magi App");
    expect(serviceWorker.status).toBe(200);
    expect(serviceWorker.contentType).toContain("text/javascript");
    expect(serviceWorker.body).toContain("magi-app-shell");
  });

  it("does not allow app route path traversal", async () => {
    const res = await requestRaw(
      `http://127.0.0.1:${port}/app/%2e%2e/package.json`,
    );

    expect(res.status).toBe(404);
  });
});
