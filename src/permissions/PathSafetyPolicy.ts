import os from "node:os";
import path from "node:path";
import fs from "node:fs";

export type PathSafetyClass =
  | "workspace_safe"
  | "outside_workspace"
  | "secret_path"
  | "sealed_file"
  | "system_path";

export interface PathSafetyResult {
  classification: PathSafetyClass;
  reason?: string;
  resolvedPath: string;
}

const SEALED_FILES = new Set([
  "SOUL.md",
  "TOOLS.md",
  "AGENTS.md",
  "CLAUDE.md",
  "HEARTBEAT.md",
  "LEARNING.md",
]);

const SECRET_PATTERNS = [
  /^\.env(?:\.|$)/,
  /^id_rsa$/,
  /^id_dsa$/,
  /^id_ed25519$/,
  /\.pem$/,
  /\.key$/,
  /token/i,
  /secret/i,
  /credential/i,
];

export function classifyPathSafety(input: {
  workspaceRoot: string;
  filePath: string;
  operation: "read" | "write";
  allowWorkspaceSecretPaths?: boolean;
}): PathSafetyResult {
  const workspaceRoot = safeRealpath(path.resolve(input.workspaceRoot));
  const raw = input.filePath.startsWith("~")
    ? path.join(os.homedir(), input.filePath.slice(1))
    : input.filePath;
  const resolved = path.isAbsolute(raw)
    ? path.resolve(raw)
    : path.resolve(workspaceRoot, raw);
  const canonical = canonicalizeForPolicy(resolved);

  if (isSystemPath(canonical, workspaceRoot)) {
    return {
      classification: "system_path",
      reason: "system path access is not allowed",
      resolvedPath: canonical,
    };
  }

  if (!isInside(canonical, workspaceRoot)) {
    return {
      classification: "outside_workspace",
      reason: "path escapes the workspace",
      resolvedPath: canonical,
    };
  }

  const base = path.basename(canonical);
  if (input.operation === "write" && SEALED_FILES.has(base)) {
    return {
      classification: "sealed_file",
      reason: `sealed file cannot be modified: ${base}`,
      resolvedPath: canonical,
    };
  }

  if (!input.allowWorkspaceSecretPaths && isSecretLike(canonical)) {
    return {
      classification: "secret_path",
      reason: "secret-like path access is not allowed",
      resolvedPath: canonical,
    };
  }

  return { classification: "workspace_safe", resolvedPath: canonical };
}

function canonicalizeForPolicy(absPath: string): string {
  if (fs.existsSync(absPath)) return safeRealpath(absPath);
  const parent = path.dirname(absPath);
  if (fs.existsSync(parent)) {
    return path.join(safeRealpath(parent), path.basename(absPath));
  }
  return path.resolve(absPath);
}

function safeRealpath(p: string): string {
  try {
    return fs.realpathSync(p);
  } catch {
    return path.resolve(p);
  }
}

function isInside(candidate: string, root: string): boolean {
  const rel = path.relative(root, candidate);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

function isSystemPath(candidate: string, workspaceRoot: string): boolean {
  if (isInside(candidate, workspaceRoot)) return false;
  return (
    candidate === "/" ||
    candidate.startsWith("/etc/") ||
    candidate.startsWith("/var/") ||
    candidate.startsWith("/bin/") ||
    candidate.startsWith("/sbin/") ||
    candidate.startsWith("/usr/") ||
    candidate.startsWith("/System/") ||
    candidate.startsWith(path.join(os.homedir(), ".ssh"))
  );
}

function isSecretLike(candidate: string): boolean {
  return candidate
    .split(path.sep)
    .some((segment) => SECRET_PATTERNS.some((pattern) => pattern.test(segment)));
}
