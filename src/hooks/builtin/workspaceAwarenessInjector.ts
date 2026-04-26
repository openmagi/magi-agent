/**
 * Workspace awareness injector (Layer 2 of the meta-cognitive
 * scaffolding — docs/plans/2026-04-20-agent-self-model-design.md).
 *
 * Injects a compact `<workspace_snapshot>` block at beforeLLMCall so
 * the bot can see (a) what top-level directories / markdown files
 * live in its workspace and (b) which files have been modified in
 * the last 3 days. Kills the "I don't remember working on X" mode
 * when X actually lives one `Glob` call away.
 *
 * Priority 7 — after agentSelfModel (0), identity (1),
 * sessionResume (2), midTurn (3), memory (5), but before discipline
 * (10+). This places the snapshot close to the task-specific tail of
 * the system prompt while letting identity + memory blocks sit on top.
 *
 * Caching: 5-minute TTL per realpath(workspaceRoot). Avoids re-walking
 * the filesystem on every iteration of the same turn (and back-to-back
 * turns). Module-level Map; entries age out on access.
 *
 * Fail-open: any filesystem / encoding / cache error is logged and the
 * turn continues without the snapshot. Snapshot is a nudge, not a
 * correctness gate.
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { RegisteredHook, HookContext } from "../types.js";

/** Max entries in the recently-modified list. */
const MAX_RECENT_FILES = 30;
/** mtime cutoff for "recent" activity, in days. */
const RECENT_DAYS = 3;
/** Cache TTL. */
const CACHE_TTL_MS = 5 * 60 * 1000;
/** Soft cap on fence size (bytes, UTF-8). ~1500 tokens @ 4 bytes/token. */
const MAX_BYTES = 6_000;
/** Directories we never descend into. */
const EXCLUDED_DIR_NAMES = new Set([
  "node_modules",
  ".git",
  ".DS_Store",
  "dist",
  "build",
  ".next",
  ".cache",
]);

interface SnapshotCacheEntry {
  fence: string;
  expiresAt: number;
}

const cache = new Map<string, SnapshotCacheEntry>();

/** Exported for tests to reset state between runs. */
export function _clearWorkspaceAwarenessCache(): void {
  cache.clear();
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_WORKSPACE_AWARENESS;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

interface TopLevelEntry {
  name: string;
  isDir: boolean;
}

async function listTopLevel(
  workspaceRoot: string,
): Promise<TopLevelEntry[]> {
  let dirents;
  try {
    dirents = await fs.readdir(workspaceRoot, { withFileTypes: true });
  } catch {
    return [];
  }
  const out: TopLevelEntry[] = [];
  for (const d of dirents) {
    if (EXCLUDED_DIR_NAMES.has(d.name)) continue;
    if (d.name.startsWith(".") && !d.name.endsWith(".md")) {
      // Skip dotfiles except markdown (e.g. keep SCRATCHPAD.md, hide
      // .core-agent-state). This mirrors the design doc's intent.
      continue;
    }
    if (d.isDirectory()) {
      out.push({ name: d.name, isDir: true });
    } else if (d.isFile() && d.name.toLowerCase().endsWith(".md")) {
      out.push({ name: d.name, isDir: false });
    }
  }
  // Deterministic order: dirs first (alphabetical), then .md files.
  out.sort((a, b) => {
    if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return out;
}

interface RecentFile {
  relPath: string;
  mtimeMs: number;
}

async function listRecentFiles(
  workspaceRoot: string,
  cutoffMs: number,
): Promise<RecentFile[]> {
  const out: RecentFile[] = [];

  async function walk(dir: string, relPrefix: string): Promise<void> {
    let dirents;
    try {
      dirents = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const d of dirents) {
      if (EXCLUDED_DIR_NAMES.has(d.name)) continue;
      const abs = path.join(dir, d.name);
      const rel = relPrefix ? `${relPrefix}/${d.name}` : d.name;
      if (d.isDirectory()) {
        // eslint-disable-next-line no-await-in-loop
        await walk(abs, rel);
      } else if (d.isFile()) {
        try {
          // eslint-disable-next-line no-await-in-loop
          const st = await fs.stat(abs);
          if (st.mtimeMs >= cutoffMs) {
            out.push({ relPath: rel, mtimeMs: st.mtimeMs });
          }
        } catch {
          // ignore unreadable entry
        }
      }
    }
  }

  await walk(workspaceRoot, "");
  out.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return out.slice(0, MAX_RECENT_FILES);
}

export interface BuildSnapshotResult {
  fence: string;
  topLevelCount: number;
  recentCount: number;
}

/**
 * Exported for tests — composes the `<workspace_snapshot>` fence.
 * Returns an empty fence when the workspace has no interesting
 * content (nothing at top-level AND no recent activity), so the hook
 * can no-op instead of injecting a useless shell.
 */
export async function buildWorkspaceSnapshot(
  workspaceRoot: string,
  nowMs: number = Date.now(),
): Promise<BuildSnapshotResult> {
  const topLevel = await listTopLevel(workspaceRoot);
  const cutoff = nowMs - RECENT_DAYS * 24 * 60 * 60 * 1000;
  const recent = await listRecentFiles(workspaceRoot, cutoff);

  if (topLevel.length === 0 && recent.length === 0) {
    return { fence: "", topLevelCount: 0, recentCount: 0 };
  }

  const refreshedAt = new Date(nowMs).toISOString();
  const lines: string[] = [];
  lines.push(`<workspace_snapshot refreshedAt="${refreshedAt}">`);
  lines.push("## Top-level workspace entries");
  if (topLevel.length === 0) {
    lines.push("- (empty)");
  } else {
    for (const e of topLevel) {
      lines.push(`- ${e.name}${e.isDir ? "/" : ""}`);
    }
  }

  lines.push("");
  lines.push(`## Recently modified files (last ${RECENT_DAYS} days, max ${MAX_RECENT_FILES})`);
  if (recent.length === 0) {
    lines.push("- (none)");
  } else {
    for (const r of recent) {
      const iso = new Date(r.mtimeMs).toISOString();
      lines.push(`- ${r.relPath}  (mtime: ${iso})`);
    }
  }

  lines.push("</workspace_snapshot>");

  let fence = lines.join("\n");
  if (Buffer.byteLength(fence, "utf8") > MAX_BYTES) {
    // Truncate trailing recent-file lines until under budget.
    while (
      lines.length > 3 &&
      Buffer.byteLength(lines.join("\n"), "utf8") > MAX_BYTES
    ) {
      // Drop the last recent-file line (but keep closing tag).
      lines.splice(lines.length - 2, 1);
    }
    fence = lines.join("\n");
  }

  return {
    fence,
    topLevelCount: topLevel.length,
    recentCount: recent.length,
  };
}

async function resolveCacheKey(workspaceRoot: string): Promise<string> {
  try {
    return await fs.realpath(workspaceRoot);
  } catch {
    // If realpath fails, fall back to the path string. The hook will
    // likely no-op on the next readdir anyway.
    return workspaceRoot;
  }
}

export interface WorkspaceAwarenessOptions {
  workspaceRoot: string;
}

export function makeWorkspaceAwarenessHook(
  opts: WorkspaceAwarenessOptions,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:workspace-awareness",
    point: "beforeLLMCall",
    priority: 7,
    blocking: false,
    handler: async (args, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (args.iteration > 0) return { action: "continue" };
        if (args.system.includes("<workspace_snapshot")) {
          return { action: "continue" };
        }

        // Fast existence check — avoid realpath on a missing root.
        try {
          const st = await fs.stat(opts.workspaceRoot);
          if (!st.isDirectory()) return { action: "continue" };
        } catch {
          return { action: "continue" };
        }

        const key = await resolveCacheKey(opts.workspaceRoot);
        const now = Date.now();
        const cached = cache.get(key);
        let fence: string;
        if (cached && cached.expiresAt > now) {
          fence = cached.fence;
        } else {
          const built = await buildWorkspaceSnapshot(opts.workspaceRoot, now);
          fence = built.fence;
          cache.set(key, { fence, expiresAt: now + CACHE_TTL_MS });
        }

        if (!fence) return { action: "continue" };

        const nextSystem = args.system
          ? `${args.system}\n\n${fence}`
          : fence;
        return {
          action: "replace",
          value: { ...args, system: nextSystem },
        };
      } catch (err) {
        ctx.log("warn", "[workspace-awareness] inject failed; turn continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
