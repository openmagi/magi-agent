/**
 * TaskGet — T2-10.
 *
 * Fetches a compact BackgroundTaskRecord projection for a given taskId.
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type {
  BackgroundTaskRecord,
  BackgroundTaskRegistry,
} from "../tasks/BackgroundTaskRegistry.js";
import { errorResult } from "../util/toolResult.js";

export interface TaskGetInput {
  taskId: string;
  includePrompt?: boolean;
}

export type TaskGetOutput = Omit<BackgroundTaskRecord, "prompt"> & {
  prompt?: string;
  promptPreview?: string;
  promptChars: number;
  promptOmitted?: boolean;
};

const PROMPT_PREVIEW_CHARS = 600;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    taskId: { type: "string", description: "taskId returned by SpawnAgent or Bash runInBackground." },
    includePrompt: {
      type: "boolean",
      description:
        "When true, include the full original child prompt. Defaults to false to keep repeated status polling compact.",
    },
  },
  required: ["taskId"],
} as const;

function taskGetProjection(
  record: BackgroundTaskRecord,
  includePrompt: boolean,
): TaskGetOutput {
  const { prompt, ...rest } = record;
  if (includePrompt) {
    return {
      ...rest,
      prompt,
      promptChars: prompt.length,
    };
  }
  const omitted = Math.max(0, prompt.length - PROMPT_PREVIEW_CHARS);
  const promptPreview =
    omitted > 0
      ? `${prompt.slice(0, PROMPT_PREVIEW_CHARS).trimEnd()}\n[truncated: ${omitted} chars omitted]`
      : prompt;
  return {
    ...rest,
    promptPreview,
    promptChars: prompt.length,
    promptOmitted: true,
  };
}

export function makeTaskGetTool(
  registry: BackgroundTaskRegistry,
): Tool<TaskGetInput, TaskGetOutput> {
  return {
    name: "TaskGet",
    description:
      "Return a compact BackgroundTaskRecord projection (status, progress, result, error, attempts, toolCallCount, spawnDir, artifacts) for a background task. The original child prompt is omitted by default and replaced with promptPreview/promptChars; pass includePrompt=true only when the full prompt is needed. Errors with `not_found` when the taskId is unknown.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    shouldDefer: true,
    kind: "core",
    validate(input) {
      if (!input || typeof input.taskId !== "string" || input.taskId.length === 0) {
        return "`taskId` is required";
      }
      if (
        input.includePrompt !== undefined &&
        typeof input.includePrompt !== "boolean"
      ) {
        return "`includePrompt` must be a boolean";
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
        return {
          status: "ok",
          output: taskGetProjection(record, input.includePrompt === true),
          durationMs: Date.now() - start,
        };
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
