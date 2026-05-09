/**
 * Session resume seed (Layer 4 of the meta-cognitive scaffolding —
 * docs/plans/2026-04-20-agent-self-model-design.md).
 *
 * beforeTurnStart hook, priority 2. On the FIRST turn of a session's
 * pod lifetime where the transcript already has prior committed
 * content, inject a compact `<session_resume>` block into the active
 * context's systemPromptAddendum so the next beforeLLMCall sees it.
 *
 * Design decisions (per doc §Decisions):
 *  1. No Haiku synopsis. Last 3 user+assistant turns verbatim,
 *     truncated to 800 chars per message.
 *  2. Recently modified files = workspace-wide `mtime` within 24h
 *     window around session.meta.lastActivityAt.
 *
 * "First turn of pod lifetime" tracked via a module-level `Set<string>`
 * of sessionKeys we've already seeded. Memory-only — pod restart
 * naturally re-seeds on the first post-restart turn, which is
 * exactly the intended trigger.
 *
 * Fail-open: any FS / transcript read error logs a warn and the turn
 * continues unseeded. The seed is a nudge, not a correctness gate.
 *
 * Toggle: `CORE_AGENT_SESSION_RESUME_SEED=off` disables globally.
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { RegisteredHook, HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import { isIncognitoMemoryMode } from "../../util/memoryMode.js";

/** Per-message character cap in the synopsis. */
const MAX_CHARS_PER_MESSAGE = 800;
/** Per-file mtime window (ms) for "recently modified". */
const RECENT_WINDOW_MS = 24 * 60 * 60 * 1000;
/** Max recently-modified files listed. */
const MAX_RECENT_FILES = 20;
/** Directories to skip when walking for recent files. */
const EXCLUDED_DIR_NAMES = new Set([
  "node_modules",
  ".git",
  ".DS_Store",
  "dist",
  "build",
  ".next",
  ".cache",
]);

/** sessionKeys we have already seeded this pod lifetime. */
const seededSessions = new Set<string>();

/** Exported for tests to reset state. */
export function _clearSessionResumeMemo(): void {
  seededSessions.clear();
}

export interface SessionResumeSnapshot {
  /** Transcript entries visible to the hook, in append order. */
  transcript: ReadonlyArray<TranscriptEntry>;
  /** Last activity timestamp (ms) — anchor for the 24h file window. */
  lastActivityAt: number;
}

export interface SessionResumeAgent {
  /** Return the snapshot needed for the seed, or null if the session
   * is fresh / not resumable. */
  getResumeSnapshot(sessionKey: string): Promise<SessionResumeSnapshot | null>;
  /** Add the assembled seed block to the session's next-turn system
   * prompt addendum. Implementation owns the exact surface (append
   * to active context's systemPromptAddendum, cache on Session,
   * etc.). */
  appendResumeSeed(sessionKey: string, seed: string): Promise<void>;
}

export interface SessionResumeOptions {
  readonly agent: SessionResumeAgent;
  readonly workspaceRoot: string;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_SESSION_RESUME_SEED;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

interface TurnPair {
  user?: string;
  assistant?: string;
  turnId: string;
}

export interface AbandonedTurnSummary {
  turnId: string;
  startedAt?: number;
  lastEventAt?: number;
  user?: string;
  assistant?: string;
  toolCalls: string[];
  committed: false;
}

/**
 * Exported for tests — fold a transcript into at most `maxPairs` most
 * recent committed {user,assistant} turns. Only turns that have both
 * a user_message AND at least one assistant_text entry AND are
 * followed by a `turn_committed` entry count.
 */
export function extractRecentTurns(
  transcript: ReadonlyArray<TranscriptEntry>,
  maxPairs: number,
): TurnPair[] {
  // Group entries by turnId.
  const byTurn = new Map<string, {
    user?: string;
    assistant: string[];
    committed: boolean;
    order: number;
  }>();
  let order = 0;
  for (const e of transcript) {
    if (!("turnId" in e) || typeof e.turnId !== "string") continue;
    const turnId = e.turnId;
    const entry = byTurn.get(turnId) ?? {
      assistant: [],
      committed: false,
      order: order++,
    };
    if (e.kind === "user_message") {
      entry.user = e.text;
    } else if (e.kind === "assistant_text") {
      entry.assistant.push(e.text);
    } else if (e.kind === "turn_committed") {
      entry.committed = true;
    }
    byTurn.set(turnId, entry);
  }
  const result: TurnPair[] = [];
  for (const [turnId, v] of byTurn) {
    if (!v.committed) continue;
    if (!v.user && v.assistant.length === 0) continue;
    result.push({
      user: v.user,
      assistant: v.assistant.length > 0 ? v.assistant.join("\n") : undefined,
      turnId,
    });
  }
  // Sort by insertion order (preserves transcript append order).
  result.sort((a, b) => {
    return (byTurn.get(a.turnId)!.order - byTurn.get(b.turnId)!.order);
  });
  return result.slice(Math.max(0, result.length - maxPairs));
}

export function extractAbandonedTurn(
  transcript: ReadonlyArray<TranscriptEntry>,
): AbandonedTurnSummary | null {
  const byTurn = new Map<string, {
    startedAt?: number;
    lastEventAt?: number;
    user?: string;
    assistant: string[];
    toolCalls: string[];
    complete: boolean;
    order: number;
  }>();
  let order = 0;
  for (const e of transcript) {
    if (!("turnId" in e) || typeof e.turnId !== "string") continue;
    const entry = byTurn.get(e.turnId) ?? {
      assistant: [],
      toolCalls: [],
      complete: false,
      order: order++,
    };
    if ("ts" in e && typeof e.ts === "number") {
      entry.lastEventAt = Math.max(entry.lastEventAt ?? e.ts, e.ts);
    }
    if (e.kind === "turn_started") {
      entry.startedAt = e.ts;
    } else if (e.kind === "user_message") {
      entry.user = e.text;
    } else if (e.kind === "assistant_text") {
      entry.assistant.push(e.text);
    } else if (e.kind === "tool_call") {
      entry.toolCalls.push(e.name);
    } else if (e.kind === "turn_committed" || e.kind === "turn_aborted") {
      entry.complete = true;
    }
    byTurn.set(e.turnId, entry);
  }

  const abandoned = [...byTurn.entries()]
    .filter(([, entry]) => !entry.complete)
    .sort((a, b) => b[1].order - a[1].order)[0];
  if (!abandoned) return null;

  const [turnId, entry] = abandoned;
  if (!entry.startedAt && !entry.user && entry.toolCalls.length === 0) {
    return null;
  }

  return {
    turnId,
    ...(entry.startedAt !== undefined ? { startedAt: entry.startedAt } : {}),
    ...(entry.lastEventAt !== undefined ? { lastEventAt: entry.lastEventAt } : {}),
    ...(entry.user ? { user: entry.user } : {}),
    ...(entry.assistant.length > 0 ? { assistant: entry.assistant.join("\n") } : {}),
    toolCalls: [...new Set(entry.toolCalls)].slice(0, 12),
    committed: false,
  };
}

async function listRecentFiles(
  workspaceRoot: string,
  cutoffMs: number,
): Promise<string[]> {
  const out: { rel: string; mtime: number }[] = [];
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
            out.push({ rel, mtime: st.mtimeMs });
          }
        } catch {
          /* ignore */
        }
      }
    }
  }
  try {
    const st = await fs.stat(workspaceRoot);
    if (!st.isDirectory()) return [];
  } catch {
    return [];
  }
  await walk(workspaceRoot, "");
  out.sort((a, b) => b.mtime - a.mtime);
  return out.slice(0, MAX_RECENT_FILES).map((x) => x.rel);
}

/**
 * Build the `<session_resume>` block. Exported for tests. Returns an
 * empty string when nothing interesting to show (no turns + no recent
 * files) so the hook can no-op.
 */
export async function buildSessionResumeBlock(
  snapshot: SessionResumeSnapshot,
  workspaceRoot: string,
): Promise<string> {
  const turns = extractRecentTurns(snapshot.transcript, 3);
  const abandoned = extractAbandonedTurn(snapshot.transcript);
  const cutoff = snapshot.lastActivityAt - RECENT_WINDOW_MS;
  const recentFiles = await listRecentFiles(workspaceRoot, cutoff);

  if (turns.length === 0 && recentFiles.length === 0 && !abandoned) {
    return "";
  }

  const lines: string[] = [];
  lines.push("<session_resume>");
  lines.push("This is the first turn since you resumed. Recent context:");
  lines.push("");

  lines.push(`## Last ${turns.length} turn(s) (verbatim)`);
  if (turns.length === 0) {
    lines.push("(no committed prior turns)");
  } else {
    for (const t of turns) {
      if (t.user) {
        lines.push(`User: ${truncate(t.user, MAX_CHARS_PER_MESSAGE)}`);
      }
      if (t.assistant) {
        lines.push(
          `Assistant: ${truncate(t.assistant, MAX_CHARS_PER_MESSAGE)}`,
        );
      }
    }
  }

  if (abandoned) {
    lines.push("");
    lines.push("## Interrupted prior turn");
    lines.push(
      `Turn ${abandoned.turnId} did not reach turn_committed or turn_aborted before this resume.`,
    );
    if (abandoned.user) {
      lines.push(`User: ${truncate(abandoned.user, MAX_CHARS_PER_MESSAGE)}`);
    }
    if (abandoned.assistant) {
      lines.push(`Assistant draft: ${truncate(abandoned.assistant, MAX_CHARS_PER_MESSAGE)}`);
    }
    if (abandoned.toolCalls.length > 0) {
      lines.push(`Tool calls before interruption: ${abandoned.toolCalls.join(", ")}`);
    }
    lines.push(
      "Treat this as potentially interrupted work. If the user's new message asks why it stopped or asks to continue, inspect workspace files and transcript state before answering.",
    );
  }

  lines.push("");
  lines.push(
    `## Recently modified files (within ${RECENT_WINDOW_MS / (60 * 60 * 1000)}h of last activity)`,
  );
  if (recentFiles.length === 0) {
    lines.push("(none)");
  } else {
    for (const rel of recentFiles) {
      lines.push(`- ${rel}`);
    }
  }

  lines.push("");
  lines.push(
    "Pick up where you left off. If the user's new message references",
  );
  lines.push(
    "earlier work, that work is in workspace — check before claiming",
  );
  lines.push("otherwise.");
  lines.push("</session_resume>");

  return lines.join("\n");
}

export function makeSessionResumeHook(
  opts: SessionResumeOptions,
): RegisteredHook<"beforeTurnStart"> {
  return {
    name: "builtin:session-resume",
    point: "beforeTurnStart",
    priority: 2,
    blocking: false,
    handler: async (_args, ctx: HookContext) => {
      try {
        if (isIncognitoMemoryMode(ctx.memoryMode)) return { action: "continue" };
        if (!isEnabled()) return { action: "continue" };

        // Already seeded this pod lifetime — noop.
        if (seededSessions.has(ctx.sessionKey)) {
          return { action: "continue" };
        }

        const snapshot = await opts.agent.getResumeSnapshot(ctx.sessionKey);
        if (!snapshot) {
          // Fresh session — nothing to resume. Mark as seeded so we
          // don't re-probe on every turn of the session's lifetime.
          seededSessions.add(ctx.sessionKey);
          return { action: "continue" };
        }

        if (snapshot.transcript.length === 0) {
          seededSessions.add(ctx.sessionKey);
          return { action: "continue" };
        }

        const block = await buildSessionResumeBlock(
          snapshot,
          opts.workspaceRoot,
        );

        // Mark as seeded FIRST, so a failure after this point still
        // prevents runaway retries on the same session.
        seededSessions.add(ctx.sessionKey);

        if (!block) return { action: "continue" };

        await opts.agent.appendResumeSeed(ctx.sessionKey, block);

        ctx.log("info", "[session-resume] seeded", {
          sessionKey: ctx.sessionKey,
          turnCount: snapshot.transcript.length,
          bytes: Buffer.byteLength(block, "utf8"),
        });

        return { action: "continue" };
      } catch (err) {
        ctx.log("warn", "[session-resume] seed failed; turn continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
