import {
  completionClaimMissingCriteria,
  completionClaimNeedsContractVerification,
  renderExecutionContractBlock,
  shouldInjectExecutionContract,
} from "../../execution/ExecutionContract.js";
import type { HookContext, RegisteredHook } from "../types.js";

const MAX_RETRIES = 1;

function isPromptEnabled(): boolean {
  const raw = process.env.MAGI_EXECUTION_CONTRACT_PROMPT;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function isVerifierEnabled(): boolean {
  const raw = process.env.MAGI_EXECUTION_CONTRACT_VERIFIER;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function makeExecutionContractPromptHook(): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:execution-contract-prompt",
    point: "beforeLLMCall",
    priority: 7,
    blocking: true,
    timeoutMs: 200,
    handler: async (args, ctx: HookContext) => {
      if (!isPromptEnabled()) return { action: "continue" };
      if (args.iteration > 0) return { action: "continue" };
      const contract = ctx.executionContract;
      if (!contract) return { action: "continue" };
      const snapshot = contract.snapshot();
      if (!shouldInjectExecutionContract(snapshot)) return { action: "continue" };
      const block = renderExecutionContractBlock(snapshot);
      return {
        action: "replace",
        value: {
          ...args,
          system: `${args.system}\n\n${block}`,
        },
      };
    },
  };
}

export function makeExecutionContractVerifierHook(): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:execution-contract-verifier",
    point: "beforeCommit",
    priority: 89,
    blocking: true,
    timeoutMs: 1_000,
    handler: async ({ assistantText, retryCount }, ctx: HookContext) => {
      if (!isVerifierEnabled()) return { action: "continue" };
      const contract = ctx.executionContract;
      if (!contract) return { action: "continue" };
      const snapshot = contract.snapshot();
      if (!completionClaimNeedsContractVerification(snapshot, assistantText)) {
        return { action: "continue" };
      }
      const missingCriteria = completionClaimMissingCriteria(snapshot, assistantText);
      if (retryCount >= MAX_RETRIES) {
        ctx.log("warn", "[execution-contract-verifier] retry exhausted; failing open", {
          acceptanceCriteria: snapshot.taskState.acceptanceCriteria,
          missingCriteria: missingCriteria.map((criterion) => ({
            id: criterion.id,
            text: criterion.text,
            status: criterion.status,
          })),
        });
        return { action: "continue" };
      }
      ctx.emit({
        type: "rule_check",
        ruleId: "execution-contract-verifier",
        verdict: "violation",
        detail: "completion claim requires execution-contract verification evidence",
      });
      return {
        action: "block",
        reason: [
          "[RETRY:EXECUTION_CONTRACT_VERIFY] The active execution contract has acceptance criteria or full verification mode,",
          "but this completion claim has no recorded verification evidence.",
          "",
          missingCriteria.length > 0
            ? [
                "Unmet acceptance criteria:",
                ...missingCriteria.map(
                  (criterion) =>
                    `- ${criterion.id}: ${criterion.text} (${criterion.status})`,
                ),
                "",
              ].join("\n")
            : "",
          "Before finalising:",
          "1) Run or inspect the deterministic check that proves the acceptance criteria.",
          "2) Record the evidence by reporting the actual command/result or artifact inspection.",
          "3) If verification is impossible, state the remaining risk instead of claiming completion.",
          "",
          renderExecutionContractBlock(snapshot),
        ].join("\n"),
      };
    },
  };
}

export const executionContractPromptHook = makeExecutionContractPromptHook();
export const executionContractVerifierHook = makeExecutionContractVerifierHook();
