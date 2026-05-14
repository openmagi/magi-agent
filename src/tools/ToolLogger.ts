/**
 * ToolLogger — per-tool execution logging to JSONL files.
 *
 * Writes to `./logs/tools/<toolName>.jsonl`. Append-only, non-blocking.
 * Log rotation: when a file exceeds 10 MB, it is renamed to `.1` and a
 * fresh file is started. Never throws, never slows down tool execution.
 *
 * Follows the same pattern as HookLogger.
 */

import fs from "node:fs";
import path from "node:path";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface ToolLogEntry {
  timestamp: string;
  toolName: string;
  status: string; // "ok" | "error" | "permission_denied" | "aborted"
  durationMs: number;
  error?: string;
  inputPreview?: string; // first 200 chars of JSON.stringify(input)
}

export interface GetToolLogsOptions {
  since?: Date;
  status?: string;
  limit?: number;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB
const DEFAULT_LOGS_DIR = "./logs/tools";
const INPUT_PREVIEW_MAX = 200;

/* ------------------------------------------------------------------ */
/*  ToolLogger                                                         */
/* ------------------------------------------------------------------ */

export class ToolLogger {
  private readonly logsDir: string;

  constructor(logsDir?: string) {
    this.logsDir = logsDir ?? DEFAULT_LOGS_DIR;
  }

  /**
   * Append a log entry for a tool execution. Non-blocking — errors are
   * silently swallowed to avoid affecting tool performance.
   */
  log(entry: ToolLogEntry): void {
    try {
      const filePath = this.logFilePath(entry.toolName);

      const dir = path.dirname(filePath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }

      this.maybeRotate(filePath);

      const line = JSON.stringify(entry) + "\n";
      fs.appendFileSync(filePath, line, "utf-8");
    } catch {
      // Non-blocking — never throw from logging
    }
  }

  /**
   * Create a log entry from a tool execution result.
   */
  static createEntry(
    toolName: string,
    status: string,
    durationMs: number,
    opts?: { error?: string; input?: unknown },
  ): ToolLogEntry {
    const entry: ToolLogEntry = {
      timestamp: new Date().toISOString(),
      toolName,
      status,
      durationMs,
    };
    if (opts?.error) {
      entry.error = opts.error;
    }
    if (opts?.input !== undefined) {
      try {
        const full = JSON.stringify(opts.input);
        entry.inputPreview = full.slice(0, INPUT_PREVIEW_MAX);
      } catch {
        // Skip input preview on serialization failure
      }
    }
    return entry;
  }

  /**
   * Read log entries for a tool, with optional filtering.
   */
  getLogs(toolName: string, opts?: GetToolLogsOptions): ToolLogEntry[] {
    try {
      const filePath = this.logFilePath(toolName);
      if (!fs.existsSync(filePath)) return [];

      const content = fs.readFileSync(filePath, "utf-8");
      const lines = content.trim().split("\n").filter(Boolean);

      let entries: ToolLogEntry[] = [];
      for (const line of lines) {
        try {
          entries.push(JSON.parse(line) as ToolLogEntry);
        } catch {
          // Skip malformed lines
        }
      }

      if (opts?.since) {
        const sinceMs = opts.since.getTime();
        entries = entries.filter(
          (e) => new Date(e.timestamp).getTime() >= sinceMs,
        );
      }

      if (opts?.status) {
        const status = opts.status;
        entries = entries.filter((e) => e.status === status);
      }

      if (opts?.limit && opts.limit > 0 && entries.length > opts.limit) {
        entries = entries.slice(-opts.limit);
      }

      return entries;
    } catch {
      return [];
    }
  }

  /**
   * Return the JSONL file path for a given tool name.
   */
  logFilePath(toolName: string): string {
    const safe = toolName.replace(/[^a-zA-Z0-9_:-]/g, "_");
    return path.join(this.logsDir, `${safe}.jsonl`);
  }

  /**
   * Rotate the log file if it exceeds MAX_FILE_SIZE_BYTES.
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
