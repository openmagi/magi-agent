import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import type { Transcript } from "../storage/Transcript.js";
import {
  CONTROL_EVENT_TYPES,
  type ControlEvent,
  type ControlEventInput,
} from "./ControlEvents.js";

export interface ControlEventLedgerOptions {
  rootDir: string;
  sessionKey: string;
  filePath?: string;
  transcript?: Transcript;
}

export class ControlEventLedger {
  private static readonly appendLocks = new Map<string, Promise<unknown>>();
  readonly filePath: string;
  private readonly sessionKey: string;
  private readonly transcript?: Transcript;

  constructor(opts: ControlEventLedgerOptions) {
    this.sessionKey = opts.sessionKey;
    this.transcript = opts.transcript;
    this.filePath =
      opts.filePath ??
      path.join(opts.rootDir, "control-events", `${sessionFileStem(opts.sessionKey)}.jsonl`);
  }

  async append(input: ControlEventInput): Promise<ControlEvent> {
    return await this.withAppendLock(async () => {
      validateControlEventInput(input);
      await this.ensureAppendBoundary();
      const events = await this.readAll();
      const event = {
        ...input,
        v: 1,
        eventId: `ce_${crypto.randomUUID().replace(/-/g, "")}`,
        seq: events.length === 0 ? 1 : Math.max(...events.map((e) => e.seq)) + 1,
        ts: input.ts ?? Date.now(),
        sessionKey: input.sessionKey ?? this.sessionKey,
      } as ControlEvent;

      await fs.mkdir(path.dirname(this.filePath), { recursive: true });
      await fs.appendFile(this.filePath, `${JSON.stringify(event)}\n`, "utf8");
      if (this.transcript) {
        await this.transcript.append({
          kind: "control_event",
          ts: event.ts,
          ...(event.turnId ? { turnId: event.turnId } : {}),
          seq: event.seq,
          eventId: event.eventId,
          eventType: event.type,
        });
      }
      return event;
    });
  }

  async readAll(): Promise<ControlEvent[]> {
    let text: string;
    try {
      text = await fs.readFile(this.filePath, "utf8");
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw err;
    }

    const events: ControlEvent[] = [];
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const parsed = JSON.parse(trimmed) as ControlEvent;
        if (isControlEvent(parsed)) events.push(parsed);
      } catch {
        continue;
      }
    }
    return events;
  }

  async readSince(seq: number): Promise<ControlEvent[]> {
    const events = await this.readAll();
    return events.filter((event) => event.seq > seq);
  }

  async readByTurn(turnId: string): Promise<ControlEvent[]> {
    const events = await this.readAll();
    return events.filter((event) => event.turnId === turnId);
  }

  private async withAppendLock<T>(fn: () => Promise<T>): Promise<T> {
    const previous =
      ControlEventLedger.appendLocks.get(this.filePath) ?? Promise.resolve();
    const run = previous.catch(() => undefined).then(fn);
    const cleanup = run.catch(() => undefined).then(() => {
      if (ControlEventLedger.appendLocks.get(this.filePath) === cleanup) {
        ControlEventLedger.appendLocks.delete(this.filePath);
      }
    });
    ControlEventLedger.appendLocks.set(this.filePath, cleanup);
    return await run;
  }

  private async ensureAppendBoundary(): Promise<void> {
    let handle: fs.FileHandle | null = null;
    try {
      handle = await fs.open(this.filePath, "r");
      const stat = await handle.stat();
      if (stat.size === 0) return;
      const buf = Buffer.alloc(1);
      await handle.read(buf, 0, 1, stat.size - 1);
      if (buf[0] !== 10) {
        await fs.appendFile(this.filePath, "\n", "utf8");
      }
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
      await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    } finally {
      await handle?.close();
    }
  }
}

export function sessionFileStem(sessionKey: string): string {
  return crypto.createHash("sha1").update(sessionKey).digest("hex").slice(0, 16);
}

function isControlEvent(value: unknown): value is ControlEvent {
  if (!value || typeof value !== "object") return false;
  const obj = value as Partial<ControlEvent>;
  return (
    obj.v === 1 &&
    typeof obj.eventId === "string" &&
    typeof obj.seq === "number" &&
    typeof obj.ts === "number" &&
    typeof obj.sessionKey === "string" &&
    typeof obj.type === "string" &&
    CONTROL_EVENT_TYPES.has(obj.type)
  );
}

function validateControlEventInput(input: ControlEventInput): void {
  if (!CONTROL_EVENT_TYPES.has(input.type)) {
    throw new Error(`unknown control event type: ${String(input.type)}`);
  }
  switch (input.type) {
    case "retry":
      requireString(input.turnId, "turnId");
      requireString(input.reason, "reason");
      requireNumber(input.attempt, "attempt");
      requireNumber(input.maxAttempts, "maxAttempts");
      requireBoolean(input.visibleToUser, "visibleToUser");
      break;
    case "permission_decision":
      requireString(input.source, "source");
      requireString(input.decision, "decision");
      break;
    case "control_request_created":
      requireString(input.request?.requestId, "request.requestId");
      requireString(input.request?.sessionKey, "request.sessionKey");
      requireString(input.request?.source, "request.source");
      requireString(input.request?.prompt, "request.prompt");
      requireNumber(input.request?.createdAt, "request.createdAt");
      requireNumber(input.request?.expiresAt, "request.expiresAt");
      break;
    case "control_request_resolved":
      requireString(input.requestId, "requestId");
      requireString(input.decision, "decision");
      break;
    case "control_request_cancelled":
      requireString(input.requestId, "requestId");
      requireString(input.reason, "reason");
      break;
    case "control_request_timed_out":
      requireString(input.requestId, "requestId");
      break;
    case "plan_lifecycle":
      requireString(input.planId, "planId");
      break;
    case "tool_use_summary":
      requireString(input.turnId, "turnId");
      requireString(input.toolName, "toolName");
      requireString(input.status, "status");
      break;
    case "structured_output":
      requireString(input.turnId, "turnId");
      requireString(input.status, "status");
      break;
    case "verification":
      requireString(input.status, "status");
      break;
    case "stop_reason":
      requireString(input.turnId, "turnId");
      requireString(input.reason, "reason");
      break;
    case "task_board_snapshot":
      requireDefined(input.taskBoard, "taskBoard");
      break;
    case "child_started":
    case "child_progress":
    case "child_cancelled":
    case "child_failed":
    case "child_completed":
      requireString(input.taskId, "taskId");
      if (input.type === "child_progress") requireString(input.detail, "detail");
      if (input.type === "child_cancelled") requireString(input.reason, "reason");
      if (input.type === "child_failed") {
        requireString(input.errorMessage, "errorMessage");
      }
      break;
    case "child_tool_request":
      requireString(input.taskId, "taskId");
      requireString(input.requestId, "requestId");
      requireString(input.toolName, "toolName");
      break;
    case "child_permission_decision":
      requireString(input.taskId, "taskId");
      requireString(input.decision, "decision");
      break;
    case "compaction_boundary":
      requireString(input.boundaryId, "boundaryId");
      break;
    case "runtime_trace":
      requireString(input.turnId, "turnId");
      requireString(input.phase, "phase");
      requireString(input.severity, "severity");
      requireString(input.title, "title");
      break;
    default:
      break;
  }
}

function requireDefined(value: unknown, field: string): void {
  if (value === undefined) {
    throw new Error(`invalid control event: missing ${field}`);
  }
}

function requireString(value: unknown, field: string): void {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`invalid control event: missing ${field}`);
  }
}

function requireNumber(value: unknown, field: string): void {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`invalid control event: missing ${field}`);
  }
}

function requireBoolean(value: unknown, field: string): void {
  if (typeof value !== "boolean") {
    throw new Error(`invalid control event: missing ${field}`);
  }
}
