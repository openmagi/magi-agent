import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";
import type { EnterPlanModeResult } from "../plan/PlanLifecycle.js";

export interface PlanModeEntryController {
  enterPlanMode(input: { turnId: string }): Promise<EnterPlanModeResult>;
}

export function makeEnterPlanModeTool(
  getController: (turnId: string) => PlanModeEntryController | null,
): Tool<Record<string, never>, EnterPlanModeResult> {
  return {
    name: "EnterPlanMode",
    description:
      "Enter planning mode before proposing a plan. This immediately restricts the rest of the session to read-only planning tools until ExitPlanMode produces a plan and the user approves it.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
    permission: "meta",
    async execute(
      _input: Record<string, never>,
      ctx: ToolContext,
    ): Promise<ToolResult<EnterPlanModeResult>> {
      const start = Date.now();
      try {
        const controller = getController(ctx.turnId);
        if (!controller) {
          return {
            status: "error",
            errorCode: "no_controller",
            errorMessage: "EnterPlanMode called but no plan-mode controller is registered",
            durationMs: Date.now() - start,
          };
        }
        const output = await controller.enterPlanMode({ turnId: ctx.turnId });
        ctx.emitAgentEvent?.({
          type: "plan_lifecycle",
          state: "entered",
          previousMode: output.previousMode,
        });
        return {
          status: "ok",
          output,
          durationMs: Date.now() - start,
        };
      } catch (err) {
        return errorResult(err, start);
      }
    },
  };
}
