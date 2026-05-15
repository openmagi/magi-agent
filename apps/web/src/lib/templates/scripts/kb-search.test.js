import { afterEach, describe, expect, it } from "vitest";
import { execFile } from "node:child_process";
import http from "node:http";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const scriptPath = "src/lib/templates/scripts/kb-search.sh";

async function runKbSearch(args, env) {
  try {
    const result = await execFileAsync("sh", [scriptPath, ...args], { env });
    return { ...result, exitCode: 0 };
  } catch (error) {
    return {
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? "",
      exitCode: typeof error.code === "number" ? error.code : 1,
    };
  }
}

async function withJsonServer(responseBody, fn) {
  const seen = { requests: [] };
  const server = http.createServer((req, res) => {
    const chunks = [];
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

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    if (!address || typeof address !== "object") throw new Error("server did not bind");
    await fn(`http://127.0.0.1:${address.port}`, seen);
  } finally {
    await new Promise((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  }
}

function scriptEnv(baseUrl) {
  return {
    ...process.env,
    BOT_ID: "bot-test",
    GATEWAY_TOKEN: "gw-token",
    CHAT_PROXY_URL: baseUrl,
    CORE_AGENT_TRANSPORT_RELIABILITY: "off",
    CORE_AGENT_RELIABLE_REQUEST_SCRIPT: "/missing/reliable-request.mjs",
  };
}

afterEach(() => {
  delete process.env.CORE_AGENT_TRANSPORT_RELIABILITY;
});

describe("kb-search.sh", () => {
  it("posts manifest requests with Unicode, quotes, and newlines safely encoded", async () => {
    await withJsonServer({ ok: true }, async (baseUrl, seen) => {
      const collection = "르챔버 \"sales\"\nline";
      const { stdout, exitCode } = await runKbSearch(["--manifest", collection], scriptEnv(baseUrl));

      expect(exitCode).toBe(0);
      expect(stdout.trim()).toBe('{"ok":true}');
      expect(seen.requests[0]).toMatchObject({
        method: "POST",
        url: "/v1/integrations/knowledge/manifest",
      });
      expect(JSON.parse(seen.requests[0].body)).toEqual({ collection });
      expect(seen.requests[0].headers.authorization).toBe("Bearer gw-token");
      expect(seen.requests[0].headers["x-bot-id"]).toBe("bot-test");
    });
  });

  it("posts search requests with safe JSON bodies", async () => {
    await withJsonServer({ results: [] }, async (baseUrl, seen) => {
      const collection = "Shared KB";
      const query = "ARR \"revenue\"\n인식";
      const { stdout, exitCode } = await runKbSearch([collection, query, "20"], scriptEnv(baseUrl));

      expect(exitCode).toBe(0);
      expect(stdout.trim()).toBe('{"results":[]}');
      expect(seen.requests[0]).toMatchObject({
        method: "POST",
        url: "/v1/integrations/knowledge/search",
      });
      expect(JSON.parse(seen.requests[0].body)).toEqual({
        collection,
        query,
        top_k: 20,
      });
    });
  });

  it("can request a guide without a collection filter", async () => {
    await withJsonServer({ guide_markdown: "# KB" }, async (baseUrl, seen) => {
      const { stdout, exitCode } = await runKbSearch(["--guide"], scriptEnv(baseUrl));

      expect(exitCode).toBe(0);
      expect(stdout.trim()).toBe('{"guide_markdown":"# KB"}');
      expect(seen.requests[0]).toMatchObject({
        method: "POST",
        url: "/v1/integrations/knowledge/guide",
        body: "{}",
      });
    });
  });
});
