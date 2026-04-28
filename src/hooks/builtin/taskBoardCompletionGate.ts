import type { HookContext, RegisteredHook } from "../types.js";
import {
  isVerificationTask,
  readBoard,
  taskFilePath,
  type TaskBoardEntry,
} from "../../tools/TaskBoard.js";
import { matchesCompletionClaim } from "./completionEvidenceGate.js";

const MAX_RETRIES = 1;

export interface TaskBoardCompletionGateOptions {
  sessionsDir: string;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_TASK_BOARD_COMPLETION_GATE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function activeTasks(tasks: TaskBoardEntry[]): TaskBoardEntry[] {
  return tasks.filter((task) => task.status === "in_progress");
}

function completedTasks(tasks: TaskBoardEntry[]): TaskBoardEntry[] {
  return tasks.filter((task) => task.status === "completed");
}

export async function readActiveTaskBoardItems(
  sessionsDir: string,
  sessionKey: string,
): Promise<TaskBoardEntry[]> {
  const file = taskFilePath(sessionsDir, sessionKey);
  const tasks = await readBoard(file);
  return activeTasks(tasks);
}

export async function readTaskBoardItems(
  sessionsDir: string,
  sessionKey: string,
): Promise<TaskBoardEntry[]> {
  const file = taskFilePath(sessionsDir, sessionKey);
  return readBoard(file);
}

export function makeTaskBoardCompletionGateHook(
  opts: TaskBoardCompletionGateOptions,
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:task-board-completion-gate",
    point: "beforeCommit",
    priority: 86,
    blocking: true,
    timeoutMs: 1_000,
    handler: async ({ assistantText, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!matchesCompletionClaim(assistantText)) return { action: "continue" };

        const tasks = await readTaskBoardItems(opts.sessionsDir, ctx.sessionKey);
        const active = activeTasks(tasks);
        if (active.length > 0) {
          if (retryCount >= MAX_RETRIES) {
            ctx.log("warn", "[task-board-completion-gate] retry exhausted; failing open", {
              active: active.map((task) => task.id),
            });
            return { action: "continue" };
          }

          ctx.emit({
            type: "rule_check",
            ruleId: "task-board-completion-gate",
            verdict: "violation",
            detail: `${active.length} task board item(s) still in_progress`,
          });
          const taskList = active
            .slice(0, 5)
            .map((task) => `- ${task.id}: ${task.title}`)
            .join("\n");
          return {
            action: "block",
            reason: [
              "[RETRY:TASK_BOARD_FOLLOW_THROUGH] You are claiming completion while TaskBoard still has in-progress work.",
              "Update the task board truthfully before closing the turn, or change the final answer to report remaining work.",
              taskList,
            ].join("\n"),
          };
        }

        const completed = completedTasks(tasks);
        if (completed.length < 3 || tasks.some((task) => isVerificationTask(task))) {
          return { action: "continue" };
        }
        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[task-board-completion-gate] verification retry exhausted; failing open", {
            completed: completed.map((task) => task.id),
          });
          return { action: "continue" };
        }
        ctx.emit({
          type: "rule_check",
          ruleId: "task-board-verification-gate",
          verdict: "violation",
          detail: `${completed.length} completed task(s) but no verification/test/build/lint/qa task`,
        });
        return {
          action: "block",
          reason: [
            "[RETRY:TASK_BOARD_VERIFICATION] TaskBoard shows three or more completed tasks but no verification task.",
            "Add or complete a verification/test/build/lint/QA task, or change the final answer to report that verification is still missing.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[task-board-completion-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
