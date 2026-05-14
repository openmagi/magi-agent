import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";

const execFileAsync = promisify(execFile);

export interface RepositoryMapInput {
  cwd?: string;
  maxFiles?: number;
  includeDiff?: boolean;
}

export type RepositoryMapFileKind =
  | "source"
  | "test"
  | "config"
  | "doc"
  | "metadata"
  | "other";

export interface RepositoryMapFile {
  path: string;
  kind: RepositoryMapFileKind;
}

export interface RepositoryProjectRoot {
  path: string;
  types: string[];
  packageManager?: "npm" | "pnpm" | "yarn" | "bun";
  scripts?: Record<string, string>;
  tsconfig?: string;
  sourceDirs: string[];
  testDirs: string[];
}

export interface RepositoryMapDiff {
  isGitRepo: boolean;
  changedFiles: string[];
  stat: string;
}

export interface RepositoryMapOutput {
  cwd: string;
  projectRoots: RepositoryProjectRoot[];
  files: RepositoryMapFile[];
  currentDiff: RepositoryMapDiff;
  warnings: string[];
  truncated: boolean;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    cwd: {
      type: "string",
      description: "Workspace-relative repository directory to summarize. Default: workspace root.",
    },
    maxFiles: {
      type: "integer",
      minimum: 1,
      maximum: 1000,
      description: "Maximum important files to include. Default: 200.",
    },
    includeDiff: {
      type: "boolean",
      description: "Include git status and diff stat evidence. Default: true.",
    },
  },
} as const;

const DEFAULT_MAX_FILES = 200;
const MAX_FILES = 1000;
const MAX_SCAN_ENTRIES = 8000;

const SKIP_DIRS = new Set([
  ".git",
  ".next",
  ".turbo",
  ".venv",
  "__pycache__",
  "build",
  "coverage",
  "dist",
  "node_modules",
  "target",
]);

const METADATA_FILES = new Set([
  "package.json",
  "tsconfig.json",
  "pyproject.toml",
  "requirements.txt",
  "go.mod",
  "Cargo.toml",
  "package-lock.json",
  "pnpm-lock.yaml",
  "yarn.lock",
  "bun.lock",
  "bun.lockb",
]);

const SOURCE_EXTENSIONS = new Set([
  ".c",
  ".cc",
  ".cpp",
  ".cs",
  ".css",
  ".go",
  ".java",
  ".js",
  ".jsx",
  ".kt",
  ".mjs",
  ".py",
  ".rb",
  ".rs",
  ".scss",
  ".swift",
  ".ts",
  ".tsx",
  ".vue",
]);

const DOC_EXTENSIONS = new Set([".adoc", ".md", ".mdx", ".rst"]);
const CONFIG_EXTENSIONS = new Set([".cjs", ".json", ".mjs", ".toml", ".yaml", ".yml"]);

interface WalkResult {
  files: string[];
  dirs: string[];
  truncated: boolean;
}

function workspaceRelative(root: string, target: string): string {
  const rel = path.relative(root, target);
  return rel.length === 0 ? "." : rel.split(path.sep).join("/");
}

function joinRel(dir: string, name: string): string {
  return dir === "." ? name : `${dir}/${name}`;
}

function fileName(relPath: string): string {
  return relPath.split("/").at(-1) ?? relPath;
}

function isUnderProjectRoot(filePath: string, projectRoot: string): boolean {
  return projectRoot === "." || filePath === projectRoot || filePath.startsWith(`${projectRoot}/`);
}

function isNestedRoot(parent: string, candidate: string): boolean {
  if (candidate === parent) return false;
  return parent === "." ? true : candidate.startsWith(`${parent}/`);
}

async function readText(filePath: string): Promise<string | null> {
  try {
    return await fs.readFile(filePath, "utf8");
  } catch {
    return null;
  }
}

async function walkRepository(root: string, warnings: string[]): Promise<WalkResult> {
  const files: string[] = [];
  const dirs: string[] = ["."];
  let seen = 0;
  let truncated = false;

  async function visit(absDir: string, relDir: string): Promise<void> {
    if (truncated) return;
    let entries: Array<import("node:fs").Dirent>;
    try {
      entries = await fs.readdir(absDir, { withFileTypes: true });
    } catch {
      warnings.push(`Could not read directory: ${relDir}`);
      return;
    }
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      seen += 1;
      if (seen > MAX_SCAN_ENTRIES) {
        truncated = true;
        warnings.push(`Repository scan stopped after ${MAX_SCAN_ENTRIES} entries`);
        return;
      }
      const rel = joinRel(relDir, entry.name);
      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name)) continue;
        dirs.push(rel);
        await visit(path.join(absDir, entry.name), rel);
      } else if (entry.isFile()) {
        files.push(rel);
      }
    }
  }

  await visit(root, ".");
  return {
    files: files.sort(),
    dirs: dirs.sort((a, b) => a.localeCompare(b)),
    truncated,
  };
}

function classifyFile(relPath: string): RepositoryMapFileKind {
  const name = fileName(relPath);
  if (METADATA_FILES.has(name)) return "metadata";
  const lower = relPath.toLowerCase();
  const ext = path.posix.extname(lower);
  const segments = lower.split("/");
  const hasTestSegment = segments.some((segment) =>
    ["__tests__", "test", "tests", "spec", "specs"].includes(segment),
  );
  const isTestFile =
    hasTestSegment ||
    /(?:^|[./_-])(?:test|spec)\.[cm]?[jt]sx?$/.test(lower) ||
    /\.(?:test|spec)\.[cm]?[jt]sx?$/.test(lower);
  if (isTestFile) return "test";
  if (SOURCE_EXTENSIONS.has(ext)) return "source";
  if (DOC_EXTENSIONS.has(ext) || name.toLowerCase().startsWith("readme")) return "doc";
  if (CONFIG_EXTENSIONS.has(ext) || lower.includes("config")) return "config";
  return "other";
}

function detectPackageManager(fileSet: Set<string>, dir: string): RepositoryProjectRoot["packageManager"] {
  if (fileSet.has(joinRel(dir, "pnpm-lock.yaml"))) return "pnpm";
  if (fileSet.has(joinRel(dir, "yarn.lock"))) return "yarn";
  if (fileSet.has(joinRel(dir, "bun.lock")) || fileSet.has(joinRel(dir, "bun.lockb"))) return "bun";
  if (fileSet.has(joinRel(dir, "package-lock.json"))) return "npm";
  return undefined;
}

async function readScripts(absProjectRoot: string): Promise<Record<string, string> | undefined> {
  const raw = await readText(path.join(absProjectRoot, "package.json"));
  if (raw === null) return undefined;
  let parsed: { scripts?: Record<string, unknown> };
  try {
    parsed = JSON.parse(raw) as { scripts?: Record<string, unknown> };
  } catch {
    return undefined;
  }
  const scripts: Record<string, string> = {};
  const entries = Object.entries(parsed.scripts ?? {}).sort(([a], [b]) => a.localeCompare(b));
  for (const [name, command] of entries) {
    if (typeof command === "string") scripts[name] = command;
  }
  return Object.keys(scripts).length > 0 ? scripts : undefined;
}

function dirsForKind(input: {
  files: RepositoryMapFile[];
  projectRoot: string;
  projectRoots: string[];
  kind: "source" | "test";
}): string[] {
  const dirs = new Set<string>();
  const nestedPrefixes = input.projectRoots
    .filter((candidate) => isNestedRoot(input.projectRoot, candidate))
    .map((candidate) => `${candidate}/`);

  for (const file of input.files) {
    if (file.kind !== input.kind) continue;
    if (!isUnderProjectRoot(file.path, input.projectRoot)) continue;
    if (nestedPrefixes.some((prefix) => file.path.startsWith(prefix))) continue;
    const dir = path.posix.dirname(file.path);
    if (dir !== ".") dirs.add(dir);
  }
  return [...dirs].sort((a, b) => a.localeCompare(b));
}

async function detectProjectRoots(input: {
  cwd: string;
  files: RepositoryMapFile[];
  dirs: string[];
}): Promise<RepositoryProjectRoot[]> {
  const fileSet = new Set(input.files.map((file) => file.path));
  const rootDirs = input.dirs.filter((dir) => {
    return (
      fileSet.has(joinRel(dir, "package.json")) ||
      fileSet.has(joinRel(dir, "tsconfig.json")) ||
      fileSet.has(joinRel(dir, "pyproject.toml")) ||
      fileSet.has(joinRel(dir, "requirements.txt")) ||
      fileSet.has(joinRel(dir, "go.mod")) ||
      fileSet.has(joinRel(dir, "Cargo.toml"))
    );
  });

  const sortedRootDirs = rootDirs.sort((a, b) => {
    if (a === ".") return -1;
    if (b === ".") return 1;
    return a.localeCompare(b);
  });

  const roots: RepositoryProjectRoot[] = [];
  for (const dir of sortedRootDirs) {
    const types: string[] = [];
    if (fileSet.has(joinRel(dir, "package.json"))) types.push("node");
    if (fileSet.has(joinRel(dir, "tsconfig.json"))) types.push("typescript");
    if (fileSet.has(joinRel(dir, "pyproject.toml")) || fileSet.has(joinRel(dir, "requirements.txt"))) {
      types.push("python");
    }
    if (fileSet.has(joinRel(dir, "go.mod"))) types.push("go");
    if (fileSet.has(joinRel(dir, "Cargo.toml"))) types.push("rust");

    const absProjectRoot = path.join(input.cwd, dir === "." ? "" : dir);
    const scripts = await readScripts(absProjectRoot);
    const packageManager = detectPackageManager(fileSet, dir);
    const projectRootPaths = sortedRootDirs;
    const root: RepositoryProjectRoot = {
      path: dir,
      types,
      sourceDirs: dirsForKind({
        files: input.files,
        projectRoot: dir,
        projectRoots: projectRootPaths,
        kind: "source",
      }),
      testDirs: dirsForKind({
        files: input.files,
        projectRoot: dir,
        projectRoots: projectRootPaths,
        kind: "test",
      }),
    };
    if (packageManager !== undefined) root.packageManager = packageManager;
    if (scripts !== undefined) root.scripts = scripts;
    if (fileSet.has(joinRel(dir, "tsconfig.json"))) root.tsconfig = joinRel(dir, "tsconfig.json");
    roots.push(root);
  }
  return roots;
}

function parseChangedFiles(status: string): string[] {
  const files = new Set<string>();
  for (const line of status.split("\n")) {
    if (!line.trim()) continue;
    const raw = line.slice(3).trim();
    if (!raw) continue;
    const renamed = raw.includes(" -> ") ? raw.split(" -> ").at(-1) ?? raw : raw;
    files.add(renamed.replace(/^"|"$/g, ""));
  }
  return [...files].sort((a, b) => a.localeCompare(b));
}

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await execFileAsync("git", args, {
    cwd,
    maxBuffer: 2 * 1024 * 1024,
  });
  return stdout;
}

async function readCurrentDiff(
  cwd: string,
  includeDiff: boolean,
  warnings: string[],
): Promise<RepositoryMapDiff> {
  if (!includeDiff) return { isGitRepo: false, changedFiles: [], stat: "" };
  try {
    const inside = (await git(cwd, ["rev-parse", "--is-inside-work-tree"])).trim();
    if (inside !== "true") return { isGitRepo: false, changedFiles: [], stat: "" };
    const [status, stat] = await Promise.all([
      git(cwd, ["status", "--porcelain", "--untracked-files=all"]),
      git(cwd, ["diff", "--stat", "--no-ext-diff"]),
    ]);
    return {
      isGitRepo: true,
      changedFiles: parseChangedFiles(status),
      stat,
    };
  } catch {
    warnings.push("Git status could not be read; currentDiff was omitted");
    return { isGitRepo: false, changedFiles: [], stat: "" };
  }
}

function maxFiles(input: RepositoryMapInput): number {
  const requested = input.maxFiles ?? DEFAULT_MAX_FILES;
  return Math.min(MAX_FILES, Math.max(1, requested));
}

export function makeRepositoryMapTool(
  workspaceRoot: string,
): Tool<RepositoryMapInput, RepositoryMapOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "RepositoryMap",
    description:
      "Build a compact, read-only repository orientation map for coding work: project roots, scripts, source/test dirs, important files, and current git status. Use before non-trivial edits so the agent understands the repo shape without ad hoc shell exploration.",
    inputSchema: INPUT_SCHEMA,
    permission: "read",
    kind: "core",
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    validate(input) {
      if (!input) return null;
      if (input.cwd !== undefined && typeof input.cwd !== "string") return "`cwd` must be a string";
      if (
        input.maxFiles !== undefined &&
        (!Number.isInteger(input.maxFiles) || input.maxFiles < 1 || input.maxFiles > MAX_FILES)
      ) {
        return "`maxFiles` must be an integer between 1 and 1000";
      }
      if (input.includeDiff !== undefined && typeof input.includeDiff !== "boolean") {
        return "`includeDiff` must be a boolean";
      }
      return null;
    },
    async execute(input: RepositoryMapInput, ctx: ToolContext): Promise<ToolResult<RepositoryMapOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const cwd = input.cwd ? ws.resolve(input.cwd) : ws.root;
        const cwdRel = workspaceRelative(ws.root, cwd);
        const warnings: string[] = [];
        const walk = await walkRepository(cwd, warnings);
        const files = walk.files.map((relPath) => ({
          path: relPath,
          kind: classifyFile(relPath),
        }));
        const cappedFiles = files.slice(0, maxFiles(input));
        const projectRoots = await detectProjectRoots({
          cwd,
          dirs: walk.dirs,
          files,
        });
        const currentDiff = await readCurrentDiff(cwd, input.includeDiff !== false, warnings);

        const output: RepositoryMapOutput = {
          cwd: cwdRel,
          projectRoots,
          files: cappedFiles,
          currentDiff,
          warnings,
          truncated: walk.truncated || cappedFiles.length < files.length,
        };

        return {
          status: "ok",
          output,
          durationMs: Date.now() - start,
          metadata: {
            evidenceKind: "repository_map",
            projectRootCount: projectRoots.length,
            fileCount: files.length,
            changedFileCount: currentDiff.changedFiles.length,
          },
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
