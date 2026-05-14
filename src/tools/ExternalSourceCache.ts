import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Utf8StreamCapture } from "../util/Utf8StreamCapture.js";
import { errorResult } from "../util/toolResult.js";
import { withMagiBinPath } from "../util/shellPath.js";
import {
  defaultResolvePublicFetchHost,
  validatePublicFetchUrl,
  type PublicFetchHostResolver,
} from "../util/publicFetchUrl.js";
import {
  normalizeWebFetchFormat,
  renderWebFetchContent,
  type WebFetchFormat,
} from "./WebFetch.js";

export type ExternalSourceCacheAction = "ensure_repo" | "ensure_url" | "prune";

export interface ExternalSourceCacheInput {
  action: ExternalSourceCacheAction;
  url?: string;
  ref?: string;
  format?: WebFetchFormat;
  maxAgeDays?: number;
  maxEntries?: number;
  timeoutMs?: number;
}

export interface ExternalSourceCacheOutput {
  action: ExternalSourceCacheAction;
  source?: string;
  url?: string;
  finalUrl?: string;
  ref?: string;
  path?: string;
  format?: WebFetchFormat;
  commit?: string;
  contentType?: string;
  contentSha256?: string;
  sizeBytes?: number;
  populated?: boolean;
  truncated?: boolean;
  pruned: string[];
}

export interface ExternalSourceCacheGitRun {
  args: string[];
  cwd?: string;
  timeoutMs: number;
  signal: AbortSignal;
}

export interface ExternalSourceCacheGitResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
}

export type ExternalSourceCacheGitRunner = (
  run: ExternalSourceCacheGitRun,
) => Promise<ExternalSourceCacheGitResult>;

export interface ExternalSourceCacheUrlRun {
  url: string;
  format: WebFetchFormat;
  timeoutMs: number;
  signal: AbortSignal;
}

export interface ExternalSourceCacheUrlResult {
  statusCode: number;
  url: string;
  finalUrl?: string;
  contentType?: string;
  body: string;
  truncated: boolean;
}

export type ExternalSourceCacheUrlRunner = (
  run: ExternalSourceCacheUrlRun,
) => Promise<ExternalSourceCacheUrlResult>;

interface NormalizedRepo {
  source: string;
  url: string;
}

interface CacheMetadata {
  kind?: "repo" | "url";
  source: string;
  url: string;
  finalUrl?: string;
  ref?: string;
  path?: string;
  format?: WebFetchFormat;
  commit?: string;
  contentType?: string;
  contentSha256?: string;
  sizeBytes?: number;
  truncated?: boolean;
  fetchedAt: number;
  lastAccessedAt: number;
}

interface ExternalSourceCacheOptions {
  cacheRoot?: string;
  now?: () => number;
  gitRunner?: ExternalSourceCacheGitRunner;
  urlRunner?: ExternalSourceCacheUrlRunner;
  resolveHost?: PublicFetchHostResolver;
  normalizeRepoUrl?: (url: string) => NormalizedRepo | null;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: ["ensure_repo", "ensure_url", "prune"],
      description:
        "ensure_repo clones or updates a GitHub repo cache; ensure_url caches a public docs URL; prune removes stale cache entries.",
    },
    url: {
      type: "string",
      description:
        "GitHub HTTPS repository URL for ensure_repo, or public HTTP(S) docs URL for ensure_url.",
    },
    ref: {
      type: "string",
      description: "Optional branch, tag, or commit-ish to fetch/checkout.",
    },
    format: {
      type: "string",
      enum: ["markdown", "text", "html"],
      description: "Docs URL cache format for ensure_url. Defaults to markdown.",
    },
    maxAgeDays: {
      type: "integer",
      minimum: 1,
      description: "Prune cache entries older than this many days.",
    },
    maxEntries: {
      type: "integer",
      minimum: 1,
      description: "Keep at most this many newest cache entries.",
    },
    timeoutMs: {
      type: "integer",
      minimum: 100,
      description: "Git operation timeout in milliseconds.",
    },
  },
  required: ["action"],
  additionalProperties: false,
} as const;

const DEFAULT_TIMEOUT_MS = 180_000;
const MAX_TIMEOUT_MS = 600_000;
const MAX_GIT_OUTPUT_BYTES = 512 * 1024;
const MAX_URL_BODY_BYTES = 1024 * 1024;
const DAY_MS = 24 * 60 * 60 * 1000;

function defaultCacheRoot(): string {
  return process.env.MAGI_EXTERNAL_SOURCE_CACHE_ROOT
    ?? path.join(os.tmpdir(), "magi-external-sources");
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function positiveInt(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : undefined;
}

function normalizeRelative(value: string): string {
  return path.normalize(value).replace(/^[/\\]+/, "");
}

function isUnderRoot(absPath: string, absRoot: string): boolean {
  return absPath === absRoot || absPath.startsWith(`${absRoot}${path.sep}`);
}

function resolveInside(root: string, relPath: string): string | null {
  const absRoot = path.resolve(root);
  const resolved = path.resolve(absRoot, normalizeRelative(relPath));
  return isUnderRoot(resolved, absRoot) ? resolved : null;
}

function normalizeGithubRepoUrl(rawUrl: string): NormalizedRepo | null {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" || parsed.hostname.toLowerCase() !== "github.com") {
    return null;
  }
  const parts = parsed.pathname
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .filter(Boolean);
  if (parts.length !== 2) return null;
  const [owner, rawRepo] = parts;
  const repo = rawRepo?.replace(/\.git$/i, "");
  const nameRe = /^[A-Za-z0-9_.-]+$/;
  if (!owner || !repo || !nameRe.test(owner) || !nameRe.test(repo)) return null;
  const source = `github.com/${owner}/${repo}`;
  return {
    source,
    url: `https://github.com/${owner}/${repo}.git`,
  };
}

function validateRef(ref: string): string | null {
  if (!ref) return null;
  if (ref.length > 200) return "ref is too long";
  if (ref.startsWith("-")) return "ref must not start with '-'";
  if (ref.includes("..")) return "ref must not contain '..'";
  if (/[\s~^:?*[\\\]{};$|&`'"<>]/.test(ref)) return "ref contains unsafe characters";
  return null;
}

function contentSha256(content: string): string {
  return createHash("sha256").update(content).digest("hex");
}

function docsCachePath(format: WebFetchFormat): string {
  if (format === "html") return "index.html";
  if (format === "text") return "index.txt";
  return "index.md";
}

function docsCacheSource(finalUrl: string, format: WebFetchFormat): string | null {
  try {
    const parsed = new URL(finalUrl);
    const host = parsed.hostname.toLowerCase().replace(/[^a-z0-9.-]/g, "-");
    if (!host) return null;
    const hash = createHash("sha256").update(`${finalUrl}\0${format}`).digest("hex").slice(0, 16);
    return `docs/${host}/${hash}`;
  } catch {
    return null;
  }
}

function metadataRoot(cacheRoot: string): string {
  return path.join(cacheRoot, ".magi-cache-metadata");
}

function metadataPath(cacheRoot: string, source: string): string | null {
  return resolveInside(metadataRoot(cacheRoot), `${source}.json`);
}

async function pathExists(filePath: string): Promise<boolean> {
  try {
    await fs.stat(filePath);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "ENOENT" ? false : Promise.reject(err);
  }
}

async function isGitRepo(sourceRoot: string): Promise<boolean> {
  try {
    const stat = await fs.stat(path.join(sourceRoot, ".git"));
    return stat.isDirectory() || stat.isFile();
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw err;
  }
}

function timeoutMs(input: ExternalSourceCacheInput): number {
  return Math.min(MAX_TIMEOUT_MS, positiveInt(input.timeoutMs) ?? DEFAULT_TIMEOUT_MS);
}

async function defaultGitRunner(
  run: ExternalSourceCacheGitRun,
): Promise<ExternalSourceCacheGitResult> {
  return new Promise((resolve, reject) => {
    const child = spawn("git", run.args, {
      cwd: run.cwd,
      env: {
        ...withMagiBinPath(process.env),
        GIT_TERMINAL_PROMPT: "0",
        GIT_ASKPASS: "true",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = new Utf8StreamCapture(MAX_GIT_OUTPUT_BYTES);
    const stderr = new Utf8StreamCapture(MAX_GIT_OUTPUT_BYTES);
    child.stdout.on("data", (chunk: Buffer) => stdout.write(chunk));
    child.stderr.on("data", (chunk: Buffer) => stderr.write(chunk));
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 3_000).unref();
    }, run.timeoutMs);
    run.signal.addEventListener("abort", () => child.kill("SIGTERM"), { once: true });
    child.on("error", (err) => {
      clearTimeout(timeout);
      reject(err);
    });
    child.on("close", (exitCode) => {
      clearTimeout(timeout);
      resolve({
        stdout: stdout.end(),
        stderr: stderr.end(),
        exitCode,
      });
    });
  });
}

async function defaultUrlRunner(
  run: ExternalSourceCacheUrlRun,
): Promise<ExternalSourceCacheUrlResult> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), run.timeoutMs);
  run.signal.addEventListener("abort", () => controller.abort(), { once: true });
  try {
    const response = await fetch(run.url, {
      redirect: "follow",
      signal: controller.signal,
      headers: {
        Accept: "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.8",
        "User-Agent": "MagiResearchAgent/1.0",
      },
    });
    const buffer = Buffer.from(await response.arrayBuffer());
    const truncated = buffer.byteLength > MAX_URL_BODY_BYTES;
    const body = buffer.subarray(0, MAX_URL_BODY_BYTES).toString("utf8");
    return {
      statusCode: response.status,
      url: run.url,
      finalUrl: response.url,
      contentType: response.headers.get("content-type") ?? undefined,
      body,
      truncated,
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function runGit(
  gitRunner: ExternalSourceCacheGitRunner,
  args: string[],
  ctx: ToolContext,
  timeout: number,
  cwd?: string,
): Promise<string> {
  const result = await gitRunner({ args, cwd, timeoutMs: timeout, signal: ctx.abortSignal });
  if (result.exitCode !== 0) {
    throw new Error(
      `git ${args[0] ?? "command"} failed: ${result.stderr.trim() || `exit ${result.exitCode}`}`,
    );
  }
  return result.stdout;
}

async function writeMetadata(cacheRoot: string, metadata: CacheMetadata): Promise<void> {
  const filePath = metadataPath(cacheRoot, metadata.source);
  if (!filePath) throw new Error(`metadata path escapes cache root: ${metadata.source}`);
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
}

async function readMetadataFile(filePath: string): Promise<CacheMetadata | null> {
  try {
    const parsed = JSON.parse(await fs.readFile(filePath, "utf8")) as Partial<CacheMetadata>;
    if (
      typeof parsed.source !== "string" ||
      typeof parsed.url !== "string" ||
      typeof parsed.fetchedAt !== "number" ||
      typeof parsed.lastAccessedAt !== "number"
    ) {
      return null;
    }
    return {
      ...(parsed.kind === "repo" || parsed.kind === "url" ? { kind: parsed.kind } : {}),
      source: parsed.source,
      url: parsed.url,
      ...(typeof parsed.finalUrl === "string" ? { finalUrl: parsed.finalUrl } : {}),
      ...(typeof parsed.ref === "string" ? { ref: parsed.ref } : {}),
      ...(typeof parsed.path === "string" ? { path: parsed.path } : {}),
      ...(parsed.format === "markdown" || parsed.format === "text" || parsed.format === "html"
        ? { format: parsed.format }
        : {}),
      ...(typeof parsed.commit === "string" ? { commit: parsed.commit } : {}),
      ...(typeof parsed.contentType === "string" ? { contentType: parsed.contentType } : {}),
      ...(typeof parsed.contentSha256 === "string"
        ? { contentSha256: parsed.contentSha256 }
        : {}),
      ...(typeof parsed.sizeBytes === "number" ? { sizeBytes: parsed.sizeBytes } : {}),
      ...(typeof parsed.truncated === "boolean" ? { truncated: parsed.truncated } : {}),
      fetchedAt: parsed.fetchedAt,
      lastAccessedAt: parsed.lastAccessedAt,
    };
  } catch {
    return null;
  }
}

async function listMetadataFiles(root: string): Promise<string[]> {
  try {
    const entries = await fs.readdir(root, { withFileTypes: true });
    const files: string[] = [];
    for (const entry of entries) {
      const entryPath = path.join(root, entry.name);
      if (entry.isDirectory()) {
        files.push(...await listMetadataFiles(entryPath));
      } else if (entry.isFile() && entry.name.endsWith(".json")) {
        files.push(entryPath);
      }
    }
    return files;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
}

async function pruneCache(
  cacheRoot: string,
  input: ExternalSourceCacheInput,
  now: number,
): Promise<string[]> {
  const maxAgeDays = positiveInt(input.maxAgeDays);
  const maxEntries = positiveInt(input.maxEntries);
  if (!maxAgeDays && !maxEntries) return [];

  const records: Array<{ metadata: CacheMetadata; filePath: string; timestamp: number }> = [];
  for (const filePath of await listMetadataFiles(metadataRoot(cacheRoot))) {
    const metadata = await readMetadataFile(filePath);
    if (!metadata) continue;
    records.push({
      metadata,
      filePath,
      timestamp: metadata.lastAccessedAt || metadata.fetchedAt,
    });
  }

  const prune = new Set<string>();
  if (maxAgeDays) {
    const cutoff = now - maxAgeDays * DAY_MS;
    for (const record of records) {
      if (record.timestamp < cutoff) prune.add(record.metadata.source);
    }
  }
  if (maxEntries && records.length > maxEntries) {
    const sorted = [...records].sort((a, b) => b.timestamp - a.timestamp);
    for (const record of sorted.slice(maxEntries)) {
      prune.add(record.metadata.source);
    }
  }

  const pruned: string[] = [];
  for (const source of [...prune].sort()) {
    const sourceRoot = resolveInside(cacheRoot, source);
    const metaFile = metadataPath(cacheRoot, source);
    if (!sourceRoot || !metaFile) continue;
    await fs.rm(sourceRoot, { recursive: true, force: true });
    await fs.rm(metaFile, { force: true });
    pruned.push(source);
  }
  return pruned;
}

async function ensureUrlCache(
  cacheRoot: string,
  input: ExternalSourceCacheInput,
  ctx: ToolContext,
  now: number,
  urlRunner: ExternalSourceCacheUrlRunner,
  resolveHost: PublicFetchHostResolver | null,
  start: number,
): Promise<ToolResult<ExternalSourceCacheOutput>> {
  const rawUrl = stringValue(input.url);
  if (!rawUrl) {
    return validationError("invalid_input", "`url` is required for ensure_url", start);
  }
  const urlError = await validatePublicFetchUrl(rawUrl, resolveHost);
  if (urlError) {
    return validationError("invalid_url", urlError, start);
  }

  const format = normalizeWebFetchFormat(input.format);
  const run = await urlRunner({
    url: rawUrl,
    format,
    timeoutMs: timeoutMs(input),
    signal: ctx.abortSignal,
  });
  if (run.statusCode < 200 || run.statusCode >= 400) {
    return validationError(
      "fetch_failed",
      `docs URL fetch returned HTTP ${run.statusCode}`,
      start,
    );
  }

  const finalUrl = run.finalUrl ?? run.url;
  const source = docsCacheSource(finalUrl, format);
  if (!source) {
    return validationError("invalid_url", "docs URL final host is invalid", start);
  }
  const sourceRoot = resolveInside(cacheRoot, source);
  if (!sourceRoot) {
    return validationError(
      "path_escape",
      `source escapes external source cache: ${source}`,
      start,
    );
  }

  const rendered = renderWebFetchContent(run.body, run.contentType, format);
  const content = rendered.content;
  const filePath = docsCachePath(format);
  const cacheFile = resolveInside(sourceRoot, filePath);
  if (!cacheFile) {
    return validationError(
      "path_escape",
      `path escapes external source cache source: ${filePath}`,
      start,
    );
  }
  await fs.mkdir(path.dirname(cacheFile), { recursive: true });
  await fs.writeFile(cacheFile, content, "utf8");

  const hash = contentSha256(content);
  const sizeBytes = Buffer.byteLength(content, "utf8");
  await writeMetadata(cacheRoot, {
    kind: "url",
    source,
    url: rawUrl,
    finalUrl,
    path: filePath,
    format,
    contentType: run.contentType,
    contentSha256: hash,
    sizeBytes,
    truncated: run.truncated,
    fetchedAt: now,
    lastAccessedAt: now,
  });

  return {
    status: "ok",
    output: {
      action: "ensure_url",
      source,
      url: rawUrl,
      finalUrl,
      path: filePath,
      format,
      contentType: run.contentType,
      contentSha256: hash,
      sizeBytes,
      populated: true,
      truncated: run.truncated,
      pruned: [],
    },
    durationMs: Date.now() - start,
  };
}

function validationError(
  errorCode: string,
  errorMessage: string,
  start: number,
): ToolResult<ExternalSourceCacheOutput> {
  return {
    status: "error",
    errorCode,
    errorMessage,
    durationMs: Date.now() - start,
  };
}

export function makeExternalSourceCacheTool(
  opts: ExternalSourceCacheOptions = {},
): Tool<ExternalSourceCacheInput, ExternalSourceCacheOutput> {
  const cacheRoot = opts.cacheRoot ?? defaultCacheRoot();
  const gitRunner = opts.gitRunner ?? defaultGitRunner;
  const urlRunner = opts.urlRunner ?? defaultUrlRunner;
  const resolveHost = opts.resolveHost ?? (opts.urlRunner ? null : defaultResolvePublicFetchHost);
  const now = opts.now ?? (() => Date.now());
  const normalizeRepoUrl = opts.normalizeRepoUrl ?? normalizeGithubRepoUrl;

  return {
    name: "ExternalSourceCache",
    description:
      "Populate or prune the managed external repo/docs cache. Use ensure_repo on public GitHub repositories or ensure_url on public docs URLs before ExternalSourceRead; this tool writes only to the managed cache, never to the user workspace.",
    inputSchema: INPUT_SCHEMA,
    permission: "net",
    dangerous: false,
    mutatesWorkspace: false,
    isConcurrencySafe: false,
    tags: ["web", "research", "external", "repo", "cache"],
    validate(input) {
      if (!input || (input as ExternalSourceCacheInput).action === undefined) {
        return "`action` is required";
      }
      const action = (input as ExternalSourceCacheInput).action;
      if (action !== "ensure_repo" && action !== "ensure_url" && action !== "prune") {
        return "`action` must be ensure_repo, ensure_url, or prune";
      }
      if (
        (action === "ensure_repo" || action === "ensure_url") &&
        !stringValue((input as ExternalSourceCacheInput).url)
      ) {
        return `\`url\` is required for ${action}`;
      }
      return null;
    },
    async execute(
      input: ExternalSourceCacheInput,
      ctx: ToolContext,
    ): Promise<ToolResult<ExternalSourceCacheOutput>> {
      const start = Date.now();
      const action = input.action;
      if (action !== "ensure_repo" && action !== "ensure_url" && action !== "prune") {
        return validationError(
          "invalid_input",
          "`action` must be ensure_repo, ensure_url, or prune",
          start,
        );
      }

      try {
        await fs.mkdir(cacheRoot, { recursive: true });
        if (action === "prune") {
          const pruned = await pruneCache(cacheRoot, input, now());
          return {
            status: "ok",
            output: { action, pruned },
            durationMs: Date.now() - start,
          };
        }
        if (action === "ensure_url") {
          return await ensureUrlCache(
            cacheRoot,
            input,
            ctx,
            now(),
            urlRunner,
            resolveHost,
            start,
          );
        }

        const rawUrl = stringValue(input.url);
        if (!rawUrl) {
          return validationError("invalid_input", "`url` is required for ensure_repo", start);
        }
        const repo = normalizeRepoUrl(rawUrl);
        if (!repo) {
          return validationError(
            "invalid_url",
            "only GitHub HTTPS repository URLs are supported",
            start,
          );
        }
        const ref = stringValue(input.ref) ?? undefined;
        if (ref) {
          const refError = validateRef(ref);
          if (refError) return validationError("invalid_ref", refError, start);
        }

        const sourceRoot = resolveInside(cacheRoot, repo.source);
        if (!sourceRoot) {
          return validationError(
            "path_escape",
            `source escapes external source cache: ${repo.source}`,
            start,
          );
        }

        const opTimeout = timeoutMs(input);
        let populated = false;
        if (!await pathExists(sourceRoot)) {
          await fs.mkdir(path.dirname(sourceRoot), { recursive: true });
          const cloneArgs = ["clone", "--depth", "1"];
          if (ref) cloneArgs.push("--branch", ref);
          cloneArgs.push(repo.url, sourceRoot);
          await runGit(gitRunner, cloneArgs, ctx, opTimeout);
          populated = true;
        } else if (!await isGitRepo(sourceRoot)) {
          return validationError(
            "not_a_repo",
            `${repo.source} exists in external cache but is not a git repository`,
            start,
          );
        } else if (ref) {
          await runGit(gitRunner, ["fetch", "--depth", "1", "origin", ref], ctx, opTimeout, sourceRoot);
          await runGit(gitRunner, ["checkout", "--detach", "FETCH_HEAD"], ctx, opTimeout, sourceRoot);
        } else {
          await runGit(gitRunner, ["pull", "--ff-only"], ctx, opTimeout, sourceRoot);
        }

        const commit = (await runGit(
          gitRunner,
          ["rev-parse", "HEAD"],
          ctx,
          opTimeout,
          sourceRoot,
        )).trim();
        const timestamp = now();
        await writeMetadata(cacheRoot, {
          kind: "repo",
          source: repo.source,
          url: repo.url,
          ...(ref ? { ref } : {}),
          commit,
          fetchedAt: timestamp,
          lastAccessedAt: timestamp,
        });
        return {
          status: "ok",
          output: {
            action,
            source: repo.source,
            url: repo.url,
            ...(ref ? { ref } : {}),
            commit,
            populated,
            pruned: [],
          },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
