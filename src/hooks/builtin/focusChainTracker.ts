import fs from "node:fs/promises";
import path from "node:path";
import type { RegisteredHook } from "../types.js";

export interface FocusChainTrackerOptions {
  workspaceRoot: string;
}

interface TaskProgress {
  currentStep: string;
  completedSteps: string[];
  pendingSteps: string[];
  blockers?: string[];
}

function isValidTaskProgress(v: unknown): v is TaskProgress {
  if (!v || typeof v !== "object") return false;
  const obj = v as Record<string, unknown>;
  if (typeof obj["currentStep"] !== "string") return false;
  if (!Array.isArray(obj["completedSteps"])) return false;
  if (!Array.isArray(obj["pendingSteps"])) return false;
  if (obj["blockers"] !== undefined && !Array.isArray(obj["blockers"])) return false;
  return true;
}

export function makeFocusChainTrackerHook(
  opts: FocusChainTrackerOptions,
): RegisteredHook<"afterToolUse"> {
  return {
    name: "builtin:focus-chain-tracker",
    point: "afterToolUse",
    priority: 91,
    blocking: false,
    timeoutMs: 3_000,
    handler: async (args, ctx) => {
      if (!args.input || typeof args.input !== "object") return;

      const inp = args.input as Record<string, unknown>;
      const raw = inp["task_progress"];
      if (raw === undefined) return;

      if (!isValidTaskProgress(raw)) {
        ctx.log("warn", "[focus-chain-tracker] malformed task_progress, skipping");
        return;
      }

      const coreAgentDir = path.join(opts.workspaceRoot, ".core-agent");
      const filePath = path.join(coreAgentDir, "focus-chain.json");

      try {
        await fs.mkdir(coreAgentDir, { recursive: true });

        const data: TaskProgress & { updatedAt: number } = {
          currentStep: raw.currentStep,
          completedSteps: raw.completedSteps,
          pendingSteps: raw.pendingSteps,
          ...(raw.blockers ? { blockers: raw.blockers } : {}),
          updatedAt: Date.now(),
        };

        await fs.writeFile(filePath, JSON.stringify(data, null, 2));

        ctx.log("info", "[focus-chain-tracker] updated", {
          currentStep: raw.currentStep,
        });
      } catch (err) {
        ctx.log("warn", "[focus-chain-tracker] write failed", {
          error: err instanceof Error ? err.message : String(err),
        });
      }
    },
  };
}
