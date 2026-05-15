import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

async function makeMockHelper(
  tempDir: string,
  response: Record<string, unknown>,
): Promise<{ helperPath: string; logPath: string }> {
  const helperPath = path.join(tempDir, "mock-reliable-request.mjs");
  const logPath = path.join(tempDir, "helper-argv.json");
  await fs.writeFile(
    helperPath,
    [
      "import fs from 'node:fs';",
      "const logPath = process.env.MOCK_RELIABLE_REQUEST_LOG;",
      "if (logPath) fs.writeFileSync(logPath, JSON.stringify(process.argv.slice(2)));",
      `process.stdout.write(${JSON.stringify(JSON.stringify(response))});`,
    ].join("\n"),
    "utf8",
  );
  return { helperPath, logPath };
}

async function makeInvalidJsonHelper(tempDir: string): Promise<string> {
  const helperPath = path.join(tempDir, "invalid-json-helper.mjs");
  await fs.writeFile(
    helperPath,
    "process.stdout.write('this is not json');\n",
    "utf8",
  );
  return helperPath;
}

async function runShell(
  scriptPath: string,
  args: string[],
  env: NodeJS.ProcessEnv,
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  try {
    const result = await execFileAsync("sh", [scriptPath, ...args], { env });
    return { ...result, exitCode: 0 };
  } catch (error) {
    const failed = error as Error & {
      stdout?: string;
      stderr?: string;
      code?: number;
    };
    return {
      stdout: failed.stdout ?? "",
      stderr: failed.stderr ?? "",
      exitCode: typeof failed.code === "number" ? failed.code : 1,
    };
  }
}

const tempRoots: string[] = [];

afterEach(async () => {
  await Promise.all(
    tempRoots.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })),
  );
});

describe("transport-aware wrapper scripts", () => {
  it("integration.sh preserves the JSON envelope contract when reliability is off", async () => {
    await withJsonServer({ ok: true }, async (baseUrl, seen) => {
      const { stdout, exitCode } = await runShell(
        path.resolve("src/lib/templates/scripts/integration.sh"),
        ["google", "calendar"],
        {
          ...process.env,
          BOT_ID: "bot-test",
          GATEWAY_TOKEN: "gw-token",
          CHAT_PROXY_URL: baseUrl,
          CORE_AGENT_TRANSPORT_RELIABILITY: "off",
          CORE_AGENT_RELIABLE_REQUEST_SCRIPT: "/missing/reliable-request.mjs",
        },
      );

      expect(exitCode).toBe(0);
      expect(stdout.trim()).toBe('{"ok":true}');
      expect(seen.requests[0]).toMatchObject({
        method: "GET",
        url: "/v1/integrations/google/calendar",
      });
      expect(seen.requests[0]?.headers.authorization).toBe("Bearer gw-token");
    });
  });

  it("integration.sh accepts documented service/action --post JSON calls", async () => {
    await withJsonServer({ ok: true }, async (baseUrl, seen) => {
      const body = '{"sido":"11","yongdo":"apt"}';
      const { stdout, exitCode } = await runShell(
        path.resolve("src/lib/templates/scripts/integration.sh"),
        ["auction/court/search-ongoing", "--post", body],
        {
          ...process.env,
          BOT_ID: "bot-test",
          GATEWAY_TOKEN: "gw-token",
          CHAT_PROXY_URL: baseUrl,
          CORE_AGENT_TRANSPORT_RELIABILITY: "off",
          CORE_AGENT_RELIABLE_REQUEST_SCRIPT: "/missing/reliable-request.mjs",
        },
      );

      expect(exitCode).toBe(0);
      expect(stdout.trim()).toBe('{"ok":true}');
      expect(seen.requests[0]).toMatchObject({
        method: "POST",
        url: "/v1/integrations/auction/court/search-ongoing",
        body,
      });
    });
  });

  it("integration.sh aliases public skill ids to chat-proxy service namespaces", async () => {
    await withJsonServer({ ok: true }, async (baseUrl, seen) => {
      const env = {
        ...process.env,
        BOT_ID: "bot-test",
        GATEWAY_TOKEN: "gw-token",
        CHAT_PROXY_URL: baseUrl,
        CORE_AGENT_TRANSPORT_RELIABILITY: "off",
        CORE_AGENT_RELIABLE_REQUEST_SCRIPT: "/missing/reliable-request.mjs",
      };

      const calls = [
        {
          args: ["korean-corporate-disclosure/company?corp_code=00126380"],
          url: "/v1/integrations/dart/company?corp_code=00126380",
        },
        {
          args: ["court-auction/court/search-ongoing", "--post", '{"sido":"11"}'],
          url: "/v1/integrations/auction/court/search-ongoing",
        },
        {
          args: ["maps-korea/kakao/places?query=서울"],
          url: "/v1/integrations/maps-kr/kakao/places?query=%EC%84%9C%EC%9A%B8",
        },
        {
          args: ["maps-google/geocode?address=Seoul"],
          url: "/v1/integrations/maps/geocode?address=Seoul",
        },
        {
          args: ["golf-caddie/search?query=용인"],
          url: "/v1/integrations/golf/search?query=%EC%9A%A9%EC%9D%B8",
        },
        {
          args: ["fmp-financial-data/stable/income-statement?symbol=AAPL"],
          url: "/v1/integrations/fmp/stable/income-statement?symbol=AAPL",
        },
      ];

      for (const call of calls) {
        const { exitCode } = await runShell(
          path.resolve("src/lib/templates/scripts/integration.sh"),
          call.args,
          env,
        );
        expect(exitCode).toBe(0);
      }

      expect(seen.requests.map((request) => request.url)).toEqual(
        calls.map((call) => call.url),
      );
    });
  });

  it("kb-write.sh preserves the JSON envelope contract when reliability is off", async () => {
    await withJsonServer({ ok: true, id: "doc-1" }, async (baseUrl, seen) => {
      const { stdout, exitCode } = await runShell(
        path.resolve("src/lib/templates/scripts/kb-write.sh"),
        ["--create-collection", "Reports"],
        {
          ...process.env,
          BOT_ID: "bot-test",
          GATEWAY_TOKEN: "gw-token",
          CHAT_PROXY_URL: baseUrl,
          CORE_AGENT_TRANSPORT_RELIABILITY: "off",
          CORE_AGENT_RELIABLE_REQUEST_SCRIPT: "/missing/reliable-request.mjs",
        },
      );

      expect(exitCode).toBe(0);
      expect(stdout.trim()).toBe('{"ok":true,"id":"doc-1"}');
      expect(seen.requests[0]).toMatchObject({
        method: "POST",
        url: "/v1/integrations/knowledge-write/create-collection",
      });
      expect(seen.requests[0]?.body).toBe('{"name":"Reports"}');
    });
  });

  it("file-send.sh preserves multipart delivery when reliability is off", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "file-send-fallback-"));
    tempRoots.push(root);
    const filePath = path.join(root, "report.md");
    await fs.writeFile(filePath, "# report\n", "utf8");

    await withJsonServer({ id: "att_fallback", ok: true }, async (baseUrl, seen) => {
      const { stdout, exitCode } = await runShell(
        path.resolve("src/lib/templates/scripts/file-send.sh"),
        [filePath, "general"],
        {
          ...process.env,
          GATEWAY_TOKEN: "gw-token",
          CHAT_PROXY_URL: baseUrl,
          CORE_AGENT_TRANSPORT_RELIABILITY: "off",
          CORE_AGENT_RELIABLE_REQUEST_SCRIPT: "/missing/reliable-request.mjs",
        },
      );

      expect(exitCode).toBe(0);
      expect(stdout).toContain('{"id":"att_fallback","ok":true}');
      expect(stdout).toContain("[attachment:att_fallback:report.md]");
      expect(seen.requests[0]).toMatchObject({
        method: "POST",
        url: "/v1/bot-channels/attachment",
      });
      expect(seen.requests[0]?.headers["content-type"]).toContain("multipart/form-data");
      expect(seen.requests[0]?.body).toContain("channel_name");
      expect(seen.requests[0]?.body).toContain("report.md");
    });
  });

  it("integration.sh routes GET requests through the shared reliable helper", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "integration-wrapper-"));
    tempRoots.push(root);
    const { helperPath, logPath } = await makeMockHelper(root, {
      ok: true,
      attemptCount: 2,
      classification: "transient",
      statusCode: 200,
      body: '{"events":[{"id":"evt_1"}]}',
    });

    const { stdout, exitCode } = await runShell(
      path.resolve("src/lib/templates/scripts/integration.sh"),
      ["google", "calendar"],
      {
        ...process.env,
        BOT_ID: "bot-test",
        GATEWAY_TOKEN: "gw-token",
        CHAT_PROXY_URL: "http://proxy.internal",
        CORE_AGENT_RELIABLE_REQUEST_SCRIPT: helperPath,
        MOCK_RELIABLE_REQUEST_LOG: logPath,
      },
    );

    expect(exitCode).toBe(0);
    expect(stdout.trim()).toBe('{"events":[{"id":"evt_1"}]}');

    const argv = JSON.parse(await fs.readFile(logPath, "utf8")) as string[];
    expect(argv).toEqual(
      expect.arrayContaining([
        "--method",
        "GET",
        "--url",
        "http://proxy.internal/v1/integrations/google/calendar",
        "--header",
        "Authorization: Bearer gw-token",
        "--header",
        "X-Bot-Id: bot-test",
      ]),
    );
  });

  it("kb-write.sh emits standardized transient failure JSON after retry exhaustion", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "kb-write-wrapper-"));
    tempRoots.push(root);
    const { helperPath } = await makeMockHelper(root, {
      ok: false,
      classification: "transient",
      attemptCount: 3,
      statusCode: 503,
      message: "upstream temporarily unavailable",
      retryExhausted: true,
    });

    const { stdout, exitCode } = await runShell(
      path.resolve("src/lib/templates/scripts/kb-write.sh"),
      ["--create-collection", "Reports"],
      {
        ...process.env,
        BOT_ID: "bot-test",
        GATEWAY_TOKEN: "gw-token",
        CHAT_PROXY_URL: "http://proxy.internal",
        CORE_AGENT_RELIABLE_REQUEST_SCRIPT: helperPath,
      },
    );

    expect(exitCode).toBe(1);
    expect(JSON.parse(stdout)).toEqual({
      ok: false,
      error: "transport_request_failed",
      classification: "transient",
      attemptCount: 3,
      statusCode: 503,
      retryExhausted: true,
      message: "upstream temporarily unavailable",
    });
  });

  it("kb-write.sh emits standardized failure JSON when the helper returns invalid JSON", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "kb-write-invalid-json-"));
    tempRoots.push(root);
    const helperPath = await makeInvalidJsonHelper(root);

    const { stdout, stderr, exitCode } = await runShell(
      path.resolve("src/lib/templates/scripts/kb-write.sh"),
      ["--create-collection", "Reports"],
      {
        ...process.env,
        BOT_ID: "bot-test",
        GATEWAY_TOKEN: "gw-token",
        CHAT_PROXY_URL: "http://proxy.internal",
        CORE_AGENT_RELIABLE_REQUEST_SCRIPT: helperPath,
      },
    );

    expect(exitCode).toBe(1);
    expect(stderr).not.toContain("SyntaxError");
    expect(JSON.parse(stdout)).toEqual({
      ok: false,
      error: "transport_request_failed",
      classification: "fatal",
      attemptCount: 1,
      retryExhausted: false,
      message: "reliable request helper returned invalid JSON",
    });
  });

  it("web-search.sh routes search requests through the shared reliable helper", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "web-search-wrapper-"));
    tempRoots.push(root);
    const { helperPath, logPath } = await makeMockHelper(root, {
      ok: true,
      attemptCount: 1,
      classification: "success",
      statusCode: 200,
      body: '{"results":[{"title":"Next.js 16"}]}',
    });

    const { stdout, exitCode } = await runShell(
      path.resolve("src/lib/templates/skills/web-search/scripts/web-search.sh"),
      ["next.js 16 release notes"],
      {
        ...process.env,
        API_PROXY_URL: "http://api-proxy.internal",
        GATEWAY_TOKEN: "gw-token",
        CORE_AGENT_RELIABLE_REQUEST_SCRIPT: helperPath,
        MOCK_RELIABLE_REQUEST_LOG: logPath,
      },
    );

    expect(exitCode).toBe(0);
    expect(stdout.trim()).toBe('{"results":[{"title":"Next.js 16"}]}');

    const argv = JSON.parse(await fs.readFile(logPath, "utf8")) as string[];
    expect(argv).toEqual(
      expect.arrayContaining([
        "--method",
        "POST",
        "--url",
        "http://api-proxy.internal/v1/search",
        "--header",
        "Authorization: Bearer gw-token",
      ]),
    );
  });

  it("lifecycle web-search.sh uses the same API proxy transport as the skill script", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "web-search-lifecycle-"));
    tempRoots.push(root);
    const { helperPath, logPath } = await makeMockHelper(root, {
      ok: true,
      attemptCount: 1,
      classification: "success",
      statusCode: 200,
      body: '{"results":[{"title":"Platform search"}]}',
    });

    const { stdout, exitCode } = await runShell(
      path.resolve("src/lib/templates/scripts/web-search.sh"),
      ["platform search"],
      {
        ...process.env,
        CORE_AGENT_API_PROXY_URL: "http://api-proxy.internal",
        GATEWAY_TOKEN: "gw-token",
        CORE_AGENT_RELIABLE_REQUEST_SCRIPT: helperPath,
        MOCK_RELIABLE_REQUEST_LOG: logPath,
      },
    );

    expect(exitCode).toBe(0);
    expect(stdout.trim()).toBe('{"results":[{"title":"Platform search"}]}');

    const argv = JSON.parse(await fs.readFile(logPath, "utf8")) as string[];
    expect(argv).toEqual(
      expect.arrayContaining([
        "--method",
        "POST",
        "--url",
        "http://api-proxy.internal/v1/search",
        "--header",
        "Authorization: Bearer gw-token",
      ]),
    );
  });

  it("file-send.sh routes attachments through the shared reliable helper and preserves marker output", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "file-send-wrapper-"));
    tempRoots.push(root);
    const filePath = path.join(root, "report.md");
    await fs.writeFile(filePath, "# report\n", "utf8");
    const { helperPath, logPath } = await makeMockHelper(root, {
      ok: true,
      attemptCount: 1,
      classification: "success",
      statusCode: 200,
      body: '{"id":"att_123","ok":true}',
    });

    const { stdout, exitCode } = await runShell(
      path.resolve("src/lib/templates/scripts/file-send.sh"),
      [filePath, "general"],
      {
        ...process.env,
        GATEWAY_TOKEN: "gw-token",
        CHAT_PROXY_URL: "http://proxy.internal",
        CORE_AGENT_RELIABLE_REQUEST_SCRIPT: helperPath,
        MOCK_RELIABLE_REQUEST_LOG: logPath,
      },
    );

    expect(exitCode).toBe(0);
    expect(stdout).toContain('{"id":"att_123","ok":true}');
    expect(stdout).toContain("[attachment:att_123:report.md]");

    const argv = JSON.parse(await fs.readFile(logPath, "utf8")) as string[];
    expect(argv).toEqual(
      expect.arrayContaining([
        "--method",
        "POST",
        "--url",
        "http://proxy.internal/v1/bot-channels/attachment",
        "--form-field",
        "channel_name=general",
      ]),
    );
    expect(argv.some((value) => value === "--form-file")).toBe(true);
  });
});

async function withJsonServer(
  responseBody: Record<string, unknown>,
  fn: (
    baseUrl: string,
    seen: {
      requests: Array<{
        method?: string;
        url?: string;
        headers: http.IncomingHttpHeaders;
        body: string;
      }>;
    },
  ) => Promise<void>,
): Promise<void> {
  const seen: {
    requests: Array<{
      method?: string;
      url?: string;
      headers: http.IncomingHttpHeaders;
      body: string;
    }>;
  } = { requests: [] };
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    req.on("end", () => {
      seen.requests.push({
        method: req.method,
        url: req.url,
        headers: req.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      });
      res.statusCode = 200;
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(responseBody));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const addr = server.address();
    if (!addr || typeof addr !== "object") {
      throw new Error("server did not bind to a TCP port");
    }
    await fn(`http://127.0.0.1:${addr.port}`, seen);
  } finally {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => (err ? reject(err) : resolve()));
    });
  }
}
