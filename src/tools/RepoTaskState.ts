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
  updatedAt: string | null;
}

export interface RepoTaskStateInput {
  action: "read" | "update";
  goal?: string;
  plan?: string[];
  touchedFiles?: string[];
  pendingTests?: string[];
  blockers?: string[];
  acceptanceCriteria?: string[];
}

export interface RepoTaskStateOutput {
  path: string;
  state: RepoTaskState;
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
  updatedAt: null,
};

function unique(values: string[] | undefined, fallback: string[]): string[] {
  if (!values) return fallback;
  return [...new Set(values.filter((v) => typeof v === "string" && v.trim().length > 0))];
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
      "Read or update structured coding task state for a repository: goal, plan, touched files, pending tests, blockers, and acceptance criteria.",
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
        const state =
          input.action === "update"
            ? {
                goal: input.goal ?? current.goal,
                plan: unique(input.plan, current.plan),
                touchedFiles: unique(input.touchedFiles, current.touchedFiles),
                pendingTests: unique(input.pendingTests, current.pendingTests),
                blockers: unique(input.blockers, current.blockers),
                acceptanceCriteria: unique(input.acceptanceCriteria, current.acceptanceCriteria),
                updatedAt: new Date().toISOString(),
              }
            : current;
        if (input.action === "update") {
          await fs.mkdir(path.dirname(file), { recursive: true });
          await fs.writeFile(file, `${JSON.stringify(state, null, 2)}\n`, "utf8");
        }
        return {
          status: "ok",
          output: {
            path: ".magi/repo-task-state.json",
            state,
          },
          durationMs: Date.now() - start,
          metadata: { repoTaskState: true },
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
