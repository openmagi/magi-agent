/**
 * Built-in sealed-files integrity hook — T3-12 (Phase 3 / OMC Port C).
 *
 * Design reference:
 * - `docs/plans/2026-04-19-core-agent-phase-3-plan.md` §5 / T3-12
 * - `docs/notes/2026-04-19-omc-self-improve-port-analysis.md` Port C
 *
 * Blocks a commit when files listed under `agent.config.yaml →
 * sealed_files` were mutated during the turn without an explicit
 * bypass. The hook stores a manifest of sha256 hashes under
 * `workspace/.sealed-manifest.json` (atomically written) and diffs the
 * current state against it on each commit attempt.
 *
 * Two bypass mechanisms:
 *   1. Config-level allowlist — `agent.config.yaml →
 *      sealed_files_allowlist_turns: [turnId, ...]`. Rarely used; for
 *      offline admin flips.
 *   2. Turn-level explicit intent — the user message contains
 *      `[UNSEAL: <glob>]`. Only paths matching `<glob>` can be changed
 *      this turn. Multiple UNSEAL markers may appear.
 *
 * Bypass outcomes are emitted as `rule_check` audit events with
 * `detail` starting "sealed_files_bypass". Violations fire
 * `detail` starting "sealed_files_violation". First-run manifest
 * initialisation fires `detail` starting "sealed_manifest_initialized".
 *
 * Toggle: `CORE_AGENT_SEALED_FILES=off` disables globally.
 * `disable_builtin_hooks: [builtin:sealed-files]` disables per-bot.
 */

import fs from "node:fs/promises";
import crypto from "node:crypto";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import { atomicWriteFile } from "../../storage/atomicWrite.js";
import type { RegisteredHook, HookContext } from "../types.js";
import {
  recordFailure as recordCircuitFailure,
  signatureFor as circuitSignatureFor,
} from "./repeatedFailureGuard.js";

export const DEFAULT_SEALED_GLOBS: readonly string[] = [
  "SOUL.md",
  "AGENTS.md",
  "TOOLS.md",
  "CLAUDE.md",
  "HEARTBEAT.md",
  "LEARNING.md",
  "identity.md",
  "rules.md",
  "soul.md",
  "skills/*/SKILL.md",
  "memory/ROOT.md",
  "agent.config.yaml",
];

const MANIFEST_REL = ".sealed-manifest.json";
const CONFIG_REL = "agent.config.yaml";

interface ManifestEntry {
  sha256: string;
  updatedAt: number;
}

type Manifest = Record<string, ManifestEntry>;

interface PendingUpdate {
  path: string;
  sha256: string;
}

/**
 * Per-turn state shared between beforeCommit (which computes the diff
 * + allow/block decision) and afterCommit (which persists the
 * allowed-change hashes back to the manifest). Keyed by turnId because
 * the two hooks fire in sequence on the same turn.
 */
const PENDING_UPDATES_BY_TURN = new Map<string, PendingUpdate[]>();
const SYSTEM_ALLOWED_UPDATES_BY_TURN = new Map<string, Set<string>>();

/**
 * Sealed files can already differ from the manifest before a turn
 * starts, for example after PVC restore, init-container sync, or an
 * operator-side workspace update. Snapshot that drift so beforeCommit
 * only blocks changes made after this turn began.
 */
const PREEXISTING_DRIFT_BY_TURN = new Map<string, Map<string, string>>();

function normaliseRelPath(relPath: string): string {
  return relPath.replace(/\\/g, "/").replace(/^\.\//, "").replace(/^\/+/, "");
}

export function allowSealedFileUpdateForTurn(turnId: string, relPath: string): void {
  const normalised = normaliseRelPath(relPath);
  if (!turnId || normalised.length === 0) return;
  const paths = SYSTEM_ALLOWED_UPDATES_BY_TURN.get(turnId) ?? new Set<string>();
  paths.add(normalised);
  SYSTEM_ALLOWED_UPDATES_BY_TURN.set(turnId, paths);
}

export async function recordSystemSealedFileUpdate(
  workspaceRoot: string,
  relPath: string,
): Promise<boolean> {
  if (!isEnabledByEnv()) return false;
  const normalised = normaliseRelPath(relPath);
  if (!workspaceRoot || normalised.length === 0) return false;

  const config = await readConfig(workspaceRoot);
  const globs = resolveSealedGlobs(config);
  if (!matchesAnyGlob(normalised, globs)) return false;

  const sha = await sha256OfFile(path.join(workspaceRoot, normalised));
  if (sha === null) return false;

  const manifest = await readManifest(workspaceRoot);
  const now = Date.now();
  if (!manifest) {
    const diff = await computeDiff(workspaceRoot, globs, null);
    diff.currentHashes[normalised] = { sha256: sha, updatedAt: now };
    await writeManifestAtomic(workspaceRoot, diff.currentHashes);
    return true;
  }

  manifest[normalised] = { sha256: sha, updatedAt: now };
  await writeManifestAtomic(workspaceRoot, manifest);
  return true;
}

function isEnabledByEnv(): boolean {
  const raw = process.env.CORE_AGENT_SEALED_FILES;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  if (v === "" || v === "on" || v === "true" || v === "1") return true;
  return false;
}

async function readConfig(workspaceRoot: string): Promise<Record<string, unknown> | null> {
  const configPath = path.join(workspaceRoot, CONFIG_REL);
  let raw: string;
  try {
    raw = await fs.readFile(configPath, "utf8");
  } catch {
    return null;
  }
  try {
    const parsed = parseYaml(raw);
    if (parsed && typeof parsed === "object") {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

function resolveSealedGlobs(config: Record<string, unknown> | null): string[] {
  if (!config) return [...DEFAULT_SEALED_GLOBS];
  const raw = config["sealed_files"];
  if (raw === undefined || raw === null) return [...DEFAULT_SEALED_GLOBS];
  if (!Array.isArray(raw)) return [...DEFAULT_SEALED_GLOBS];
  const out: string[] = [];
  for (const entry of raw) {
    if (typeof entry === "string" && entry.trim().length > 0) {
      out.push(entry.trim());
    }
  }
  return out.length > 0 ? out : [...DEFAULT_SEALED_GLOBS];
}

function resolveConfigTurnAllowlist(config: Record<string, unknown> | null): string[] {
  if (!config) return [];
  const raw = config["sealed_files_allowlist_turns"];
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const entry of raw) {
    if (typeof entry === "string" && entry.trim().length > 0) {
      out.push(entry.trim());
    }
  }
  return out;
}

/**
 * Compile a single glob to an anchored RegExp. Supports:
 *   *   — matches any run of chars except "/"
 *   **  — matches any run of chars including "/"
 *   ?   — matches any single char except "/"
 * All other regex metacharacters are escaped. Paths are compared
 * after normalisation (POSIX separators, no leading "./" or "/").
 *
 * Special case: a `**\/` segment (double-star followed by separator)
 * matches zero or more path segments — so `foo/**\/bar` matches
 * `foo/bar`, `foo/a/bar`, `foo/a/b/bar`. A bare `**` matches any
 * number of characters including separators.
 */
export function globToRegExp(glob: string): RegExp {
  const g = glob.replace(/^\.\//, "").replace(/^\/+/, "");
  let re = "^";
  let i = 0;
  while (i < g.length) {
    const ch = g[i];
    if (ch === "*") {
      const isDouble = g[i + 1] === "*";
      if (isDouble) {
        // Consume the second "*".
        i += 2;
        if (g[i] === "/") {
          // `**/` — zero or more path segments.
          re += "(?:.*/)?";
          i += 1;
        } else {
          // Bare `**` — anything including separators.
          re += ".*";
        }
      } else {
        re += "[^/]*";
        i += 1;
      }
    } else if (ch === "?") {
      re += "[^/]";
      i += 1;
    } else if (ch !== undefined && /[.+^$|()[\]{}\\]/.test(ch)) {
      re += "\\" + ch;
      i += 1;
    } else {
      re += ch ?? "";
      i += 1;
    }
  }
  re += "$";
  return new RegExp(re);
}

export function matchesAnyGlob(relPath: string, globs: readonly string[]): boolean {
  const normalised = relPath.replace(/\\/g, "/").replace(/^\.\//, "").replace(/^\/+/, "");
  for (const g of globs) {
    if (globToRegExp(g).test(normalised)) return true;
  }
  return false;
}

/**
 * Walk the workspace and return every file path (relative, POSIX-
 * separator) that matches at least one of `globs`. Skips `.sealed-
 * manifest.json` and any `.spawn/` subtree. Does not follow symlinks.
 */
export async function resolveSealedPaths(
  workspaceRoot: string,
  globs: readonly string[],
): Promise<string[]> {
  const out: string[] = [];
  async function walk(dir: string, rel: string): Promise<void> {
    let entries;
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const ent of entries) {
      // Skip the manifest itself + ephemeral spawn dirs + any dotted
      // internal state we don't want to accidentally seal.
      if (ent.name === MANIFEST_REL && rel === "") continue;
      if (ent.name === ".spawn") continue;
      const nextRel = rel.length === 0 ? ent.name : `${rel}/${ent.name}`;
      const full = path.join(dir, ent.name);
      if (ent.isSymbolicLink()) continue;
      if (ent.isDirectory()) {
        await walk(full, nextRel);
      } else if (ent.isFile()) {
        if (matchesAnyGlob(nextRel, globs)) {
          out.push(nextRel);
        }
      }
    }
  }
  await walk(workspaceRoot, "");
  return out.sort();
}

async function sha256OfFile(full: string): Promise<string | null> {
  try {
    const buf = await fs.readFile(full);
    return crypto.createHash("sha256").update(buf).digest("hex");
  } catch {
    return null;
  }
}

async function readManifest(workspaceRoot: string): Promise<Manifest | null> {
  const p = path.join(workspaceRoot, MANIFEST_REL);
  let raw: string;
  try {
    raw = await fs.readFile(p, "utf8");
  } catch {
    return null;
  }
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const out: Manifest = {};
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (v && typeof v === "object") {
          const entry = v as Record<string, unknown>;
          const sha = typeof entry["sha256"] === "string" ? (entry["sha256"] as string) : null;
          const ts = typeof entry["updatedAt"] === "number" ? (entry["updatedAt"] as number) : null;
          if (sha && ts !== null) {
            out[k] = { sha256: sha, updatedAt: ts };
          }
        }
      }
      return out;
    }
  } catch {
    return null;
  }
  return null;
}

async function writeManifestAtomic(workspaceRoot: string, manifest: Manifest): Promise<void> {
  const target = path.join(workspaceRoot, MANIFEST_REL);
  const body = JSON.stringify(manifest, null, 2) + "\n";
  await atomicWriteFile(target, body);
}

/**
 * Extract every `[UNSEAL: <glob>]` marker from a user message. The
 * glob may contain any char except `]`. Whitespace around the glob is
 * trimmed. Empty patterns are dropped.
 */
export function extractUnsealPatterns(userMessage: string): string[] {
  if (!userMessage) return [];
  const re = /\[UNSEAL:\s*([^\]]+?)\s*\]/g;
  const out: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(userMessage)) !== null) {
    const pat = (m[1] ?? "").trim();
    if (pat.length > 0) out.push(pat);
  }
  return out;
}

export interface SealedFilesOptions {
  workspaceRoot: string;
}

interface DiffResult {
  changed: Array<{ path: string; sha256: string }>;
  firstRun: boolean;
  currentHashes: Manifest;
}

async function computeDiff(
  workspaceRoot: string,
  globs: readonly string[],
  manifest: Manifest | null,
): Promise<DiffResult> {
  const paths = await resolveSealedPaths(workspaceRoot, globs);
  const now = Date.now();
  const currentHashes: Manifest = {};
  const changed: Array<{ path: string; sha256: string }> = [];
  for (const rel of paths) {
    const full = path.join(workspaceRoot, rel);
    const sha = await sha256OfFile(full);
    if (sha === null) continue;
    currentHashes[rel] = { sha256: sha, updatedAt: now };
    if (!manifest) continue;
    const prev = manifest[rel];
    if (!prev) {
      // New sealed file — treat as a change so first appearance goes
      // through the allowlist (or creates a fresh entry when
      // initialising).
      changed.push({ path: rel, sha256: sha });
    } else if (prev.sha256 !== sha) {
      changed.push({ path: rel, sha256: sha });
    }
  }
  // Also detect deletions (file in manifest but not on disk). Removed
  // paths are flagged as changed with empty sha ("deleted:<rel>"), so
  // callers can block on unauthorised deletion too.
  if (manifest) {
    for (const rel of Object.keys(manifest)) {
      if (!currentHashes[rel]) {
        changed.push({ path: rel, sha256: "" });
      }
    }
  }
  return { changed, firstRun: manifest === null, currentHashes };
}

function makeBeforeTurnStartHook(opts: SealedFilesOptions): RegisteredHook<"beforeTurnStart"> {
  return {
    name: "builtin:sealed-files:beforeTurnStart",
    point: "beforeTurnStart",
    priority: 70,
    blocking: false,
    timeoutMs: 5_000,
    handler: async (_args, ctx: HookContext) => {
      PREEXISTING_DRIFT_BY_TURN.delete(ctx.turnId);
      SYSTEM_ALLOWED_UPDATES_BY_TURN.delete(ctx.turnId);
      if (!isEnabledByEnv()) return { action: "continue" };

      const config = await readConfig(opts.workspaceRoot);
      const globs = resolveSealedGlobs(config);
      const manifest = await readManifest(opts.workspaceRoot);
      if (!manifest) return { action: "continue" };

      const diff = await computeDiff(opts.workspaceRoot, globs, manifest);
      if (diff.changed.length === 0) return { action: "continue" };

      PREEXISTING_DRIFT_BY_TURN.set(
        ctx.turnId,
        new Map(diff.changed.map((ch) => [ch.path, ch.sha256])),
      );
      ctx.emit({
        type: "rule_check",
        ruleId: "sealed-files",
        verdict: "ok",
        detail: `sealed_files_preexisting_drift count=${diff.changed.length}`,
      });
      ctx.log("info", "[sealedFiles] pre-existing sealed drift snapshotted", {
        turnId: ctx.turnId,
        paths: diff.changed.map((ch) => ch.path),
      });
      return { action: "continue" };
    },
  };
}

/**
 * beforeCommit hook — computes the diff and either blocks the turn or
 * records the allowed changes for `afterCommit` to persist.
 */
function makeBeforeCommitHook(opts: SealedFilesOptions): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:sealed-files",
    point: "beforeCommit",
    priority: 70,
    blocking: true,
    timeoutMs: 5_000,
    handler: async ({ userMessage }, ctx: HookContext) => {
      if (!isEnabledByEnv()) return { action: "continue" };

      const config = await readConfig(opts.workspaceRoot);
      const globs = resolveSealedGlobs(config);
      const manifest = await readManifest(opts.workspaceRoot);
      const diff = await computeDiff(opts.workspaceRoot, globs, manifest);

      // First run — no manifest, no violation. Seed the manifest from
      // the current filesystem and emit an audit event.
      if (diff.firstRun) {
        try {
          await writeManifestAtomic(opts.workspaceRoot, diff.currentHashes);
        } catch (err) {
          ctx.log("warn", "[sealedFiles] failed to initialise manifest", {
            error: String(err),
          });
        }
        ctx.emit({
          type: "rule_check",
          ruleId: "sealed-files",
          verdict: "ok",
          detail: `sealed_manifest_initialized count=${Object.keys(diff.currentHashes).length}`,
        });
        ctx.log("info", "[sealedFiles] sealed_manifest_initialized", {
          turnId: ctx.turnId,
          count: Object.keys(diff.currentHashes).length,
        });
        return { action: "continue" };
      }

      if (diff.changed.length === 0) {
        return { action: "continue" };
      }

      const configTurnAllowlist = resolveConfigTurnAllowlist(config);
      const turnAllowed = configTurnAllowlist.includes(ctx.turnId);
      const unsealPatterns = extractUnsealPatterns(userMessage);
      const preexistingDrift = PREEXISTING_DRIFT_BY_TURN.get(ctx.turnId);
      const systemAllowed = SYSTEM_ALLOWED_UPDATES_BY_TURN.get(ctx.turnId);

      const violations: string[] = [];
      const bypassedByConfig: string[] = [];
      const bypassedByUnseal: Array<{ path: string; pattern: string }> = [];
      const bypassedAsPreexisting: string[] = [];
      const bypassedBySystem: string[] = [];
      const pendingUpdates: PendingUpdate[] = [];

      for (const ch of diff.changed) {
        if (preexistingDrift?.get(ch.path) === ch.sha256) {
          bypassedAsPreexisting.push(ch.path);
          if (ch.sha256 !== "") pendingUpdates.push({ path: ch.path, sha256: ch.sha256 });
          continue;
        }
        if (systemAllowed?.has(ch.path)) {
          bypassedBySystem.push(ch.path);
          if (ch.sha256 !== "") pendingUpdates.push({ path: ch.path, sha256: ch.sha256 });
          continue;
        }
        if (turnAllowed) {
          bypassedByConfig.push(ch.path);
          if (ch.sha256 !== "") pendingUpdates.push({ path: ch.path, sha256: ch.sha256 });
          continue;
        }
        // UNSEAL: explicit per-turn bypass for this specific path.
        let matchedPattern: string | null = null;
        for (const pat of unsealPatterns) {
          if (matchesAnyGlob(ch.path, [pat])) {
            matchedPattern = pat;
            break;
          }
        }
        if (matchedPattern) {
          bypassedByUnseal.push({ path: ch.path, pattern: matchedPattern });
          if (ch.sha256 !== "") pendingUpdates.push({ path: ch.path, sha256: ch.sha256 });
          continue;
        }
        violations.push(ch.path);
      }

      for (const p of bypassedByConfig) {
        ctx.emit({
          type: "rule_check",
          ruleId: "sealed-files",
          verdict: "ok",
          detail: `sealed_files_bypass kind=config_turn path=${p}`,
        });
        ctx.log("info", "[sealedFiles] sealed_files_bypass (config_turn)", {
          turnId: ctx.turnId,
          path: p,
        });
      }
      for (const { path: p, pattern } of bypassedByUnseal) {
        ctx.emit({
          type: "rule_check",
          ruleId: "sealed-files",
          verdict: "ok",
          detail: `sealed_files_bypass kind=unseal_marker path=${p} pattern=${pattern}`,
        });
        ctx.log("info", "[sealedFiles] sealed_files_bypass (unseal_marker)", {
          turnId: ctx.turnId,
          path: p,
          pattern,
        });
      }
      for (const p of bypassedAsPreexisting) {
        ctx.emit({
          type: "rule_check",
          ruleId: "sealed-files",
          verdict: "ok",
          detail: `sealed_files_preexisting_drift path=${p}`,
        });
        ctx.log("info", "[sealedFiles] sealed_files_preexisting_drift", {
          turnId: ctx.turnId,
          path: p,
        });
      }
      for (const p of bypassedBySystem) {
        ctx.emit({
          type: "rule_check",
          ruleId: "sealed-files",
          verdict: "ok",
          detail: `sealed_files_bypass kind=system path=${p}`,
        });
        ctx.log("info", "[sealedFiles] sealed_files_bypass (system)", {
          turnId: ctx.turnId,
          path: p,
        });
      }

      if (violations.length > 0) {
        ctx.emit({
          type: "rule_check",
          ruleId: "sealed-files",
          verdict: "violation",
          detail: `sealed_files_violation paths=${violations.join(",")}`,
        });
        ctx.log("warn", "[sealedFiles] sealed_files_violation", {
          turnId: ctx.turnId,
          paths: violations,
        });
        // Discard any pending updates — a mixed allow+violation turn
        // should not silently persist the allowed half.
        PENDING_UPDATES_BY_TURN.delete(ctx.turnId);
        PREEXISTING_DRIFT_BY_TURN.delete(ctx.turnId);
        SYSTEM_ALLOWED_UPDATES_BY_TURN.delete(ctx.turnId);

        // Circuit-breaker integration: record the repeated failure so
        // a retry cascade (new turnId per attempt) hits the cooldown
        // after CIRCUIT_THRESHOLD violations of the same file-set.
        const signature = circuitSignatureFor("builtin:sealed-files", violations);
        let cooldownSuffix = "";
        try {
          const rec = await recordCircuitFailure(
            { workspaceRoot: opts.workspaceRoot },
            signature,
          );
          if (rec.tripped) {
            ctx.emit({
              type: "rule_check",
              ruleId: "sealed-files",
              verdict: "violation",
              detail: `circuit_breaker_tripped signature=${signature.slice(0, 8)} count=${rec.entry.count}`,
            });
            ctx.log("warn", "[sealedFiles] circuit_breaker_tripped", {
              turnId: ctx.turnId,
              signature,
              count: rec.entry.count,
              trippedUntil: rec.entry.trippedUntil,
            });
            cooldownSuffix =
              " 🚫 Circuit breaker: 동일 sealed_files 위반 3회 반복. 10분 쿨다운 — 새 prompt으로 재시도하세요.";
          }
        } catch (err) {
          // Fail-open: breaker bookkeeping must never mask the block.
          ctx.log("warn", "[sealedFiles] circuit breaker record failed", {
            turnId: ctx.turnId,
            error: String(err),
          });
        }

        return {
          action: "block",
          reason: `[RULE:SEALED_FILES] Sealed files changed without explicit approval. Revert the sealed-file edits before retrying, or ask the user for explicit [UNSEAL:<path>] approval for each intended path.${cooldownSuffix}`,
        };
      }

      if (pendingUpdates.length > 0) {
        // Remember these for afterCommit.
        PENDING_UPDATES_BY_TURN.set(ctx.turnId, pendingUpdates);
      }

      return { action: "continue" };
    },
  };
}

/**
 * afterCommit hook — persists allowed hash updates to the manifest so
 * the next turn's diff sees the new baseline. Skipped if beforeCommit
 * found no allowed changes for this turn.
 */
function makeAfterCommitHook(opts: SealedFilesOptions): RegisteredHook<"afterCommit"> {
  return {
    name: "builtin:sealed-files:afterCommit",
    point: "afterCommit",
    priority: 70,
    blocking: false,
    timeoutMs: 5_000,
    handler: async (_args, ctx: HookContext) => {
      const pending = PENDING_UPDATES_BY_TURN.get(ctx.turnId);
      PENDING_UPDATES_BY_TURN.delete(ctx.turnId);
      PREEXISTING_DRIFT_BY_TURN.delete(ctx.turnId);
      SYSTEM_ALLOWED_UPDATES_BY_TURN.delete(ctx.turnId);
      if (!isEnabledByEnv()) return;
      if (!pending || pending.length === 0) return;
      const manifest = (await readManifest(opts.workspaceRoot)) ?? {};
      const now = Date.now();
      for (const upd of pending) {
        manifest[upd.path] = { sha256: upd.sha256, updatedAt: now };
      }
      try {
        await writeManifestAtomic(opts.workspaceRoot, manifest);
        ctx.log("info", "[sealedFiles] manifest updated after allowed commit", {
          turnId: ctx.turnId,
          count: pending.length,
        });
      } catch (err) {
        ctx.log("warn", "[sealedFiles] failed to update manifest", {
          turnId: ctx.turnId,
          error: String(err),
        });
      }
    },
  };
}

export interface SealedFilesHooks {
  beforeTurnStart: RegisteredHook<"beforeTurnStart">;
  beforeCommit: RegisteredHook<"beforeCommit">;
  afterCommit: RegisteredHook<"afterCommit">;
}

export function makeSealedFilesHooks(opts: SealedFilesOptions): SealedFilesHooks {
  return {
    beforeTurnStart: makeBeforeTurnStartHook(opts),
    beforeCommit: makeBeforeCommitHook(opts),
    afterCommit: makeAfterCommitHook(opts),
  };
}

/** Test helpers — NOT public API. Exported only so the unit tests can
 * poke the internal per-turn pending-updates map. */
export const __testing = {
  clearPending: (): void => {
    PENDING_UPDATES_BY_TURN.clear();
    PREEXISTING_DRIFT_BY_TURN.clear();
    SYSTEM_ALLOWED_UPDATES_BY_TURN.clear();
  },
  getPending: (turnId: string): ReadonlyArray<PendingUpdate> | undefined =>
    PENDING_UPDATES_BY_TURN.get(turnId),
};
