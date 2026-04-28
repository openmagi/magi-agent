/**
 * TranscriptReader — read-only views over the per-session jsonl files.
 *
 * Phase 2h: `GET /v1/compliance` and `GET /v1/audit` need to project
 * transcript entries into summaries / per-turn bundles. This module
 * centralises the disk access so the HTTP layer can stay thin.
 */

import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import readline from "node:readline";
import type { TranscriptEntry } from "./Transcript.js";

export function sessionsDirOf(workspaceRoot: string): string {
  return path.join(workspaceRoot, "core-agent", "sessions");
}

export function sessionFileName(sessionKey: string): string {
  const hash = crypto.createHash("sha1").update(sessionKey).digest("hex").slice(0, 16);
  return `${hash}.jsonl`;
}

export function sessionFilePath(workspaceRoot: string, sessionKey: string): string {
  return path.join(sessionsDirOf(workspaceRoot), sessionFileName(sessionKey));
}

/**
 * Read every entry from a session file. Small files (Phase 2h sessions
 * are capped under a few MB); the caller pages at the turn level.
 */
export async function readAllEntries(file: string): Promise<TranscriptEntry[]> {
  try {
    const raw = await fs.readFile(file, "utf8");
    const out: TranscriptEntry[] = [];
    for (const line of raw.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        out.push(JSON.parse(trimmed) as TranscriptEntry);
      } catch {
        // Malformed trailing write — skip (invariant F tolerates).
      }
    }
    return out;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
}

export interface TurnSummary {
  turnId: string;
  startedAt?: number;
  endedAt?: number;
  status: "pending" | "committed" | "aborted";
  toolUseCount: number;
  messageCount: number;
  inputTokens?: number;
  outputTokens?: number;
  abortReason?: string;
}

/**
 * Collapse raw entries into one summary per turn, ordered by
 * `turn_started` time. Entries with no associated turn_started are
 * grouped under their declared turnId.
 */
export function summariseTurns(entries: TranscriptEntry[]): TurnSummary[] {
  const byTurn = new Map<string, TurnSummary>();
  for (const e of entries) {
    if (!e.turnId) continue;
    const id = e.turnId;
    let s = byTurn.get(id);
    if (!s) {
      s = {
        turnId: id,
        status: "pending",
        toolUseCount: 0,
        messageCount: 0,
      };
      byTurn.set(id, s);
    }
    switch (e.kind) {
      case "turn_started":
        s.startedAt = e.ts;
        break;
      case "user_message":
      case "assistant_text":
        s.messageCount++;
        break;
      case "tool_call":
        s.toolUseCount++;
        break;
      case "turn_committed":
        s.status = "committed";
        s.endedAt = e.ts;
        s.inputTokens = e.inputTokens;
        s.outputTokens = e.outputTokens;
        break;
      case "turn_aborted":
        s.status = "aborted";
        s.endedAt = e.ts;
        s.abortReason = e.reason;
        break;
    }
  }
  const list = [...byTurn.values()];
  list.sort((a, b) => (a.startedAt ?? 0) - (b.startedAt ?? 0));
  return list;
}

export interface TurnBundle {
  turnId: string;
  sessionKey: string;
  startedAt?: number;
  endedAt?: number;
  status: "pending" | "committed" | "aborted";
  abortReason?: string;
  messages: Array<{ role: "user" | "assistant"; ts: number; text: string }>;
  toolUses: Array<{
    toolUseId: string;
    name: string;
    input: unknown;
    status?: string;
    output?: string;
    isError?: boolean;
    callTs?: number;
    resultTs?: number;
    durationMs?: number;
  }>;
  inputTokens?: number;
  outputTokens?: number;
}

/**
 * Assemble a full bundle for one turn: messages array + tool_use array
 * with inputs + outputs + timing, plus commit status.
 */
export function bundleTurn(
  entries: TranscriptEntry[],
  sessionKey: string,
  turnId: string,
): TurnBundle | null {
  const filtered = entries.filter((e) => e.turnId === turnId);
  if (filtered.length === 0) return null;

  const bundle: TurnBundle = {
    turnId,
    sessionKey,
    status: "pending",
    messages: [],
    toolUses: [],
  };

  type PartialToolUse = TurnBundle["toolUses"][number];
  const tuByUseId = new Map<string, PartialToolUse>();

  for (const e of filtered) {
    switch (e.kind) {
      case "turn_started":
        bundle.startedAt = e.ts;
        break;
      case "user_message":
        bundle.messages.push({ role: "user", ts: e.ts, text: e.text });
        break;
      case "assistant_text":
        bundle.messages.push({ role: "assistant", ts: e.ts, text: e.text });
        break;
      case "tool_call": {
        const tu: PartialToolUse = {
          toolUseId: e.toolUseId,
          name: e.name,
          input: e.input,
          callTs: e.ts,
        };
        tuByUseId.set(e.toolUseId, tu);
        bundle.toolUses.push(tu);
        break;
      }
      case "tool_result": {
        const tu = tuByUseId.get(e.toolUseId);
        if (tu) {
          tu.status = e.status;
          if (e.output !== undefined) tu.output = e.output;
          if (e.isError !== undefined) tu.isError = e.isError;
          tu.resultTs = e.ts;
          if (tu.callTs !== undefined) tu.durationMs = e.ts - tu.callTs;
        } else {
          // Orphan result (call persisted in a prior file version).
          bundle.toolUses.push({
            toolUseId: e.toolUseId,
            name: "<unknown>",
            input: null,
            status: e.status,
            ...(e.output !== undefined ? { output: e.output } : {}),
            ...(e.isError !== undefined ? { isError: e.isError } : {}),
            resultTs: e.ts,
          });
        }
        break;
      }
      case "turn_committed":
        bundle.status = "committed";
        bundle.endedAt = e.ts;
        bundle.inputTokens = e.inputTokens;
        bundle.outputTokens = e.outputTokens;
        break;
      case "turn_aborted":
        bundle.status = "aborted";
        bundle.endedAt = e.ts;
        bundle.abortReason = e.reason;
        break;
    }
  }
  return bundle;
}

/**
 * Find which session file a raw turnId lives in. Used by
 * `GET /v1/audit?turnId=...` when `sessionKey` isn't provided.
 *
 * Caller must pass a sessionKey hint map (hash→sessionKey) so we can
 * recover the plaintext sessionKey from the filename. For unknown
 * sessions (e.g. pre-Phase-2h data) the file hash is returned as the
 * opaque sessionKey stand-in.
 */
export async function findSessionOfTurn(
  workspaceRoot: string,
  turnId: string,
  sessionKeyByHash: Map<string, string>,
): Promise<{ sessionKey: string; file: string } | null> {
  const dir = sessionsDirOf(workspaceRoot);
  let files: string[];
  try {
    files = await fs.readdir(dir);
  } catch {
    return null;
  }
  for (const name of files) {
    if (!name.endsWith(".jsonl")) continue;
    const file = path.join(dir, name);
    const hash = name.replace(/\.jsonl$/, "");
    const found = await hasTurn(file, turnId);
    if (found) {
      const sessionKey = sessionKeyByHash.get(hash) ?? `#${hash}`;
      return { sessionKey, file };
    }
  }
  return null;
}

async function hasTurn(file: string, turnId: string): Promise<boolean> {
  return new Promise((resolve, reject) => {
    const stream = fsSync.createReadStream(file, { encoding: "utf8" });
    const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
    let found = false;
    rl.on("line", (line) => {
      if (found) return;
      const trimmed = line.trim();
      if (!trimmed) return;
      try {
        const e = JSON.parse(trimmed) as { turnId?: unknown };
        if (e.turnId === turnId) {
          found = true;
          rl.close();
          stream.destroy();
        }
      } catch {
        // skip
      }
    });
    rl.on("close", () => resolve(found));
    rl.on("error", reject);
  });
}
