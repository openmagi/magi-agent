/**
 * ExitPlanMode — used by the model to submit a final plan artifact.
 * Emits a durable plan approval request and keeps the session in plan
 * mode until the user approves it.
 *
 * T2-08 — calling {@link PlanModeController.exitPlanMode} is wired
 * through the Turn instance, which in turn invokes
 * `Session.exitPlanMode()`. That method restores the pre-plan
 * permission posture captured on entry (default / auto / bypass),
 * rather than hard-coding back to `default`. Resolves the plan-vs-
 * permission coupling from DEBT-PLAN-PERMS-01.
 *
 * Design reference: §7.2.
 */

import type { Tool, ToolContext, ToolResult } from "../Tool.js";
import { errorResult } from "../util/toolResult.js";
import type { SubmitPlanResult } from "../plan/PlanLifecycle.js";

export interface ExitPlanModeInput {
  plan: string;
}

export interface ExitPlanModeOutput {
  planApproved: false;
  planId: string;
  requestId: string;
  state: "awaiting_approval";
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    plan: {
      type: "string",
      minLength: 1,
      description:
        "The final plan text (typically markdown bullets). This will be shown to the user for approval before execute-mode tools are unlocked.",
    },
  },
  required: ["plan"],
} as const;

/**
 * Surface that the ExitPlanMode tool needs to flip the containing
 * Turn's plan-mode state. Kept as a bare interface so we don't have to
 * import Turn here (which would create a cycle with Tool.ts).
 */
export interface PlanModeController {
  /** Returns true if the turn is currently in plan mode. */
  isPlanMode(): boolean;
  /** Persist the plan approval request without leaving plan mode. */
  submitPlan(input: {
    turnId: string;
    plan: string;
    emitAgentEvent?: (event: unknown) => void;
  }): Promise<SubmitPlanResult>;
}

export function makeExitPlanModeTool(
  getController: (turnId: string) => PlanModeController | null,
): Tool<ExitPlanModeInput, ExitPlanModeOutput> {
  return {
    name: "ExitPlanMode",
    description:
      "Signal that you are done planning and ready to execute. Pass the final plan text. The runtime emits a `plan_ready` event; the client UI may request user approval before subsequent write/execute tools are unlocked. Only callable while in plan mode.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    validate(input) {
      if (!input || typeof input.plan !== "string" || input.plan.trim().length === 0) {
        return "`plan` is required and must be non-empty";
      }
      return null;
    },
    async execute(
      input: ExitPlanModeInput,
      ctx: ToolContext,
    ): Promise<ToolResult<ExitPlanModeOutput>> {
      const start = Date.now();
      try {
        const controller = getController(ctx.turnId);
        if (!controller) {
          return {
            status: "error",
            errorCode: "no_controller",
            errorMessage: "ExitPlanMode called but no plan-mode controller is registered",
            durationMs: Date.now() - start,
          };
        }
        if (!controller.isPlanMode()) {
          return {
            status: "error",
            errorCode: "not_in_plan_mode",
            errorMessage: "ExitPlanMode called while the turn was not in plan mode",
            durationMs: Date.now() - start,
          };
        }
        const output = await controller.submitPlan({
          turnId: ctx.turnId,
          plan: input.plan,
          emitAgentEvent: ctx.emitAgentEvent,
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
