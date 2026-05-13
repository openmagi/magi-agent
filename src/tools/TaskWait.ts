/**
 * TaskWait — blocks until one or more background tasks complete.
 *
 * Eliminates polling overhead: instead of repeated TaskGet calls or
 * Bash sleep loops, the model calls TaskWait once and gets results
 * when all tasks reach a terminal state.
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import type {
  BackgroundTaskRecord,
  BackgroundTaskRegistry,
} from "../tasks/BackgroundTaskRegistry.js";
import { errorResult } from "../util/toolResult.js";

export interface TaskWaitInput {
  taskIds: string[];
  timeout_ms?: number;
}

export interface TaskWaitResult {
  taskId: string;
  status: BackgroundTaskRecord["status"];
  resultText?: string;
  toolCallCount?: number;
  error?: string;
  durationMs?: number;
}

export interface TaskWaitOutput {
  results: TaskWaitResult[];
  timedOut: boolean;
}

const MAX_WAIT_TIMEOUT_MS = 600_000;
const DEFAULT_WAIT_TIMEOUT_MS = 300_000;
const MAX_TASK_IDS = 10;

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    taskIds: {
      type: "array",
      items: { type: "string" },
      minItems: 1,
      maxItems: MAX_TASK_IDS,
      description:
        "Array of taskIds (from SpawnAgent background) to wait for. All must reach a terminal state (completed/failed/aborted) before the tool returns.",
    },
    timeout_ms: {
      type: "integer",
      minimum: 5000,
      maximum: MAX_WAIT_TIMEOUT_MS,
      description: `Max wait time in ms. Default ${DEFAULT_WAIT_TIMEOUT_MS}ms, max ${MAX_WAIT_TIMEOUT_MS}ms. If tasks haven't completed by then, returns partial results with timedOut=true.`,
    },
  },
  required: ["taskIds"],
} as const;

function projectResult(record: BackgroundTaskRecord): TaskWaitResult {
  const durationMs = record.finishedAt
    ? record.finishedAt - record.startedAt
    : undefined;
  return {
    taskId: record.taskId,
    status: record.status,
    ...(record.resultText !== undefined
      ? { resultText: truncateResult(record.resultText) }
      : {}),
    ...(record.toolCallCount !== undefined
      ? { toolCallCount: record.toolCallCount }
      : {}),
    ...(record.error !== undefined ? { error: record.error } : {}),
    ...(durationMs !== undefined ? { durationMs } : {}),
  };
}

function truncateResult(text: string, limit = 2000): string {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit).trimEnd()}\n[truncated: ${text.length - limit} chars omitted]`;
}

export function makeTaskWaitTool(
  registry: BackgroundTaskRegistry,
): Tool<TaskWaitInput, TaskWaitOutput> {
  return {
    name: "TaskWait",
    description:
      "Block until all specified background tasks reach a terminal state (completed/failed/aborted). Use this instead of polling TaskGet in a loop or using Bash sleep. Returns all task results at once. Much more efficient than repeated TaskGet calls — zero LLM roundtrips while waiting.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    kind: "core",
    validate(input) {
      if (
        !input ||
        !Array.isArray(input.taskIds) ||
        input.taskIds.length === 0
      ) {
        return "`taskIds` must be a non-empty array";
      }
      if (input.taskIds.length > MAX_TASK_IDS) {
        return `\`taskIds\` cannot exceed ${MAX_TASK_IDS} entries`;
      }
      if (input.taskIds.some((id) => typeof id !== "string" || id.length === 0)) {
        return "each taskId must be a non-empty string";
      }
      return null;
    },
    async execute(
      input: TaskWaitInput,
      ctx: ToolContext,
    ): Promise<ToolResult<TaskWaitOutput>> {
      const start = Date.now();
      const timeoutMs = Math.min(
        MAX_WAIT_TIMEOUT_MS,
        Math.max(5000, input.timeout_ms ?? DEFAULT_WAIT_TIMEOUT_MS),
      );
      const deadline = start + timeoutMs;

      try {
        const notFound: string[] = [];
        for (const taskId of input.taskIds) {
          const rec = await registry.get(taskId);
          if (!rec) notFound.push(taskId);
        }
        if (notFound.length > 0) {
          return {
            status: "error",
            errorCode: "not_found",
            errorMessage: `taskId(s) not found: ${notFound.join(", ")}`,
            durationMs: Date.now() - start,
          };
        }

        const timeoutController = new AbortController();
        const timer = setTimeout(
          () => timeoutController.abort(),
          Math.max(0, deadline - Date.now()),
        );
        const onParentAbort = (): void => timeoutController.abort();
        ctx.abortSignal.addEventListener("abort", onParentAbort, { once: true });

        try {
          const waitPromises = input.taskIds.map((taskId) =>
            registry.waitForCompletion(taskId, {
              signal: timeoutController.signal,
            }),
          );
          const records = await Promise.all(waitPromises);

          const timedOut = timeoutController.signal.aborted && !ctx.abortSignal.aborted;
          const results: TaskWaitResult[] = [];
          for (let i = 0; i < input.taskIds.length; i++) {
            const rec = records[i];
            if (rec) {
              results.push(projectResult(rec));
            } else {
              const current = await registry.get(input.taskIds[i]!);
              results.push({
                taskId: input.taskIds[i]!,
                status: current?.status ?? "running",
                ...(current?.error ? { error: current.error } : {}),
              });
            }
          }

          return {
            status: "ok",
            output: { results, timedOut },
            durationMs: Date.now() - start,
          };
        } finally {
          clearTimeout(timer);
          ctx.abortSignal.removeEventListener("abort", onParentAbort);
        }
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
