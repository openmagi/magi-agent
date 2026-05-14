import type { HookContext, RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import {
  inspectToolCallResourceBoundary,
  resourceBindingsAreActive,
  type ResourceBoundaryViolation,
} from "../../execution/ResourceBoundary.js";

const MAX_RETRIES = 1;

export interface ResourceBoundaryAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface ResourceBoundaryGateOptions {
  agent?: ResourceBoundaryAgent;
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_RESOURCE_BOUNDARY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function makeResourceBoundaryHooks(
  opts: ResourceBoundaryGateOptions = {},
): {
  beforeToolUse: RegisteredHook<"beforeToolUse">;
  beforeCommit: RegisteredHook<"beforeCommit">;
} {
  return {
    beforeToolUse: {
      name: "builtin:resource-boundary",
      point: "beforeToolUse",
      priority: 18,
      blocking: true,
      timeoutMs: 500,
      handler: async ({ toolName, toolUseId, input }, ctx) => {
        if (!isEnabled()) return { action: "continue" };
        const contract = ctx.executionContract;
        if (!contract) return { action: "continue" };
        const snapshot = contract.snapshot();
        const bindings = snapshot.taskState.resourceBindings;
        if (!resourceBindingsAreActive(bindings)) return { action: "continue" };

        const inspection = inspectToolCallResourceBoundary({
          toolName,
          toolUseId,
          input,
          bindings,
        });
        for (const resource of inspection.usedResources) {
          contract.recordUsedResource(resource);
        }
        emitInspection(ctx, inspection.violations);

        if (bindings.mode === "audit" || inspection.violations.length === 0) {
          return { action: "continue" };
        }
        return {
          action: "block",
          reason: renderViolationReason(inspection.violations),
        };
      },
    },
    beforeCommit: {
      name: "builtin:resource-boundary-before-commit",
      point: "beforeCommit",
      priority: 88,
      blocking: true,
      timeoutMs: 2_000,
      handler: async ({ retryCount }, ctx) => {
        if (!isEnabled()) return { action: "continue" };
        const contract = ctx.executionContract;
        if (!contract) return { action: "continue" };
        const snapshot = contract.snapshot();
        const bindings = snapshot.taskState.resourceBindings;
        if (!resourceBindingsAreActive(bindings)) return { action: "continue" };

        const transcript = await readTranscript(opts, ctx);
        const violations: ResourceBoundaryViolation[] = [];
        for (const entry of transcript) {
          if (entry.kind !== "tool_call" || entry.turnId !== ctx.turnId) continue;
          const inspection = inspectToolCallResourceBoundary({
            toolName: entry.name,
            toolUseId: entry.toolUseId,
            input: entry.input,
            bindings,
          });
          for (const resource of inspection.usedResources) {
            contract.recordUsedResource(resource);
          }
          violations.push(...inspection.violations);
        }
        emitInspection(ctx, violations);

        if (bindings.mode === "audit" || violations.length === 0) {
          return { action: "continue" };
        }
        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[resource-boundary] retry exhausted; failing open", {
            violations: violations.map((violation) => ({
              kind: violation.kind,
              value: violation.value,
              toolName: violation.toolName,
            })),
          });
          return { action: "continue" };
        }
        return {
          action: "block",
          reason: renderViolationReason(violations),
        };
      },
    },
  };
}

async function readTranscript(
  opts: ResourceBoundaryGateOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  try {
    const entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
    return entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
  } catch (err) {
    ctx.log("warn", "[resource-boundary] transcript read failed; falling back", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  }
}

function emitInspection(ctx: HookContext, violations: ResourceBoundaryViolation[]): void {
  ctx.emit({
    type: "rule_check",
    ruleId: "resource-boundary",
    verdict: violations.length === 0 ? "ok" : "violation",
    detail:
      violations.length === 0
        ? "tool resources match active execution contract bindings"
        : violations.map((violation) => `${violation.kind}:${violation.value}`).join("; "),
  });
}

function renderViolationReason(violations: ResourceBoundaryViolation[]): string {
  return [
    "[RETRY:RESOURCE_BOUNDARY] A tool attempted to use a resource outside the active execution contract.",
    "",
    "Violations:",
    ...violations.map(
      (violation) =>
        `- ${violation.toolName}: ${violation.kind} ${violation.value} — ${violation.reason}`,
    ),
    "",
    "Use only the bound resources, or explicitly explain why the contract cannot be satisfied.",
  ].join("\n");
}
