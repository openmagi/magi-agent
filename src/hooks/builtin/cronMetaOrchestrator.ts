/**
 * cronMetaOrchestrator — makes scheduled cron fires follow the same
 * meta/subagent split as interactive complex work.
 *
 * CronScheduler fires a synthetic top-level session key:
 *   agent:cron:<channel>:<channelId>:<cronId>
 * That parent turn must only orchestrate, inspect child results, and
 * manage scheduling. The actual work runs in SpawnAgent children, which
 * keep their normal tool access, retry loop, and verifier surface.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { LLMToolDef } from "../../transport/LLMClient.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import { matchesCompletionClaim } from "./completionEvidenceGate.js";

export const CRON_META_ALLOWED_PARENT_TOOLS: readonly string[] = [
  "SpawnAgent",
  "TaskBoard",
  "TaskList",
  "TaskGet",
  "TaskOutput",
  "TaskStop",
  "CronList",
  "CronCreate",
  "CronUpdate",
  "CronDelete",
  "ArtifactRead",
  "ArtifactList",
  "AskUserQuestion",
];

const ALLOWED_PARENT_TOOL_SET = new Set(CRON_META_ALLOWED_PARENT_TOOLS);
const CRON_SESSION_PREFIX = "agent:cron:";
const SPAWN_TURN_MARKER = "::spawn::";
const CONTRACT_OPEN = "<cron-meta-orchestrator>";
const CONTRACT_CLOSE = "</cron-meta-orchestrator>";
const MAX_RETRIES = 1;

export interface CronMetaOrchestratorAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface CronMetaOrchestratorOptions {
  agent?: CronMetaOrchestratorAgent;
}

export function isCronParentTurn(ctx: Pick<HookContext, "sessionKey" | "turnId">): boolean {
  return (
    ctx.sessionKey.startsWith(CRON_SESSION_PREFIX) &&
    !ctx.turnId.includes(SPAWN_TURN_MARKER)
  );
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_CRON_META_ORCHESTRATOR;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function buildCronMetaContract(): string {
  return [
    CONTRACT_OPEN,
    "This turn was fired by a scheduled cron. You are the main/meta agent, not the worker.",
    "",
    "Required execution contract:",
    "1. Do not perform the scheduled work directly in the parent cron turn.",
    "2. Delegate the real work to `SpawnAgent` (`deliver:\"return\"` for foreground cron results, `deliver:\"background\"` only when the scheduled workflow intentionally continues later).",
    "3. Put the full original cron task, current schedule context, success criteria, and delivery expectations in the child prompt. The child has no conversation context unless you include it.",
    "4. If the child fails, retry `SpawnAgent` with a changed prompt/allowed tools when duplicate side effects are safe. Do not switch to direct execution in the parent.",
    "5. The parent may only synthesize, inspect returned child artifacts, update/delete/create crons, ask for required approval, or report a truthful failure.",
    "6. Final text should summarize the child result and any verification evidence. Never claim the parent did tool/file/browser/KB/integration work directly.",
    "",
    "Parent tool surface is intentionally limited to meta/orchestration tools. Actual reads, writes, browser work, KB/search, integration calls, document generation, and delivery tools belong in the child.",
    CONTRACT_CLOSE,
  ].join("\n");
}

function appendCronMetaContract(system: string): string {
  if (system.includes(CONTRACT_OPEN)) return system;
  const block = buildCronMetaContract();
  return system ? `${block}\n\n${system}` : block;
}

function filterParentTools(tools: LLMToolDef[]): LLMToolDef[] {
  return tools.filter((tool) => ALLOWED_PARENT_TOOL_SET.has(tool.name));
}

async function readTranscript(
  opts: CronMetaOrchestratorOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  try {
    const entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
    return entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
  } catch (err) {
    ctx.log("warn", "[cron-meta-orchestrator] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  }
}

function parseOutputJson(output: string | undefined): unknown {
  if (!output) return null;
  try {
    return JSON.parse(output);
  } catch {
    return null;
  }
}

function spawnOutputSucceeded(output: string | undefined): boolean {
  const parsed = parseOutputJson(output);
  if (!parsed || typeof parsed !== "object") return true;
  const status = (parsed as Record<string, unknown>).status;
  return status === undefined || status === "ok" || status === "completed";
}

function hasSuccessfulSpawnAgentResult(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  const spawnToolUseIds = new Set<string>();
  for (const entry of transcript) {
    if (
      entry.kind === "tool_call" &&
      entry.turnId === turnId &&
      entry.name === "SpawnAgent"
    ) {
      spawnToolUseIds.add(entry.toolUseId);
    }
  }
  if (spawnToolUseIds.size === 0) return false;

  for (const entry of transcript) {
    if (
      entry.kind === "tool_result" &&
      entry.turnId === turnId &&
      spawnToolUseIds.has(entry.toolUseId) &&
      entry.status === "ok" &&
      entry.isError !== true &&
      spawnOutputSucceeded(entry.output)
    ) {
      return true;
    }
  }
  return false;
}

export function makeCronMetaOrchestratorHooks(
  opts: CronMetaOrchestratorOptions = {},
): {
  beforeLLMCall: RegisteredHook<"beforeLLMCall">;
  beforeToolUse: RegisteredHook<"beforeToolUse">;
  beforeCommit: RegisteredHook<"beforeCommit">;
} {
  return {
    beforeLLMCall: {
      name: "builtin:cron-meta-orchestrator",
      point: "beforeLLMCall",
      priority: 4,
      blocking: true,
      timeoutMs: 100,
      handler: async ({ messages, tools, system, iteration }, ctx) => {
        if (!isEnabled() || !isCronParentTurn(ctx)) {
          return { action: "continue" };
        }
        const filteredTools = filterParentTools(tools);
        ctx.emit({
          type: "rule_check",
          ruleId: "cron-meta-orchestrator",
          verdict: "ok",
          detail: `cron parent tool surface restricted to ${filteredTools
            .map((tool) => tool.name)
            .join(",")}`,
        });
        return {
          action: "replace",
          value: {
            messages,
            tools: filteredTools,
            system: appendCronMetaContract(system),
            iteration,
          },
        };
      },
    },
    beforeCommit: {
      name: "builtin:cron-meta-orchestrator-commit-gate",
      point: "beforeCommit",
      priority: 86,
      blocking: true,
      timeoutMs: 1_000,
      handler: async ({ assistantText, retryCount }, ctx) => {
        if (!isEnabled() || !isCronParentTurn(ctx)) {
          return { action: "continue" };
        }
        if (!matchesCompletionClaim(assistantText)) {
          return { action: "continue" };
        }
        const transcript = await readTranscript(opts, ctx);
        if (hasSuccessfulSpawnAgentResult(transcript, ctx.turnId)) {
          return { action: "continue" };
        }
        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[cron-meta-orchestrator] retry exhausted; failing open", {
            retryCount,
          });
          return { action: "continue" };
        }
        ctx.emit({
          type: "rule_check",
          ruleId: "cron-meta-orchestrator",
          verdict: "violation",
          detail: "cron parent completion claim without a successful SpawnAgent result",
        });
        return {
          action: "block",
          reason: [
            "[RETRY:CRON_META_ORCHESTRATOR] Cron parent turns are meta-only.",
            "You are claiming the scheduled work is complete, but this cron turn has no successful SpawnAgent result.",
            "Delegate the real work to SpawnAgent, inspect the child result/artifacts, then summarize that result.",
            "If the child cannot run or fails after changed-strategy retries, report the failure without claiming completion.",
          ].join("\n"),
        };
      },
    },
    beforeToolUse: {
      name: "builtin:cron-meta-orchestrator-tool-guard",
      point: "beforeToolUse",
      priority: 41,
      blocking: true,
      timeoutMs: 100,
      handler: async ({ toolName }, ctx) => {
        if (!isEnabled() || !isCronParentTurn(ctx)) {
          return { action: "continue" };
        }
        if (ALLOWED_PARENT_TOOL_SET.has(toolName)) {
          return { action: "continue" };
        }
        ctx.emit({
          type: "rule_check",
          ruleId: "cron-meta-orchestrator",
          verdict: "violation",
          detail: `blocked parent cron direct work tool: ${toolName}`,
        });
        return {
          action: "block",
          reason:
            `[CRON_META_ORCHESTRATOR] Cron parent turns are meta-only. ` +
            `Delegate real work to SpawnAgent; do not call ${toolName} directly from the cron parent.`,
        };
      },
    },
  };
}
