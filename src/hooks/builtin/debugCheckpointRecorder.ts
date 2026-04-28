import type { RegisteredHook } from "../types.js";
import type { DebugWorkflow } from "../../debug/DebugWorkflow.js";

export interface DebugCheckpointRecorderOptions {
  workflow: DebugWorkflow;
}

const INSPECTION_TOOLS = new Set(["FileRead", "Grep", "Glob", "ArtifactRead", "ArtifactList"]);
const PATCH_TOOLS = new Set(["FileWrite", "FileEdit", "ArtifactUpdate"]);
const HYPOTHESIS_RE =
  /(?:likely cause|root cause|cause was|원인(?:은|은\s)|원인(?:이|이\s)|원인은|원인으로)/i;
const VERIFY_COMMAND_RE =
  /\bnpm\s+(?:run\s+)?(?:test|lint|build|qa|typecheck)\b|\b(?:vitest|jest|pytest|ruff|mypy)\b|\bgo\s+test\b|\bcargo\s+(?:test|check|clippy)\b|\b(?:tsc|eslint)\b/i;
const INSPECT_COMMAND_RE =
  /\b(?:cat|sed\s+-n|head|tail|rg|grep|git\s+diff|git\s+show|ls|find)\b/i;
const PATCH_COMMAND_RE =
  /\bapply_patch\b|\bsed\s+-i\b|\bperl\s+-pi\b|\bcat\b[\s\S]{0,40}>\s*\S+|\btee\b[\s\S]{0,40}\S+/i;

function commandFromInput(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const cmd = (input as Record<string, unknown>).command;
  return typeof cmd === "string" ? cmd : "";
}

function resultSucceeded(result: { status?: string; ok?: boolean }): boolean {
  return result.ok === true || result.status === "ok" || result.status === "success";
}

export function makeDebugAfterToolCheckpointHook(
  opts: DebugCheckpointRecorderOptions,
): RegisteredHook<"afterToolUse"> {
  return {
    name: "builtin:debug-checkpoint-recorder",
    point: "afterToolUse",
    priority: 90,
    blocking: false,
    handler: async ({ toolName, input, result }, ctx) => {
      const state = opts.workflow.getTurnState(ctx.sessionKey, ctx.turnId);
      if (!state?.classified || !resultSucceeded(result)) {
        return { action: "continue" };
      }

      if (INSPECTION_TOOLS.has(toolName)) {
        opts.workflow.recordInspection(ctx.sessionKey, ctx.turnId, toolName);
      }
      if (PATCH_TOOLS.has(toolName)) {
        opts.workflow.recordPatch(ctx.sessionKey, ctx.turnId, toolName);
      }
      if (toolName === "Bash") {
        const command = commandFromInput(input);
        if (INSPECT_COMMAND_RE.test(command)) {
          opts.workflow.recordInspection(ctx.sessionKey, ctx.turnId, command);
        }
        if (PATCH_COMMAND_RE.test(command)) {
          opts.workflow.recordPatch(ctx.sessionKey, ctx.turnId, command);
        }
        if (VERIFY_COMMAND_RE.test(command)) {
          opts.workflow.recordVerification(ctx.sessionKey, ctx.turnId, command);
        }
      }

      return { action: "continue" };
    },
  };
}

export function makeDebugCommitCheckpointHook(
  opts: DebugCheckpointRecorderOptions,
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:debug-commit-checkpoint",
    point: "beforeCommit",
    priority: 80,
    blocking: false,
    handler: async ({ assistantText }, ctx) => {
      const state = opts.workflow.getTurnState(ctx.sessionKey, ctx.turnId);
      if (!state?.classified) {
        return { action: "continue" };
      }
      if (HYPOTHESIS_RE.test(assistantText)) {
        opts.workflow.recordHypothesis(ctx.sessionKey, ctx.turnId, assistantText);
      }
      return { action: "continue" };
    },
  };
}
