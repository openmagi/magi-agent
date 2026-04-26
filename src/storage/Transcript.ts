/**
 * Transcript — per-session append-only jsonl.
 * Design reference: §5.2, §6-F.
 *
 * Phase 1b: minimal writer. Each line is a standalone JSON object with
 * a `kind` discriminator. Startup-replay logic lives in Session, which
 * ignores any trailing entries without a matching `turn_committed`
 * event (invariant F).
 */

import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import {
  applyMigrations,
  consoleMigrationLogger,
  transcriptMigrations,
  type TranscriptShape,
} from "../migrations/index.js";

export type TranscriptEntry =
  | {
      kind: "user_message";
      ts: number;
      turnId: string;
      text: string;
    }
  | {
      kind: "assistant_text";
      ts: number;
      turnId: string;
      text: string;
    }
  | {
      kind: "tool_call";
      ts: number;
      turnId: string;
      toolUseId: string;
      name: string;
      input: unknown;
    }
  | {
      kind: "tool_result";
      ts: number;
      turnId: string;
      toolUseId: string;
      status: string;
      output?: string;
      isError?: boolean;
    }
  | {
      kind: "turn_started";
      ts: number;
      turnId: string;
      declaredRoute: string;
    }
  | {
      kind: "turn_committed";
      ts: number;
      turnId: string;
      inputTokens: number;
      outputTokens: number;
    }
  | {
      kind: "turn_aborted";
      ts: number;
      turnId: string;
      reason: string;
    }
  | {
      kind: "compaction_boundary";
      ts: number;
      turnId: string;
      boundaryId: string;
      beforeTokenCount: number;
      afterTokenCount: number;
      summaryHash: string;
      summaryText: string;
      createdAt: number;
    };

/**
 * Type guard for `compaction_boundary` transcript entries (T1-02).
 * Used by `ContextEngine.buildMessagesFromTranscript` to partition
 * entries into pre-boundary (collapsed to synthetic summary) vs
 * post-boundary (replayed normally).
 */
export function isCompactionBoundary(
  entry: TranscriptEntry,
): entry is Extract<TranscriptEntry, { kind: "compaction_boundary" }> {
  return entry.kind === "compaction_boundary";
}

export interface TranscriptOptions {
  /**
   * Optional explicit file path override. Used by T4-19 Context so a
   * non-default context can live at `{sha1(sessionKey)}__{contextId}.jsonl`
   * while the default context keeps the legacy flat layout.
   */
  filePath?: string;
}

export class Transcript {
  readonly filePath: string;

  constructor(sessionsDir: string, sessionKey: string, opts?: TranscriptOptions) {
    if (opts?.filePath) {
      this.filePath = opts.filePath;
      return;
    }
    // Hash sessionKey into a filesystem-safe filename (colons allowed
    // on ext4 but not portable; strip to be safe).
    const hash = crypto.createHash("sha1").update(sessionKey).digest("hex").slice(0, 16);
    this.filePath = path.join(sessionsDir, `${hash}.jsonl`);
  }

  async ensureDir(): Promise<void> {
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
  }

  async append(entry: TranscriptEntry): Promise<void> {
    await this.ensureDir();
    const line = JSON.stringify(entry) + "\n";
    await fs.appendFile(this.filePath, line, "utf8");
  }

  /**
   * Read the transcript (whole file, Phase 1b is small). Returns all
   * entries including uncommitted tails — the caller is responsible
   * for discarding anything past the last `turn_committed`.
   *
   * Runs any pending transcript schema migrations in-memory. The
   * JSONL on-disk layout is append-only so the framework operates on
   * the parsed entry list without rewriting the file; the per-file
   * schema version lives in the sibling `{stem}.schema.json`
   * sentinel (see `src/migrations/transcriptMigrations.ts`). v0→v1
   * is a no-op today so no sentinel is required for existing data.
   */
  async readAll(): Promise<TranscriptEntry[]> {
    let txt: string;
    try {
      txt = await fs.readFile(this.filePath, "utf8");
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw err;
    }
    const entries: TranscriptEntry[] = [];
    for (const line of txt.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        entries.push(JSON.parse(trimmed) as TranscriptEntry);
      } catch {
        // Ignore malformed trailing lines (crash during write).
      }
    }

    // Apply migrations in-memory. Target path omitted on purpose —
    // we never rewrite the append-only JSONL file; future migrations
    // that need to persist per-file version metadata should write to
    // a sibling sentinel, not touch the transcript itself.
    const shape: TranscriptShape = { entries };
    const migrated = await applyMigrations(shape, transcriptMigrations, {
      workspaceRoot: path.dirname(this.filePath),
      log: consoleMigrationLogger,
    });
    return migrated.entries;
  }

  /**
   * Entries up to (and including) the last completed turn — committed
   * OR aborted. Used for session resume / LLM message construction.
   *
   * 2026-04-22 bug fix: previously only looked for `turn_committed`,
   * so sessions where every turn was aborted (e.g. factGroundingVerifier
   * blocked commit) returned [] — causing bot amnesia. Now treats
   * `turn_aborted` as a valid boundary too, since aborted turns were
   * already streamed to the user via SSE text_delta.
   */
  async readCommitted(): Promise<TranscriptEntry[]> {
    const all = await this.readAll();
    let lastComplete = -1;
    for (let i = all.length - 1; i >= 0; i--) {
      const kind = all[i]?.kind;
      if (kind === "turn_committed" || kind === "turn_aborted") {
        lastComplete = i;
        break;
      }
    }
    if (lastComplete < 0) return [];
    return all.slice(0, lastComplete + 1);
  }
}
