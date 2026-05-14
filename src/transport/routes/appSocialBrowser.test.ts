import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { HttpServer } from "../HttpServer.js";
import { resetLocalSocialBrowserSessionsForTests } from "./appSocialBrowser.js";

const TINY_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

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

async function installFakeAgentBrowser(binDir: string, callsFile: string): Promise<void> {
  const script = `#!/usr/bin/env node
const fs = require("fs");
const args = process.argv.slice(2);
fs.appendFileSync(process.env.MAGI_AGENT_BROWSER_CALLS_FILE, JSON.stringify(args) + "\\n");
const screenshotIndex = args.indexOf("screenshot");
if (screenshotIndex >= 0) {
  const target = args[screenshotIndex + 1];
  fs.writeFileSync(target, Buffer.from("${TINY_PNG_BASE64}", "base64"));
}
if (args.includes("get") && args.includes("url")) {
  process.stdout.write("https://example.local/social\\n");
}
`;
  const bin = path.join(binDir, "agent-browser");
  await fs.writeFile(bin, script, "utf8");
  await fs.chmod(bin, 0o755);
  await fs.writeFile(callsFile, "", "utf8");
}

describe("HttpServer /v1/app social-browser routes", () => {
  let tmp: string;
  let server: HttpServer;
  let port: number;
  let originalPath: string | undefined;
  let originalCallsFile: string | undefined;
  let callsFile: string;

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "magi-app-social-browser-"));
    callsFile = path.join(tmp, "agent-browser-calls.ndjson");
    const binDir = path.join(tmp, "bin");
    await fs.mkdir(binDir, { recursive: true });
    await installFakeAgentBrowser(binDir, callsFile);
    originalPath = process.env.PATH;
    originalCallsFile = process.env.MAGI_AGENT_BROWSER_CALLS_FILE;
    process.env.PATH = `${binDir}${path.delimiter}${originalPath ?? ""}`;
    process.env.MAGI_AGENT_BROWSER_CALLS_FILE = callsFile;

    const agent = {
      config: { botId: "bot-test", userId: "user-test", workspaceRoot: tmp },
    } as unknown as ConstructorParameters<typeof HttpServer>[0]["agent"];
    server = new HttpServer({ port: 0, agent, bearerToken: "local-token" });
    await server.start();
    const anyServer = server as unknown as { server: http.Server };
    const addr = anyServer.server.address();
    port = typeof addr === "object" && addr ? addr.port : 0;
  });

  afterEach(async () => {
    process.env.PATH = originalPath;
    if (originalCallsFile === undefined) delete process.env.MAGI_AGENT_BROWSER_CALLS_FILE;
    else process.env.MAGI_AGENT_BROWSER_CALLS_FILE = originalCallsFile;
    resetLocalSocialBrowserSessionsForTests();
    await server.stop();
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("starts a local one-time social browser session and returns only redacted preview data", async () => {
    const res = await requestJson(
      `http://127.0.0.1:${port}/v1/app/social-browser/session`,
      "local-token",
      { method: "POST", body: { provider: "instagram" } },
    );

    expect(res.status).toBe(201);
    const body = res.body as {
      ok: boolean;
      session: { provider: string; sessionId: string; agentSessionName?: string; cdpEndpoint?: string };
      screenshot: { contentType: string; imageBase64: string; url?: string };
    };
    expect(body.ok).toBe(true);
    expect(body.session.provider).toBe("instagram");
    expect(body.session.sessionId).toMatch(/^[a-f0-9]{32}$/);
    expect(body.session.agentSessionName).toBeUndefined();
    expect(body.session.cdpEndpoint).toBeUndefined();
    expect(body.screenshot).toEqual({
      contentType: "image/png",
      imageBase64: TINY_PNG_BASE64,
      url: "https://example.local/social",
    });

    const calls = (await fs.readFile(callsFile, "utf8"))
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as string[]);
    expect(calls[0]).toEqual(
      expect.arrayContaining(["open", "https://www.instagram.com/accounts/login/"]),
    );
    expect(calls[1]).toEqual(expect.arrayContaining(["screenshot"]));
  });

  it("runs local browser commands against an existing social session", async () => {
    const started = await requestJson(
      `http://127.0.0.1:${port}/v1/app/social-browser/session`,
      "local-token",
      { method: "POST", body: { provider: "x" } },
    );
    const sessionId = (started.body as { session: { sessionId: string } }).session.sessionId;

    const clicked = await requestJson(
      `http://127.0.0.1:${port}/v1/app/social-browser/session/${sessionId}/command`,
      "local-token",
      { method: "POST", body: { action: "click", x: 11, y: 22 } },
    );

    expect(clicked.status).toBe(200);
    expect(clicked.body).toEqual(
      expect.objectContaining({
        ok: true,
        contentType: "image/png",
        imageBase64: TINY_PNG_BASE64,
      }),
    );
    expect(JSON.stringify(clicked.body)).not.toContain("agentSessionName");

    const callsText = await fs.readFile(callsFile, "utf8");
    expect(callsText).toContain('"mouse","move","11","22"');
    expect(callsText).toContain('"mouse","down","left"');
    expect(callsText).toContain('"mouse","up","left"');
  });
});
