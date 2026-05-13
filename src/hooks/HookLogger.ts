/**
 * HookLogger — per-hook execution logging to JSONL files.
 *
 * Writes to `./logs/hooks/<hookName>.jsonl`. Append-only, non-blocking.
 * Log rotation: when a file exceeds 10 MB, it is renamed to `.1` and a
 * fresh file is started. Never throws, never slows down hook execution.
 */

import fs from "node:fs";
import path from "node:path";

import type { HookPoint } from "./types.js";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface HookLogEntry {
  timestamp: string;
  hookName: string;
  point: HookPoint;
  action: string;
  reason?: string;
  durationMs: number;
  error?: string;
}

export interface GetLogsOptions {
  since?: Date;
  level?: string;
  limit?: number;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB
const DEFAULT_LOGS_DIR = "./logs/hooks";

/* ------------------------------------------------------------------ */
/*  HookLogger                                                         */
/* ------------------------------------------------------------------ */

export class HookLogger {
  private readonly logsDir: string;

  constructor(logsDir?: string) {
    this.logsDir = logsDir ?? DEFAULT_LOGS_DIR;
  }

  /**
   * Append a log entry for a hook execution. Non-blocking — errors are
   * silently swallowed to avoid affecting hook performance.
   */
  log(entry: HookLogEntry): void {
    try {
      const filePath = this.logFilePath(entry.hookName);

      // Ensure directory exists
      const dir = path.dirname(filePath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }

      // Check rotation before writing
      this.maybeRotate(filePath);

      const line = JSON.stringify(entry) + "\n";
      fs.appendFileSync(filePath, line, "utf-8");
    } catch {
      // Non-blocking — never throw from logging
    }
  }

  /**
   * Read log entries for a hook, with optional filtering.
   */
  getLogs(hookName: string, opts?: GetLogsOptions): HookLogEntry[] {
    try {
      const filePath = this.logFilePath(hookName);
      if (!fs.existsSync(filePath)) return [];

      const content = fs.readFileSync(filePath, "utf-8");
      const lines = content.trim().split("\n").filter(Boolean);

      let entries: HookLogEntry[] = [];
      for (const line of lines) {
        try {
          entries.push(JSON.parse(line) as HookLogEntry);
        } catch {
          // Skip malformed lines
        }
      }

      // Filter by `since`
      if (opts?.since) {
        const sinceMs = opts.since.getTime();
        entries = entries.filter(
          (e) => new Date(e.timestamp).getTime() >= sinceMs,
        );
      }

      // Filter by `level` (matches action field)
      if (opts?.level) {
        const level = opts.level;
        entries = entries.filter((e) => e.action === level);
      }

      // Limit — take last N entries
      if (opts?.limit && opts.limit > 0 && entries.length > opts.limit) {
        entries = entries.slice(-opts.limit);
      }

      return entries;
    } catch {
      return [];
    }
  }

  /**
   * Return the JSONL file path for a given hook name.
   */
  logFilePath(hookName: string): string {
    // Sanitize hook name for filesystem safety
    const safe = hookName.replace(/[^a-zA-Z0-9_:-]/g, "_");
    return path.join(this.logsDir, `${safe}.jsonl`);
  }

  /**
   * Rotate the log file if it exceeds MAX_FILE_SIZE_BYTES. The existing
   * file is renamed to `.1`; if `.1` already exists it is overwritten.
   */
  private maybeRotate(filePath: string): void {
    try {
      if (!fs.existsSync(filePath)) return;
      const stat = fs.statSync(filePath);
      if (stat.size < MAX_FILE_SIZE_BYTES) return;

      const rotatedPath = filePath + ".1";
      fs.renameSync(filePath, rotatedPath);
    } catch {
      // Rotation failure is non-fatal
    }
  }
}
