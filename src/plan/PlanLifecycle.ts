import crypto from "node:crypto";
import type { ControlEventLedger } from "../control/ControlEventLedger.js";
import type {
  ControlRequestRecord,
  PlanProjection,
} from "../control/ControlEvents.js";
import type {
  ControlRequestStore,
  ResolveControlRequestInput,
} from "../control/ControlRequestStore.js";
import type { ControlProjection } from "../control/ControlProjection.js";
import type { PermissionMode } from "../Session.js";

export interface PlanLifecycleOptions {
  sessionKey: string;
  channelName?: string;
  controlEvents: ControlEventLedger;
  controlRequests: ControlRequestStore;
  getPermissionMode?: () => PermissionMode;
  setPermissionMode?: (mode: PermissionMode) => void;
  exitPlanMode?: () => void;
  enqueueHiddenContext?: (message: string) => void;
}

export interface SubmitPlanInput {
  turnId: string;
  plan: string;
  emitAgentEvent?: (event: unknown) => void;
}

export interface SubmitPlanResult {
  planApproved: false;
  planId: string;
  requestId: string;
  state: "awaiting_approval";
}

export interface EnterPlanModeResult {
  planMode: true;
  previousMode: PermissionMode;
  state: "entered";
}

export class PlanLifecycle {
  private cachedProjection: ControlProjection | null = null;

  constructor(private readonly opts: PlanLifecycleOptions) {}

  static async load(rootDir: string, sessionKey: string): Promise<PlanLifecycle> {
    const { ControlEventLedger } = await import("../control/ControlEventLedger.js");
    const { ControlRequestStore } = await import("../control/ControlRequestStore.js");
    const ledger = new ControlEventLedger({ rootDir, sessionKey });
    const requests = new ControlRequestStore({ ledger });
    const lifecycle = new PlanLifecycle({
      sessionKey,
      controlEvents: ledger,
      controlRequests: requests,
    });
    await lifecycle.project();
    return lifecycle;
  }

  async enterPlanMode(input: { turnId: string }): Promise<EnterPlanModeResult> {
    const previousMode = this.opts.getPermissionMode?.() ?? "default";
    this.opts.setPermissionMode?.("plan");
    await this.opts.controlEvents.append({
      type: "plan_lifecycle",
      turnId: input.turnId,
      planId: `plan_${crypto.randomUUID().replace(/-/g, "")}`,
      state: "entered",
    });
    await this.project();
    return { planMode: true, previousMode, state: "entered" };
  }

  async submitPlan(input: SubmitPlanInput): Promise<SubmitPlanResult> {
    const planId = `plan_${crypto.randomUUID().replace(/-/g, "")}`;
    const plan = input.plan.trim();
    await this.opts.controlEvents.append({
      type: "plan_lifecycle",
      turnId: input.turnId,
      planId,
      state: "ready",
      plan,
    });
    const request = await this.opts.controlRequests.create({
      kind: "plan_approval",
      turnId: input.turnId,
      sessionKey: this.opts.sessionKey,
      channelName: this.opts.channelName,
      source: "plan",
      prompt: "Approve this plan before execution tools are unlocked.",
      proposedInput: { planId, plan },
      expiresAt: Date.now() + 30 * 60_000,
    });
    await this.opts.controlEvents.append({
      type: "plan_lifecycle",
      turnId: input.turnId,
      planId,
      state: "awaiting_approval",
      requestId: request.requestId,
      plan,
    });
    input.emitAgentEvent?.({
      type: "plan_ready",
      planId,
      requestId: request.requestId,
      state: "awaiting_approval",
      plan,
    });
    await this.project();
    return {
      planApproved: false,
      planId,
      requestId: request.requestId,
      state: "awaiting_approval",
    };
  }

  async resolveControlRequest(
    requestId: string,
    input: ResolveControlRequestInput,
  ): Promise<ControlRequestRecord> {
    const before = (await this.opts.controlRequests.project()).requests[requestId];
    const resolved = await this.opts.controlRequests.resolve(requestId, input);
    if ((before ?? resolved).kind !== "plan_approval") {
      await this.project();
      return resolved;
    }
    if (resolved.state === "approved") {
      await this.markApproved(resolved);
    } else if (resolved.state === "denied") {
      await this.markRejected(resolved);
    }
    await this.project();
    return resolved;
  }

  async project(): Promise<ControlProjection> {
    this.cachedProjection = await this.opts.controlRequests.project();
    return this.cachedProjection;
  }

  activePlan(): PlanProjection | null {
    return this.cachedProjection?.activePlan ?? null;
  }

  async hasPendingVerification(): Promise<boolean> {
    const projection = await this.project();
    return projection.activePlan?.state === "verification_pending";
  }

  private async markApproved(request: ControlRequestRecord): Promise<void> {
    const meta = planMeta(request);
    if (!meta) return;
    this.opts.exitPlanMode?.();
    await this.opts.controlEvents.append({
      type: "plan_lifecycle",
      turnId: request.turnId,
      planId: meta.planId,
      state: "approved",
      requestId: request.requestId,
      plan: meta.plan,
    });
    await this.opts.controlEvents.append({
      type: "verification",
      turnId: request.turnId,
      status: "pending",
      reason: "approved plan requires verification evidence before completion",
    });
    await this.opts.controlEvents.append({
      type: "plan_lifecycle",
      turnId: request.turnId,
      planId: meta.planId,
      state: "verification_pending",
      requestId: request.requestId,
      plan: meta.plan,
    });
  }

  private async markRejected(request: ControlRequestRecord): Promise<void> {
    const meta = planMeta(request);
    if (!meta) return;
    this.opts.setPermissionMode?.("plan");
    await this.opts.controlEvents.append({
      type: "plan_lifecycle",
      turnId: request.turnId,
      planId: meta.planId,
      state: "rejected",
      requestId: request.requestId,
      plan: meta.plan,
      feedback: request.feedback,
    });
    if (request.feedback) {
      this.opts.enqueueHiddenContext?.(
        [
          "<plan_rejection_feedback>",
          request.feedback,
          "</plan_rejection_feedback>",
          "Revise the plan in plan mode. Do not execute write or shell tools until a revised plan is approved.",
        ].join("\n"),
      );
    }
  }
}

function planMeta(
  request: ControlRequestRecord,
): { planId: string; plan: string } | null {
  const value = request.proposedInput;
  if (!value || typeof value !== "object") return null;
  const rec = value as { planId?: unknown; plan?: unknown };
  if (typeof rec.planId !== "string" || typeof rec.plan !== "string") return null;
  return { planId: rec.planId, plan: rec.plan };
}
