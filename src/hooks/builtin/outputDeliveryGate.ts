import type { RegisteredHook, HookContext } from "../types.js";

export interface OutputDeliveryGateAgent {
  listUndelivered(
    sessionKey: string,
    turnId: string,
  ): Promise<ReadonlyArray<{ artifactId: string; filename: string }>>;
}

export interface OutputDeliveryGateOptions {
  agent?: OutputDeliveryGateAgent;
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_OUTPUT_DELIVERY_GATE;
  if (raw === undefined || raw === null) return true;
  const normalized = raw.trim().toLowerCase();
  return normalized === "" || normalized === "on" || normalized === "true" || normalized === "1";
}

export function matchesDeliveryFailureExplanation(text: string): boolean {
  if (!text) return false;
  return (
    /delivery failed|attachment upload failed|kb upload failed/i.test(text) ||
    /전송이 실패|첨부 .*실패|KB .*실패/u.test(text)
  );
}

export function makeOutputDeliveryGateHook(
  opts: OutputDeliveryGateOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:output-delivery-gate",
    point: "beforeCommit",
    priority: 87,
    blocking: true,
    handler: async ({ assistantText }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!opts.agent) return { action: "continue" };
        if (matchesDeliveryFailureExplanation(assistantText)) {
          return { action: "continue" };
        }

        const pending = await opts.agent.listUndelivered(ctx.sessionKey, ctx.turnId);
        const first = pending[0];
        if (!first) {
          return { action: "continue" };
        }

        ctx.log("warn", "[output-delivery-gate] blocking undelivered artifact", {
          artifactId: first.artifactId,
          filename: first.filename,
          pendingCount: pending.length,
        });
        ctx.emit({
          type: "rule_check",
          ruleId: "output-delivery-gate",
          verdict: "violation",
          detail: `undelivered artifacts: ${pending.map((item) => item.filename).join(", ")}`,
        });
        return {
          action: "block",
          reason: `Output artifact "${first.filename}" was created but not delivered yet. Call FileDeliver or explain the delivery failure.`,
        };
      } catch (error) {
        ctx.log("warn", "[output-delivery-gate] failed open", {
          error: error instanceof Error ? error.message : String(error),
        });
        return { action: "continue" };
      }
    },
  };
}

export const outputDeliveryGateHook = makeOutputDeliveryGateHook();
