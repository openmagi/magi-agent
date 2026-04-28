import type { RegisteredHook } from "../types.js";
import type { DebugWorkflow } from "../../debug/DebugWorkflow.js";

export interface DebugTurnClassifierOptions {
  workflow: DebugWorkflow;
}

export function makeDebugTurnClassifierHook(
  opts: DebugTurnClassifierOptions,
): RegisteredHook<"beforeTurnStart"> {
  return {
    name: "builtin:debug-turn-classifier",
    point: "beforeTurnStart",
    priority: 10,
    blocking: false,
    handler: async ({ userMessage }, ctx) => {
      opts.workflow.classifyTurn(ctx.sessionKey, ctx.turnId, userMessage);
      return { action: "continue" };
    },
  };
}
