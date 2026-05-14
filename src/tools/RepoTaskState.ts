import fs from "node:fs/promises";
import path from "node:path";
import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { Workspace } from "../storage/Workspace.js";
import { errorResult } from "../util/toolResult.js";

export interface RepoTaskState {
  goal: string | null;
  plan: string[];
  touchedFiles: string[];
  pendingTests: string[];
  blockers: string[];
  acceptanceCriteria: string[];
  commitUnits: CodingCommitUnit[];
  activeUnitId: string | null;
  workspaceLock: CodingWorkspaceLock | null;
  updatedAt: string | null;
}

export type CodingCommitUnitStatus = "pending" | "in_progress" | "blocked" | "completed";

export interface CodingCommitUnit {
  id: string;
  title: string;
  description?: string;
  status: CodingCommitUnitStatus;
  acceptanceCriteria: string[];
  changedFiles: string[];
  verificationCommands: string[];
  blockers: string[];
  commitSha?: string;
  startedAt?: string;
  completedAt?: string;
  updatedAt: string;
}

export interface CodingWorkspaceLock {
  status: "active" | "released";
  lockId: string;
  ownerSessionKey: string;
  goal: string;
  activeUnitId?: string;
  acquiredAt: string;
  updatedAt: string;
  releasedAt?: string;
  releaseReason?: string;
}

export interface CodingCommitUnitPatch {
  id: string;
  title?: string;
  description?: string;
  status?: CodingCommitUnitStatus;
  acceptanceCriteria?: string[];
  changedFiles?: string[];
  verificationCommands?: string[];
  blockers?: string[];
  commitSha?: string;
}

export type CodingWorkspaceLockInput =
  | {
      action: "acquire";
      goal?: string;
      activeUnitId?: string;
      lockId?: string;
    }
  | {
      action: "release";
      reason?: string;
    };

export interface RepoTaskStateInput {
  action: "read" | "update";
  goal?: string;
  plan?: string[];
  touchedFiles?: string[];
  pendingTests?: string[];
  blockers?: string[];
  acceptanceCriteria?: string[];
  commitUnits?: CodingCommitUnitPatch[];
  activeUnitId?: string;
  workspaceLock?: CodingWorkspaceLockInput;
}

export interface RepoTaskStateOutput {
  path: string;
  state: RepoTaskState;
  ledgerPath?: string;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["read", "update"] },
    goal: { type: "string" },
    plan: { type: "array", items: { type: "string" } },
    touchedFiles: { type: "array", items: { type: "string" } },
    pendingTests: { type: "array", items: { type: "string" } },
    blockers: { type: "array", items: { type: "string" } },
    acceptanceCriteria: { type: "array", items: { type: "string" } },
    commitUnits: {
      type: "array",
      items: {
        type: "object",
        properties: {
          id: { type: "string" },
          title: { type: "string" },
          description: { type: "string" },
          status: {
            type: "string",
            enum: ["pending", "in_progress", "blocked", "completed"],
          },
          acceptanceCriteria: { type: "array", items: { type: "string" } },
          changedFiles: { type: "array", items: { type: "string" } },
          verificationCommands: { type: "array", items: { type: "string" } },
          blockers: { type: "array", items: { type: "string" } },
          commitSha: { type: "string" },
        },
        required: ["id"],
      },
    },
    activeUnitId: { type: "string" },
    workspaceLock: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["acquire", "release"] },
        goal: { type: "string" },
        activeUnitId: { type: "string" },
        lockId: { type: "string" },
        reason: { type: "string" },
      },
      required: ["action"],
    },
  },
  required: ["action"],
} as const;

const EMPTY_STATE: RepoTaskState = {
  goal: null,
  plan: [],
  touchedFiles: [],
  pendingTests: [],
  blockers: [],
  acceptanceCriteria: [],
  commitUnits: [],
  activeUnitId: null,
  workspaceLock: null,
  updatedAt: null,
};

function unique(values: string[] | undefined, fallback: string[]): string[] {
  if (!values) return fallback;
  return [...new Set(values.filter((v) => typeof v === "string" && v.trim().length > 0))];
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return unique(value.filter((v): v is string => typeof v === "string"), []);
}

function isUnitStatus(value: unknown): value is CodingCommitUnitStatus {
  return value === "pending" || value === "in_progress" || value === "blocked" || value === "completed";
}

function normalizeCommitUnit(raw: unknown): CodingCommitUnit | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const rec = raw as Record<string, unknown>;
  if (typeof rec.id !== "string" || rec.id.trim().length === 0) return null;
  const title = typeof rec.title === "string" && rec.title.trim().length > 0
    ? rec.title
    : rec.id;
  const status = isUnitStatus(rec.status) ? rec.status : "pending";
  const out: CodingCommitUnit = {
    id: rec.id,
    title,
    status,
    acceptanceCriteria: asStringArray(rec.acceptanceCriteria),
    changedFiles: asStringArray(rec.changedFiles),
    verificationCommands: asStringArray(rec.verificationCommands),
    blockers: asStringArray(rec.blockers),
    updatedAt: typeof rec.updatedAt === "string" ? rec.updatedAt : new Date(0).toISOString(),
  };
  if (typeof rec.description === "string") out.description = rec.description;
  if (typeof rec.commitSha === "string") out.commitSha = rec.commitSha;
  if (typeof rec.startedAt === "string") out.startedAt = rec.startedAt;
  if (typeof rec.completedAt === "string") out.completedAt = rec.completedAt;
  return out;
}

function normalizeWorkspaceLock(raw: unknown): CodingWorkspaceLock | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const rec = raw as Record<string, unknown>;
  const status = rec.status === "active" || rec.status === "released" ? rec.status : null;
  if (!status) return null;
  if (typeof rec.lockId !== "string" || typeof rec.ownerSessionKey !== "string") return null;
  if (typeof rec.goal !== "string" || typeof rec.acquiredAt !== "string" || typeof rec.updatedAt !== "string") {
    return null;
  }
  const out: CodingWorkspaceLock = {
    status,
    lockId: rec.lockId,
    ownerSessionKey: rec.ownerSessionKey,
    goal: rec.goal,
    acquiredAt: rec.acquiredAt,
    updatedAt: rec.updatedAt,
  };
  if (typeof rec.activeUnitId === "string") out.activeUnitId = rec.activeUnitId;
  if (typeof rec.releasedAt === "string") out.releasedAt = rec.releasedAt;
  if (typeof rec.releaseReason === "string") out.releaseReason = rec.releaseReason;
  return out;
}

function patchCommitUnits(
  current: CodingCommitUnit[],
  patches: CodingCommitUnitPatch[] | undefined,
  now: string,
): CodingCommitUnit[] {
  if (!patches) return current;
  const byId = new Map(current.map((unit) => [unit.id, unit]));
  for (const patch of patches) {
    if (!patch || typeof patch.id !== "string" || patch.id.trim().length === 0) continue;
    const prev = byId.get(patch.id);
    const nextStatus = patch.status ?? prev?.status ?? "pending";
    const next: CodingCommitUnit = {
      id: patch.id,
      title: patch.title ?? prev?.title ?? patch.id,
      status: nextStatus,
      acceptanceCriteria: unique(patch.acceptanceCriteria, prev?.acceptanceCriteria ?? []),
      changedFiles: unique(patch.changedFiles, prev?.changedFiles ?? []),
      verificationCommands: unique(patch.verificationCommands, prev?.verificationCommands ?? []),
      blockers: unique(patch.blockers, prev?.blockers ?? []),
      updatedAt: now,
    };
    if (patch.description ?? prev?.description) next.description = patch.description ?? prev?.description;
    if (patch.commitSha ?? prev?.commitSha) next.commitSha = patch.commitSha ?? prev?.commitSha;
    if (prev?.startedAt) next.startedAt = prev.startedAt;
    if (prev?.completedAt) next.completedAt = prev.completedAt;
    if (nextStatus === "in_progress" && !next.startedAt) next.startedAt = now;
    if (nextStatus === "completed" && !next.completedAt) next.completedAt = now;
    byId.set(patch.id, next);
  }
  return [...byId.values()];
}

function applyWorkspaceLockInput(
  current: CodingWorkspaceLock | null,
  input: CodingWorkspaceLockInput | undefined,
  ctx: ToolContext,
  goal: string | null,
  activeUnitId: string | null,
  now: string,
): { lock: CodingWorkspaceLock | null; error?: string } {
  if (!input) return { lock: current };
  if (input.action === "acquire") {
    if (
      current?.status === "active" &&
      current.ownerSessionKey !== ctx.sessionKey
    ) {
      return {
        lock: current,
        error: `workspace lock is active for session ${current.ownerSessionKey}: ${current.goal}`,
      };
    }
    return {
      lock: {
        status: "active",
        lockId: current?.status === "active"
          ? current.lockId
          : input.lockId ?? `lock_${Date.now().toString(36)}`,
        ownerSessionKey: ctx.sessionKey,
        goal: input.goal ?? goal ?? current?.goal ?? "coding task",
        activeUnitId: input.activeUnitId ?? activeUnitId ?? current?.activeUnitId,
        acquiredAt: current?.status === "active" ? current.acquiredAt : now,
        updatedAt: now,
      },
    };
  }
  if (current?.status === "active" && current.ownerSessionKey !== ctx.sessionKey) {
    return {
      lock: current,
      error: `workspace lock is active for session ${current.ownerSessionKey}: ${current.goal}`,
    };
  }
  if (!current) return { lock: null };
  return {
    lock: {
      ...current,
      status: "released",
      releasedAt: now,
      updatedAt: now,
      releaseReason: input.reason,
    },
  };
}

function sanitizeLedgerId(value: string | null | undefined): string {
  const cleaned = (value ?? "")
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^[._-]+|[._-]+$/g, "")
    .slice(0, 80);
  return cleaned.length > 0 ? cleaned : "repo-task";
}

function ledgerPathForState(state: RepoTaskState): string | undefined {
  if (!state.workspaceLock && state.commitUnits.length === 0 && !state.activeUnitId) {
    return undefined;
  }
  const firstUnitId = state.commitUnits[0]?.id;
  const id = sanitizeLedgerId(
    state.activeUnitId ??
      state.workspaceLock?.activeUnitId ??
      firstUnitId ??
      state.workspaceLock?.lockId ??
      state.goal,
  );
  return `.magi/coding/${id}/log.md`;
}

function valueOrNone(value: string | null | undefined): string {
  const trimmed = value?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : "none";
}

function markdownCommand(command: string): string {
  return `\`${command.replace(/`/g, "'")}\``;
}

function appendList(
  lines: string[],
  label: string,
  values: string[],
  format: (value: string) => string = (value) => value,
): void {
  lines.push(`${label}:`);
  if (values.length === 0) {
    lines.push("- none");
    return;
  }
  for (const value of values) {
    lines.push(`- ${format(value)}`);
  }
}

function renderCodingLedger(state: RepoTaskState, ledgerPath: string): string {
  const ledgerId = path.basename(path.dirname(ledgerPath));
  const lines: string[] = [
    "# Coding Task Ledger",
    "",
    `Ledger ID: ${ledgerId}`,
    `Goal: ${valueOrNone(state.goal)}`,
    `Updated: ${valueOrNone(state.updatedAt)}`,
    "",
    `Workspace lock: ${state.workspaceLock?.status ?? "none"}`,
  ];

  if (state.workspaceLock) {
    lines.push(
      `Lock ID: ${state.workspaceLock.lockId}`,
      `Owner session: ${state.workspaceLock.ownerSessionKey}`,
      `Active unit: ${valueOrNone(state.workspaceLock.activeUnitId ?? state.activeUnitId)}`,
      `Acquired: ${state.workspaceLock.acquiredAt}`,
      `Released: ${valueOrNone(state.workspaceLock.releasedAt)}`,
      `Release reason: ${valueOrNone(state.workspaceLock.releaseReason)}`,
    );
  } else {
    lines.push(`Active unit: ${valueOrNone(state.activeUnitId)}`);
  }

  lines.push("", "## Commit Units");
  if (state.commitUnits.length === 0) {
    lines.push("", "- none");
  } else {
    for (const unit of state.commitUnits) {
      lines.push(
        "",
        `### ${unit.id}`,
        `Title: ${unit.title}`,
        `Status: ${unit.status}`,
        `Description: ${valueOrNone(unit.description)}`,
        `Started: ${valueOrNone(unit.startedAt)}`,
        `Completed: ${valueOrNone(unit.completedAt)}`,
        `Updated: ${unit.updatedAt}`,
      );
      if (unit.commitSha) {
        lines.push(`Commit: ${unit.commitSha}`);
      }
      appendList(lines, "Acceptance criteria", unit.acceptanceCriteria);
      appendList(lines, "Changed files", unit.changedFiles);
      appendList(lines, "Verification", unit.verificationCommands, markdownCommand);
      appendList(lines, "Blockers", unit.blockers);
    }
  }

  const events: string[] = [];
  if (state.workspaceLock) {
    events.push(`${state.workspaceLock.acquiredAt} lock acquired by ${state.workspaceLock.ownerSessionKey}`);
    if (state.workspaceLock.releasedAt) {
      events.push(
        `${state.workspaceLock.releasedAt} lock released: ${state.workspaceLock.releaseReason ?? "released"}`,
      );
    }
  }
  for (const unit of state.commitUnits) {
    if (unit.startedAt) events.push(`${unit.startedAt} unit ${unit.id} started`);
    if (unit.completedAt) events.push(`${unit.completedAt} unit ${unit.id} completed`);
    if (unit.commitSha) {
      events.push(`${unit.completedAt ?? unit.updatedAt} commit ${unit.commitSha} recorded for unit ${unit.id}`);
    }
  }

  lines.push("", "## Events");
  if (events.length === 0) {
    lines.push("- none");
  } else {
    for (const event of events) {
      lines.push(`- ${event}`);
    }
  }

  return `${lines.join("\n")}\n`;
}

async function writeCodingLedger(ws: Workspace, state: RepoTaskState): Promise<string | undefined> {
  const ledgerPath = ledgerPathForState(state);
  if (!ledgerPath) return undefined;
  await ws.writeFile(ledgerPath, renderCodingLedger(state, ledgerPath));
  return ledgerPath;
}

async function readState(file: string): Promise<RepoTaskState> {
  try {
    const parsed = JSON.parse(await fs.readFile(file, "utf8")) as Partial<RepoTaskState>;
    return {
      goal: typeof parsed.goal === "string" ? parsed.goal : null,
      plan: Array.isArray(parsed.plan) ? parsed.plan.filter((v): v is string => typeof v === "string") : [],
      touchedFiles: Array.isArray(parsed.touchedFiles)
        ? parsed.touchedFiles.filter((v): v is string => typeof v === "string")
        : [],
      pendingTests: Array.isArray(parsed.pendingTests)
        ? parsed.pendingTests.filter((v): v is string => typeof v === "string")
        : [],
      blockers: Array.isArray(parsed.blockers)
        ? parsed.blockers.filter((v): v is string => typeof v === "string")
        : [],
      acceptanceCriteria: Array.isArray(parsed.acceptanceCriteria)
        ? parsed.acceptanceCriteria.filter((v): v is string => typeof v === "string")
        : [],
      commitUnits: Array.isArray(parsed.commitUnits)
        ? parsed.commitUnits.map(normalizeCommitUnit).filter((v): v is CodingCommitUnit => v !== null)
        : [],
      activeUnitId: typeof parsed.activeUnitId === "string" ? parsed.activeUnitId : null,
      workspaceLock: normalizeWorkspaceLock(parsed.workspaceLock),
      updatedAt: typeof parsed.updatedAt === "string" ? parsed.updatedAt : null,
    };
  } catch {
    return { ...EMPTY_STATE };
  }
}

export function makeRepoTaskStateTool(
  workspaceRoot: string,
): Tool<RepoTaskStateInput, RepoTaskStateOutput> {
  const defaultWorkspace = new Workspace(workspaceRoot);
  return {
    name: "RepoTaskState",
    description:
      "Read or update structured coding task state for a repository: goal, plan, commit units, active coding workspace lock, human-readable coding ledger, touched files, pending tests, blockers, and acceptance criteria. For repo coding work, acquire a workspaceLock and keep commitUnits current before editing source/test files.",
    inputSchema: INPUT_SCHEMA,
    permission: "write",
    mutatesWorkspace: true,
    isConcurrencySafe: false,
    validate(input) {
      if (!input || (input.action !== "read" && input.action !== "update")) {
        return "`action` must be 'read' or 'update'";
      }
      return null;
    },
    async execute(
      input: RepoTaskStateInput,
      ctx: ToolContext,
    ): Promise<ToolResult<RepoTaskStateOutput>> {
      const start = Date.now();
      try {
        const ws = ctx.spawnWorkspace ?? defaultWorkspace;
        const file = ws.resolve(".magi/repo-task-state.json");
        const current = await readState(file);
        const now = new Date().toISOString();
        const nextGoal = input.goal ?? current.goal;
        const nextActiveUnitId = input.activeUnitId ?? current.activeUnitId;
        const nextCommitUnits = patchCommitUnits(current.commitUnits, input.commitUnits, now);
        const lockResult = applyWorkspaceLockInput(
          current.workspaceLock,
          input.workspaceLock,
          ctx,
          nextGoal,
          nextActiveUnitId,
          now,
        );
        const state: RepoTaskState =
          input.action === "update"
            ? {
                goal: nextGoal,
                plan: unique(input.plan, current.plan),
                touchedFiles: unique(input.touchedFiles, current.touchedFiles),
                pendingTests: unique(input.pendingTests, current.pendingTests),
                blockers: unique(input.blockers, current.blockers),
                acceptanceCriteria: unique(input.acceptanceCriteria, current.acceptanceCriteria),
                commitUnits: nextCommitUnits,
                activeUnitId: nextActiveUnitId,
                workspaceLock: lockResult.lock,
                updatedAt: now,
              }
            : current;
        if (input.action === "update" && lockResult.error) {
          return {
            status: "error",
            errorCode: "workspace_lock_active",
            errorMessage: lockResult.error,
            output: {
              path: ".magi/repo-task-state.json",
              state,
            },
            durationMs: Date.now() - start,
            metadata: { repoTaskState: true, workspaceLock: "active_conflict" },
          };
        }
        let ledgerPath = ledgerPathForState(state);
        if (input.action === "update") {
          await fs.mkdir(path.dirname(file), { recursive: true });
          await fs.writeFile(file, `${JSON.stringify(state, null, 2)}\n`, "utf8");
          ledgerPath = await writeCodingLedger(ws, state);
        }
        return {
          status: "ok",
          output: {
            path: ".magi/repo-task-state.json",
            state,
            ...(ledgerPath ? { ledgerPath } : {}),
          },
          durationMs: Date.now() - start,
          metadata: { repoTaskState: true, ...(ledgerPath ? { ledgerPath } : {}) },
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
