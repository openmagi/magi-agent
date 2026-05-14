import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { ToolContext } from "../Tool.js";
import {
  makeExternalSourceCacheTool,
  type ExternalSourceCacheGitRunner,
  type ExternalSourceCacheGitRun,
  type ExternalSourceCacheUrlRun,
  type ExternalSourceCacheUrlRunner,
} from "./ExternalSourceCache.js";

const roots: string[] = [];

async function makeRoot(prefix: string): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), prefix));
  roots.push(root);
  return root;
}

function ctx(workspaceRoot: string): ToolContext {
  return {
    botId: "bot-1",
    sessionKey: "s-1",
    turnId: "turn-1",
    workspaceRoot,
    askUser: async () => ({ selectedId: "ok" }),
    emitProgress: () => {},
    emitAgentEvent: () => {},
    abortSignal: AbortSignal.timeout(5_000),
    staging: {
      stageFileWrite: () => {},
      stageTranscriptAppend: () => {},
      stageAuditEvent: () => {},
    },
  };
}

interface GitRunCall {
  args: string[];
  cwd?: string;
}

interface UrlRunCall {
  url: string;
  format: string;
  timeoutMs: number;
}

function fakeGitRunner(
  calls: GitRunCall[],
  commit = "abc123",
): ExternalSourceCacheGitRunner {
  return async (run: ExternalSourceCacheGitRun) => {
    calls.push({ args: run.args, cwd: run.cwd });
    if (run.args[0] === "clone") {
      const target = run.args.at(-1);
      if (target) await fs.mkdir(path.join(target, ".git"), { recursive: true });
    }
    if (run.args[0] === "rev-parse") {
      return { stdout: `${commit}\n`, stderr: "", exitCode: 0 };
    }
    return { stdout: "", stderr: "", exitCode: 0 };
  };
}

function fakeUrlRunner(
  calls: UrlRunCall[],
  result: {
    statusCode?: number;
    url?: string;
    finalUrl?: string;
    contentType?: string;
    body: string;
    truncated?: boolean;
  },
): ExternalSourceCacheUrlRunner {
  return async (run: ExternalSourceCacheUrlRun) => {
    calls.push({
      url: run.url,
      format: run.format,
      timeoutMs: run.timeoutMs,
    });
    return {
      statusCode: result.statusCode ?? 200,
      url: result.url ?? run.url,
      finalUrl: result.finalUrl ?? result.url ?? run.url,
      contentType: result.contentType,
      body: result.body,
      truncated: result.truncated ?? false,
    };
  };
}

function expectedDocsSource(url: string, format: string): string {
  const parsed = new URL(url);
  const hash = crypto.createHash("sha256").update(`${url}\0${format}`).digest("hex").slice(0, 16);
  return `docs/${parsed.hostname.toLowerCase()}/${hash}`;
}

async function readJson(filePath: string): Promise<Record<string, unknown>> {
  return JSON.parse(await fs.readFile(filePath, "utf8")) as Record<string, unknown>;
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })),
  );
});

describe("ExternalSourceCache", () => {
  it("caches a public docs URL into the managed external cache", async () => {
    const workspaceRoot = await makeRoot("external-cache-workspace-");
    const cacheRoot = await makeRoot("external-cache-root-");
    const calls: UrlRunCall[] = [];
    const url = "https://docs.example.com/sdk";
    const source = expectedDocsSource(url, "markdown");
    const tool = makeExternalSourceCacheTool({
      cacheRoot,
      now: () => 4321,
      gitRunner: fakeGitRunner([]),
      urlRunner: fakeUrlRunner(calls, {
        contentType: "text/html; charset=utf-8",
        body: "<html><head><title>SDK Docs</title></head><body><h1>SDK Docs</h1><p>Install the SDK.</p></body></html>",
      }),
      resolveHost: async () => [{ address: "93.184.216.34", family: 4 }],
    });

    const result = await tool.execute(
      { action: "ensure_url", url, format: "markdown" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      action: "ensure_url",
      source,
      url,
      finalUrl: url,
      path: "index.md",
      populated: true,
      truncated: false,
      pruned: [],
    });
    expect(result.output?.contentSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(result.output?.sizeBytes).toBeGreaterThan(0);
    expect(calls).toEqual([{ url, format: "markdown", timeoutMs: 180_000 }]);

    const cached = await fs.readFile(path.join(cacheRoot, source, "index.md"), "utf8");
    expect(cached).toContain("SDK Docs");
    expect(cached).toContain("Install the SDK.");
    expect(cached).not.toContain("<h1>");

    const metadata = await readJson(path.join(cacheRoot, ".magi-cache-metadata", `${source}.json`));
    expect(metadata).toMatchObject({
      kind: "url",
      source,
      url,
      finalUrl: url,
      path: "index.md",
      format: "markdown",
      contentType: "text/html; charset=utf-8",
      fetchedAt: 4321,
      lastAccessedAt: 4321,
    });
  });

  it("rejects private docs URLs before invoking the URL runner", async () => {
    const workspaceRoot = await makeRoot("external-cache-workspace-");
    const cacheRoot = await makeRoot("external-cache-root-");
    const calls: UrlRunCall[] = [];
    const tool = makeExternalSourceCacheTool({
      cacheRoot,
      gitRunner: fakeGitRunner([]),
      urlRunner: fakeUrlRunner(calls, { body: "secret" }),
    });

    const result = await tool.execute(
      { action: "ensure_url", url: "http://localhost/docs" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_url");
    expect(calls).toEqual([]);
  });

  it("clones a GitHub repository into the managed external cache", async () => {
    const workspaceRoot = await makeRoot("external-cache-workspace-");
    const cacheRoot = await makeRoot("external-cache-root-");
    const calls: GitRunCall[] = [];
    const tool = makeExternalSourceCacheTool({
      cacheRoot,
      now: () => 1234,
      gitRunner: fakeGitRunner(calls, "abc123"),
    });

    const result = await tool.execute(
      { action: "ensure_repo", url: "https://github.com/anomalyco/opencode", ref: "main" },
      ctx(workspaceRoot),
    );

    const sourceRoot = path.join(cacheRoot, "github.com/anomalyco/opencode");
    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      action: "ensure_repo",
      source: "github.com/anomalyco/opencode",
      url: "https://github.com/anomalyco/opencode.git",
      ref: "main",
      commit: "abc123",
      populated: true,
      pruned: [],
    });
    expect(calls.map((call) => call.args)).toEqual([
      [
        "clone",
        "--depth",
        "1",
        "--branch",
        "main",
        "https://github.com/anomalyco/opencode.git",
        sourceRoot,
      ],
      ["rev-parse", "HEAD"],
    ]);
    expect(calls[1]?.cwd).toBe(sourceRoot);
    const metadata = await readJson(
      path.join(cacheRoot, ".magi-cache-metadata/github.com/anomalyco/opencode.json"),
    );
    expect(metadata).toMatchObject({
      source: "github.com/anomalyco/opencode",
      url: "https://github.com/anomalyco/opencode.git",
      ref: "main",
      commit: "abc123",
      fetchedAt: 1234,
      lastAccessedAt: 1234,
    });
  });

  it("updates an existing cached repository with a pinned ref", async () => {
    const workspaceRoot = await makeRoot("external-cache-workspace-");
    const cacheRoot = await makeRoot("external-cache-root-");
    const sourceRoot = path.join(cacheRoot, "github.com/anomalyco/opencode");
    await fs.mkdir(path.join(sourceRoot, ".git"), { recursive: true });
    const calls: GitRunCall[] = [];
    const tool = makeExternalSourceCacheTool({
      cacheRoot,
      now: () => 2000,
      gitRunner: fakeGitRunner(calls, "def456"),
    });

    const result = await tool.execute(
      { action: "ensure_repo", url: "https://github.com/anomalyco/opencode.git", ref: "v1.2.3" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      source: "github.com/anomalyco/opencode",
      ref: "v1.2.3",
      commit: "def456",
      populated: false,
    });
    expect(calls).toEqual([
      { args: ["fetch", "--depth", "1", "origin", "v1.2.3"], cwd: sourceRoot },
      { args: ["checkout", "--detach", "FETCH_HEAD"], cwd: sourceRoot },
      { args: ["rev-parse", "HEAD"], cwd: sourceRoot },
    ]);
  });

  it("rejects non-GitHub or non-HTTPS repository URLs", async () => {
    const workspaceRoot = await makeRoot("external-cache-workspace-");
    const cacheRoot = await makeRoot("external-cache-root-");
    const calls: GitRunCall[] = [];
    const tool = makeExternalSourceCacheTool({
      cacheRoot,
      gitRunner: fakeGitRunner(calls),
    });

    const nonGithub = await tool.execute(
      { action: "ensure_repo", url: "https://evil.example/repo.git" },
      ctx(workspaceRoot),
    );
    const sshUrl = await tool.execute(
      { action: "ensure_repo", url: "git@github.com:owner/repo.git" },
      ctx(workspaceRoot),
    );

    expect(nonGithub.status).toBe("error");
    expect(nonGithub.errorCode).toBe("invalid_url");
    expect(sshUrl.status).toBe("error");
    expect(sshUrl.errorCode).toBe("invalid_url");
    expect(calls).toEqual([]);
  });

  it("rejects unsafe refs before invoking git", async () => {
    const workspaceRoot = await makeRoot("external-cache-workspace-");
    const cacheRoot = await makeRoot("external-cache-root-");
    const calls: GitRunCall[] = [];
    const tool = makeExternalSourceCacheTool({
      cacheRoot,
      gitRunner: fakeGitRunner(calls),
    });

    const result = await tool.execute(
      {
        action: "ensure_repo",
        url: "https://github.com/anomalyco/opencode",
        ref: "--upload-pack=sh",
      },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("invalid_ref");
    expect(calls).toEqual([]);
  });

  it("rejects cache source escapes before invoking git", async () => {
    const workspaceRoot = await makeRoot("external-cache-workspace-");
    const cacheRoot = await makeRoot("external-cache-root-");
    const calls: GitRunCall[] = [];
    const tool = makeExternalSourceCacheTool({
      cacheRoot,
      gitRunner: fakeGitRunner(calls),
      normalizeRepoUrl: () => ({
        source: "../outside",
        url: "https://github.com/anomalyco/opencode.git",
      }),
    });

    const result = await tool.execute(
      { action: "ensure_repo", url: "https://github.com/anomalyco/opencode" },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("error");
    expect(result.errorCode).toBe("path_escape");
    expect(calls).toEqual([]);
  });

  it("prunes old cache entries by metadata age and keeps recent entries", async () => {
    const workspaceRoot = await makeRoot("external-cache-workspace-");
    const cacheRoot = await makeRoot("external-cache-root-");
    const metadataRoot = path.join(cacheRoot, ".magi-cache-metadata");
    await fs.mkdir(path.join(cacheRoot, "github.com/old/repo"), { recursive: true });
    await fs.mkdir(path.join(cacheRoot, "github.com/recent/repo"), { recursive: true });
    await fs.mkdir(path.join(metadataRoot, "github.com/old"), { recursive: true });
    await fs.mkdir(path.join(metadataRoot, "github.com/recent"), { recursive: true });
    await fs.writeFile(
      path.join(metadataRoot, "github.com/old/repo.json"),
      JSON.stringify({
        source: "github.com/old/repo",
        url: "https://github.com/old/repo.git",
        fetchedAt: 1000,
        lastAccessedAt: 1000,
      }),
    );
    await fs.writeFile(
      path.join(metadataRoot, "github.com/recent/repo.json"),
      JSON.stringify({
        source: "github.com/recent/repo",
        url: "https://github.com/recent/repo.git",
        fetchedAt: 31 * 24 * 60 * 60 * 1000,
        lastAccessedAt: 31 * 24 * 60 * 60 * 1000,
      }),
    );
    const tool = makeExternalSourceCacheTool({
      cacheRoot,
      now: () => 31 * 24 * 60 * 60 * 1000,
      gitRunner: fakeGitRunner([]),
    });

    const result = await tool.execute(
      { action: "prune", maxAgeDays: 30 },
      ctx(workspaceRoot),
    );

    expect(result.status).toBe("ok");
    expect(result.output).toMatchObject({
      action: "prune",
      pruned: ["github.com/old/repo"],
    });
    await expect(fs.stat(path.join(cacheRoot, "github.com/old/repo"))).rejects.toMatchObject({
      code: "ENOENT",
    });
    await expect(fs.stat(path.join(cacheRoot, "github.com/recent/repo"))).resolves.toBeTruthy();
  });
});
