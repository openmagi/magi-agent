import fs from "node:fs/promises";
import path from "node:path";
import type { RegisteredHook, HookContext } from "../types.js";

export interface FocusChainInjectorOptions {
  workspaceRoot: string;
}

interface PersistedFocusChain {
  currentStep: string;
  completedSteps: string[];
  pendingSteps: string[];
  blockers?: string[];
  updatedAt: number;
}

function isPersistedFocusChain(v: unknown): v is PersistedFocusChain {
  if (!v || typeof v !== "object") return false;
  const obj = v as Record<string, unknown>;
  return (
    typeof obj["currentStep"] === "string" &&
    Array.isArray(obj["completedSteps"]) &&
    Array.isArray(obj["pendingSteps"])
  );
}

function stripStepNumber(step: string): string {
  return step.replace(/^\d+\/\d+:\s*/, "");
}

function formatFocusChainFence(data: PersistedFocusChain): string {
  const lines: string[] = [];
  lines.push("<focus_chain>");
  lines.push(`Current: ${data.currentStep}`);

  if (data.completedSteps.length > 0) {
    const items = data.completedSteps
      .map((s, i) => `[${i + 1}] ${stripStepNumber(s)}`)
      .join(", ");
    lines.push(`Done: ${items}`);
  }

  if (data.pendingSteps.length > 0) {
    const baseIndex = data.completedSteps.length + 2;
    const items = data.pendingSteps
      .map((s, i) => `[${baseIndex + i}] ${stripStepNumber(s)}`)
      .join(", ");
    lines.push(`Pending: ${items}`);
  }

  if (data.blockers && data.blockers.length > 0) {
    lines.push(`Blockers: ${data.blockers.join("; ")}`);
  }

  lines.push("</focus_chain>");
  return lines.join("\n");
}

export function makeFocusChainInjectorHook(
  opts: FocusChainInjectorOptions,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:focus-chain-injector",
    point: "beforeLLMCall",
    priority: 10,
    blocking: false,
    handler: async (args, ctx: HookContext) => {
      try {
        if (args.iteration > 0) return { action: "continue" };

        const filePath = path.join(
          opts.workspaceRoot,
          ".core-agent",
          "focus-chain.json",
        );

        let raw: string;
        try {
          raw = await fs.readFile(filePath, "utf-8");
        } catch {
          return { action: "continue" };
        }

        let parsed: unknown;
        try {
          parsed = JSON.parse(raw);
        } catch {
          ctx.log("warn", "[focus-chain-injector] invalid JSON, skipping");
          return { action: "continue" };
        }

        if (!isPersistedFocusChain(parsed)) {
          return { action: "continue" };
        }

        const fence = formatFocusChainFence(parsed);
        const nextSystem = args.system
          ? `${args.system}\n\n${fence}`
          : fence;

        return {
          action: "replace",
          value: { ...args, system: nextSystem },
        };
      } catch (err) {
        ctx.log("warn", "[focus-chain-injector] inject failed; turn continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
