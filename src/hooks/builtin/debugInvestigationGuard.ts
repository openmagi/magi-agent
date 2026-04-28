import type { RegisteredHook } from "../types.js";
import type { DebugWorkflow } from "../../debug/DebugWorkflow.js";

export interface DebugInvestigationGuardOptions {
  workflow: DebugWorkflow;
}

function commandFromInput(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const cmd = (input as Record<string, unknown>).command;
  return typeof cmd === "string" ? cmd : "";
}

function isPatchCommand(command: string): boolean {
  return /\bapply_patch\b|\bsed\s+-i\b|\bperl\s+-pi\b|\bcat\b[\s\S]{0,40}>\s*\S+|\btee\b[\s\S]{0,40}\S+/i.test(
    command,
  );
}

function isPatchTool(toolName: string, input: unknown): boolean {
  if (toolName === "FileWrite" || toolName === "FileEdit" || toolName === "ArtifactUpdate") {
    return true;
  }
  if (toolName === "Bash") {
    return isPatchCommand(commandFromInput(input));
  }
  return false;
}

export function makeDebugInvestigationGuardHook(
  opts: DebugInvestigationGuardOptions,
): RegisteredHook<"beforeToolUse"> {
  return {
    name: "builtin:debug-investigation-guard",
    point: "beforeToolUse",
    priority: 35,
    blocking: true,
    handler: async ({ toolName, input }, ctx) => {
      const state = opts.workflow.getTurnState(ctx.sessionKey, ctx.turnId);
      if (!state?.classified) {
        return { action: "continue" };
      }
      if (!isPatchTool(toolName, input)) {
        return { action: "continue" };
      }
      if (state.investigated) {
        return { action: "continue" };
      }
      return {
        action: "block",
        reason: [
          "[RETRY:DEBUG_INVESTIGATE] This turn is in debug workflow mode.",
          "Gather evidence first before patching: reproduce, inspect logs/files, or run the failing check.",
          "Only patch once the turn has investigation evidence.",
        ].join("\n"),
      };
    },
  };
}
