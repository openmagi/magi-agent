/**
 * TaskList — T2-10.
 *
 * Lists background tasks from the
 * BackgroundTaskRegistry. Mirrors Claude Code's AgentTool "TaskList"
 * surface.
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type {
  BackgroundTaskRecord,
  BackgroundTaskRegistry,
  BackgroundTaskStatus,
} from "../tasks/BackgroundTaskRegistry.js";
import { errorResult } from "../util/toolResult.js";
import { emitRunningBackgroundTask } from "./TaskGet.js";

export interface TaskListInput {
  status?: BackgroundTaskStatus;
  limit?: number;
  cursor?: string;
  sessionKey?: string;
}

export interface TaskListOutput {
  tasks: BackgroundTaskRecord[];
  nextCursor?: string;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    status: {
      type: "string",
      enum: ["running", "completed", "aborted", "failed"],
      description: "Filter by task status.",
    },
    limit: {
      type: "integer",
      minimum: 1,
      maximum: 200,
      description: "Page size (default 50, max 200).",
    },
    cursor: {
      type: "string",
      description: "Opaque cursor returned by a previous page.",
    },
    sessionKey: {
      type: "string",
      description:
        "Restrict results to one session. When omitted, returns tasks across all sessions this agent tracked.",
    },
  },
} as const;

export function makeTaskListTool(
  registry: BackgroundTaskRegistry,
): Tool<TaskListInput, TaskListOutput> {
  return {
    name: "TaskList",
    description:
      "List background tasks spawned via SpawnAgent deliver='background' or Bash runInBackground. Optionally filter by status (running|completed|aborted|failed), limit, cursor, or sessionKey. Returns a page of BackgroundTaskRecord entries sorted newest-first.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    kind: "core",
    async execute(
      input: TaskListInput,
      ctx: ToolContext,
    ): Promise<ToolResult<TaskListOutput>> {
      const start = Date.now();
      try {
        const page = await registry.list({
          ...(input.status ? { status: input.status } : {}),
          ...(input.sessionKey ? { sessionKey: input.sessionKey } : {}),
          ...(input.limit ? { limit: input.limit } : {}),
          ...(input.cursor ? { cursor: input.cursor } : {}),
        });
        for (const task of page.tasks) {
          emitRunningBackgroundTask(ctx, task);
        }
        return {
          status: "ok",
          output: page.nextCursor
            ? { tasks: page.tasks, nextCursor: page.nextCursor }
            : { tasks: page.tasks },
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
