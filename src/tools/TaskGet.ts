/**
 * TaskGet — T2-10.
 *
 * Fetches the full BackgroundTaskRecord for a given taskId.
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type {
  BackgroundTaskRecord,
  BackgroundTaskRegistry,
} from "../tasks/BackgroundTaskRegistry.js";
import { errorResult } from "../util/toolResult.js";

export interface TaskGetInput {
  taskId: string;
}

export type TaskGetOutput = BackgroundTaskRecord;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    taskId: { type: "string", description: "taskId returned by SpawnAgent or Bash runInBackground." },
  },
  required: ["taskId"],
} as const;

export function makeTaskGetTool(
  registry: BackgroundTaskRegistry,
): Tool<TaskGetInput, TaskGetOutput> {
  return {
    name: "TaskGet",
    description:
      "Return the full BackgroundTaskRecord (status, progress, result, error, attempts, toolCallCount, spawnDir, artifacts) for a background task. Errors with `not_found` when the taskId is unknown.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    kind: "core",
    validate(input) {
      if (!input || typeof input.taskId !== "string" || input.taskId.length === 0) {
        return "`taskId` is required";
      }
      return null;
    },
    async execute(
      input: TaskGetInput,
      ctx: ToolContext,
    ): Promise<ToolResult<TaskGetOutput>> {
      const start = Date.now();
      try {
        const record = await registry.get(input.taskId);
        if (!record) {
          return {
            status: "error",
            errorCode: "not_found",
            errorMessage: `taskId ${input.taskId} not found`,
            durationMs: Date.now() - start,
          };
        }
        emitRunningBackgroundTask(ctx, record);
        return { status: "ok", output: record, durationMs: Date.now() - start };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}

export function emitRunningBackgroundTask(
  ctx: Pick<ToolContext, "emitAgentEvent">,
  record: BackgroundTaskRecord,
): void {
  if (record.status !== "running") return;
  const latestProgress = record.progress?.at(-1)?.label;
  ctx.emitAgentEvent?.({
    type: "background_task",
    taskId: record.taskId,
    persona: record.persona,
    status: record.status,
    ...(latestProgress ? { detail: latestProgress } : {}),
  });
}
