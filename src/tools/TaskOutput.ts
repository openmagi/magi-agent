/**
 * TaskOutput — T2-10.
 *
 * Narrow projection of a BackgroundTaskRecord suitable for the parent
 * agent to ingest as tool_result context without pulling the entire
 * record.
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type {
  BackgroundTaskRegistry,
  BackgroundTaskStatus,
} from "../tasks/BackgroundTaskRegistry.js";
import { errorResult } from "../util/toolResult.js";

export interface TaskOutputInput {
  taskId: string;
}

export interface TaskOutputOutput {
  taskId: string;
  status: BackgroundTaskStatus;
  resultText?: string;
  error?: string;
  durationMs: number;
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    taskId: { type: "string", description: "taskId returned by SpawnAgent or Bash runInBackground." },
  },
  required: ["taskId"],
} as const;

export function makeTaskOutputTool(
  registry: BackgroundTaskRegistry,
): Tool<TaskOutputInput, TaskOutputOutput> {
  return {
    name: "TaskOutput",
    description:
      "Return the final output (resultText, error, durationMs) of a background task. If the task is still running, returns its current status without blocking.",
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
      input: TaskOutputInput,
      _ctx: ToolContext,
    ): Promise<ToolResult<TaskOutputOutput>> {
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
        const durationMs =
          (record.finishedAt ?? Date.now()) - record.startedAt;
        const output: TaskOutputOutput = {
          taskId: record.taskId,
          status: record.status,
          durationMs,
        };
        if (record.resultText !== undefined) output.resultText = record.resultText;
        if (record.error !== undefined) output.error = record.error;
        return { status: "ok", output, durationMs: Date.now() - start };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
