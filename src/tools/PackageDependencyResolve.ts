import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";

type PackageManager = "npm";
type DependencyType =
  | "dependencies"
  | "devDependencies"
  | "peerDependencies"
  | "optionalDependencies";

export interface PackageDependencyResolveInput {
  packageName?: string;
  manager?: PackageManager;
  manifestPath?: string;
  lockfilePath?: string;
  includeRegistry?: boolean;
  timeoutMs?: number;
}

export interface PackageDependencyCacheHint {
  tool: "ExternalSourceCache";
  action: "ensure_repo" | "ensure_url";
  url: string;
  format?: "markdown";
}

export interface PackageDependencyResolveOutput {
  manager: PackageManager;
  packageName: string;
  manifestPath: string;
  lockfilePath?: string;
  dependencyType?: DependencyType;
  requestedRange?: string;
  lockedVersion?: string;
  registryFetched: boolean;
  registry?: {
    url: string;
    version?: string;
  };
  repositoryUrl?: string;
  docsUrl?: string;
  cacheHints: PackageDependencyCacheHint[];
}

export interface PackageDependencyRegistryRun {
  packageName: string;
  url: string;
  timeoutMs: number;
  signal: AbortSignal;
}

export interface PackageDependencyRegistryResult {
  statusCode: number;
  url: string;
  body: string;
}

export type PackageDependencyRegistryRunner = (
  run: PackageDependencyRegistryRun,
) => Promise<PackageDependencyRegistryResult>;

interface PackageDependencyResolveOptions {
  registryRunner?: PackageDependencyRegistryRunner;
}

interface PackageJson {
  dependencies?: Record<string, unknown>;
  devDependencies?: Record<string, unknown>;
  peerDependencies?: Record<string, unknown>;
  optionalDependencies?: Record<string, unknown>;
}

interface NpmLockfile {
  packages?: Record<string, unknown>;
  dependencies?: Record<string, unknown>;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    packageName: {
      type: "string",
      description: "npm package name to resolve, for example react or @scope/pkg.",
    },
    manager: {
      type: "string",
      enum: ["npm"],
      description: "Package manager family. Currently npm package.json/package-lock.json.",
    },
    manifestPath: {
      type: "string",
      description: "Workspace-relative manifest path. Defaults to package.json.",
    },
    lockfilePath: {
      type: "string",
      description: "Workspace-relative npm lockfile path. Defaults to package-lock.json.",
    },
    includeRegistry: {
      type: "boolean",
      description: "Fetch npm registry metadata. Defaults to true.",
    },
    timeoutMs: {
      type: "integer",
      minimum: 100,
      maximum: 120000,
      description: "Registry fetch timeout in ms. Defaults to 30000.",
    },
  },
  required: ["packageName"],
  additionalProperties: false,
} as const;

const DEPENDENCY_TYPES: DependencyType[] = [
  "dependencies",
  "devDependencies",
  "peerDependencies",
  "optionalDependencies",
];
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 120_000;
const MAX_REGISTRY_BYTES = 2 * 1024 * 1024;

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
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

function relativeToRoot(root: string, absPath: string): string {
  return path.relative(path.resolve(root), absPath).split(path.sep).join("/");
}

function normalizeTimeout(timeoutMs: unknown): number {
  if (typeof timeoutMs !== "number" || !Number.isFinite(timeoutMs)) return DEFAULT_TIMEOUT_MS;
  return Math.max(100, Math.min(MAX_TIMEOUT_MS, Math.trunc(timeoutMs)));
}

function validationError(
  errorCode: string,
  errorMessage: string,
  start: number,
): ToolResult<PackageDependencyResolveOutput> {
  return {
    status: "error",
    errorCode,
    errorMessage,
    durationMs: Date.now() - start,
  };
}

async function readJsonFile<T>(
  filePath: string,
  label: string,
): Promise<{ ok: true; value: T } | { ok: false; errorCode: string; errorMessage: string }> {
  try {
    return { ok: true, value: JSON.parse(await fs.readFile(filePath, "utf8")) as T };
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return { ok: false, errorCode: "not_found", errorMessage: `${label} not found` };
    }
    if (err instanceof SyntaxError) {
      return { ok: false, errorCode: "invalid_json", errorMessage: `${label} is not valid JSON` };
    }
    throw err;
  }
}

function dependencyEntry(
  manifest: PackageJson,
  packageName: string,
): { dependencyType?: DependencyType; requestedRange?: string } {
  for (const dependencyType of DEPENDENCY_TYPES) {
    const value = manifest[dependencyType]?.[packageName];
    if (typeof value === "string") return { dependencyType, requestedRange: value };
  }
  return {};
}

function lockPackageKey(packageName: string): string {
  return `node_modules/${packageName}`;
}

function stringProperty(record: unknown, key: string): string | undefined {
  if (!record || typeof record !== "object") return undefined;
  const value = (record as Record<string, unknown>)[key];
  return typeof value === "string" ? value : undefined;
}

function lockedVersion(lockfile: NpmLockfile | null, packageName: string): string | undefined {
  if (!lockfile) return undefined;
  const packagesEntry = lockfile.packages?.[lockPackageKey(packageName)];
  const packagesVersion = stringProperty(packagesEntry, "version");
  if (packagesVersion) return packagesVersion;
  const dependencyEntry = lockfile.dependencies?.[packageName];
  return stringProperty(dependencyEntry, "version");
}

function registryUrl(packageName: string): string {
  if (packageName.startsWith("@") && packageName.includes("/")) {
    const [scope, name] = packageName.split("/", 2);
    return `https://registry.npmjs.org/${scope}%2f${encodeURIComponent(name ?? "")}`;
  }
  return `https://registry.npmjs.org/${encodeURIComponent(packageName)}`;
}

async function defaultRegistryRunner(
  run: PackageDependencyRegistryRun,
): Promise<PackageDependencyRegistryResult> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), run.timeoutMs);
  run.signal.addEventListener("abort", () => controller.abort(), { once: true });
  try {
    const response = await fetch(run.url, {
      redirect: "follow",
      signal: controller.signal,
      headers: {
        Accept: "application/vnd.npm.install-v1+json,application/json",
        "User-Agent": "MagiResearchAgent/1.0",
      },
    });
    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.byteLength > MAX_REGISTRY_BYTES) {
      throw new Error(`npm registry response exceeded ${MAX_REGISTRY_BYTES} bytes`);
    }
    return {
      statusCode: response.status,
      url: run.url,
      body: buffer.toString("utf8"),
    };
  } finally {
    clearTimeout(timeout);
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function selectedRegistryVersion(
  metadata: Record<string, unknown>,
  preferredVersion: string | undefined,
): { version?: string; record?: Record<string, unknown> } {
  const versions = asRecord(metadata.versions);
  if (preferredVersion && versions) {
    const exact = asRecord(versions[preferredVersion]);
    if (exact) return { version: preferredVersion, record: exact };
  }
  const distTags = asRecord(metadata["dist-tags"]);
  const latest = typeof distTags?.latest === "string" ? distTags.latest : undefined;
  if (latest && versions) {
    const latestRecord = asRecord(versions[latest]);
    if (latestRecord) return { version: latest, record: latestRecord };
  }
  return {};
}

function normalizeRepositoryUrl(value: unknown): string | undefined {
  const raw = typeof value === "string"
    ? value
    : typeof asRecord(value)?.url === "string"
      ? asRecord(value)?.url as string
      : undefined;
  if (!raw) return undefined;
  let normalized = raw.trim().replace(/^git\+/, "");
  if (normalized.startsWith("git://github.com/")) {
    normalized = `https://github.com/${normalized.slice("git://github.com/".length)}`;
  }
  const sshMatch = /^git@github\.com:([^/]+\/[^/]+?)(?:\.git)?$/.exec(normalized);
  if (sshMatch) normalized = `https://github.com/${sshMatch[1]}.git`;
  const sshUrlMatch = /^ssh:\/\/git@github\.com\/([^/]+\/[^/]+?)(?:\.git)?$/.exec(normalized);
  if (sshUrlMatch) normalized = `https://github.com/${sshUrlMatch[1]}.git`;
  try {
    const parsed = new URL(normalized);
    if (parsed.protocol !== "https:" || parsed.hostname.toLowerCase() !== "github.com") {
      return undefined;
    }
    return parsed.toString();
  } catch {
    return undefined;
  }
}

function normalizeDocsUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.toString() : undefined;
  } catch {
    return undefined;
  }
}

function buildCacheHints(
  repositoryUrl: string | undefined,
  docsUrl: string | undefined,
): PackageDependencyCacheHint[] {
  const hints: PackageDependencyCacheHint[] = [];
  if (repositoryUrl) {
    hints.push({
      tool: "ExternalSourceCache",
      action: "ensure_repo",
      url: repositoryUrl,
    });
  }
  if (docsUrl) {
    hints.push({
      tool: "ExternalSourceCache",
      action: "ensure_url",
      url: docsUrl,
      format: "markdown",
    });
  }
  return hints;
}

function validateInput(input: unknown): string | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return "`input` must be an object";
  }
  const packageName = stringValue((input as PackageDependencyResolveInput).packageName);
  if (!packageName) return "`packageName` is required";
  const manager = (input as PackageDependencyResolveInput).manager;
  if (manager !== undefined && manager !== "npm") return "`manager` must be npm";
  return null;
}

export function makePackageDependencyResolveTool(
  opts: PackageDependencyResolveOptions = {},
): Tool<PackageDependencyResolveInput, PackageDependencyResolveOutput> {
  const registryRunner = opts.registryRunner ?? defaultRegistryRunner;
  return {
    name: "PackageDependencyResolve",
    description:
      "Resolve an npm dependency from package.json/package-lock.json to its locked version, registry metadata, and ExternalSourceCache repo/docs hints for dependency research.",
    inputSchema: INPUT_SCHEMA,
    permission: "net",
    dangerous: false,
    mutatesWorkspace: false,
    isConcurrencySafe: true,
    tags: ["research", "dependencies", "npm", "external", "cache"],
    validate(input) {
      return validateInput(input);
    },
    async execute(
      input: PackageDependencyResolveInput,
      ctx: ToolContext,
    ): Promise<ToolResult<PackageDependencyResolveOutput>> {
      const start = Date.now();
      const validation = validateInput(input);
      if (validation) return validationError("invalid_input", validation, start);

      const packageName = stringValue(input.packageName) ?? "";
      const manager: PackageManager = input.manager ?? "npm";
      const manifestRel = stringValue(input.manifestPath) ?? "package.json";
      const lockfileRel = stringValue(input.lockfilePath) ?? "package-lock.json";
      const manifestAbs = resolveInside(ctx.workspaceRoot, manifestRel);
      const lockfileAbs = resolveInside(ctx.workspaceRoot, lockfileRel);
      if (!manifestAbs) {
        return validationError("path_escape", `manifest path escapes workspace: ${manifestRel}`, start);
      }
      if (!lockfileAbs) {
        return validationError("path_escape", `lockfile path escapes workspace: ${lockfileRel}`, start);
      }

      try {
        const manifestResult = await readJsonFile<PackageJson>(manifestAbs, "package manifest");
        if (!manifestResult.ok) {
          return validationError(manifestResult.errorCode, manifestResult.errorMessage, start);
        }
        const lockfileResult = await readJsonFile<NpmLockfile>(lockfileAbs, "package lockfile");
        if (!lockfileResult.ok && lockfileResult.errorCode !== "not_found") {
          return validationError(lockfileResult.errorCode, lockfileResult.errorMessage, start);
        }
        const lockfile = lockfileResult.ok ? lockfileResult.value : null;
        const dep = dependencyEntry(manifestResult.value, packageName);
        const version = lockedVersion(lockfile, packageName);
        const output: PackageDependencyResolveOutput = {
          manager,
          packageName,
          manifestPath: relativeToRoot(ctx.workspaceRoot, manifestAbs),
          ...(lockfileResult.ok
            ? { lockfilePath: relativeToRoot(ctx.workspaceRoot, lockfileAbs) }
            : {}),
          ...(dep.dependencyType ? { dependencyType: dep.dependencyType } : {}),
          ...(dep.requestedRange ? { requestedRange: dep.requestedRange } : {}),
          ...(version ? { lockedVersion: version } : {}),
          registryFetched: false,
          cacheHints: [],
        };

        if (input.includeRegistry === false) {
          return {
            status: "ok",
            output,
            durationMs: Date.now() - start,
          };
        }

        const url = registryUrl(packageName);
        const registryResult = await registryRunner({
          packageName,
          url,
          timeoutMs: normalizeTimeout(input.timeoutMs),
          signal: ctx.abortSignal,
        });
        if (registryResult.statusCode < 200 || registryResult.statusCode >= 400) {
          return validationError(
            "registry_fetch_failed",
            `npm registry returned HTTP ${registryResult.statusCode}`,
            start,
          );
        }
        const metadata = JSON.parse(registryResult.body) as Record<string, unknown>;
        const selected = selectedRegistryVersion(metadata, version);
        const repositoryUrl = normalizeRepositoryUrl(selected.record?.repository ?? metadata.repository);
        const docsUrl = normalizeDocsUrl(selected.record?.homepage ?? metadata.homepage);

        return {
          status: "ok",
          output: {
            ...output,
            registryFetched: true,
            registry: {
              url,
              ...(selected.version ? { version: selected.version } : {}),
            },
            ...(repositoryUrl ? { repositoryUrl } : {}),
            ...(docsUrl ? { docsUrl } : {}),
            cacheHints: buildCacheHints(repositoryUrl, docsUrl),
          },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        if (err instanceof SyntaxError) {
          return validationError("invalid_registry_json", "npm registry response is not valid JSON", start);
        }
        return errorResult(err, start);
      }
    },
  };
}
