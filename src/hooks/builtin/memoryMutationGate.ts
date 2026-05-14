import type { HookArgs, HookContext, HookResult, RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { RequestMetaClassificationResult } from "../../execution/ExecutionContract.js";
import {
  getOrClassifyFinalAnswerMeta,
  getOrClassifyRequestMeta,
} from "./turnMetaClassifier.js";

const MAX_RETRIES = 1;

export interface MemoryMutationGateAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface MemoryMutationGateOptions {
  agent?: MemoryMutationGateAgent;
}

interface MemoryRedactEvidence {
  present: boolean;
  redacted: boolean;
  attemptedButNoMatch: boolean;
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_MEMORY_MUTATION_GATE;
  if (raw === undefined || raw === null) return true;
  const value = raw.trim().toLowerCase();
  return value === "" || value === "on" || value === "true" || value === "1";
}

export function makeMemoryMutationGateHooks(
  opts: MemoryMutationGateOptions = {},
): {
  beforeLLMCall: RegisteredHook<"beforeLLMCall">;
  beforeToolUse: RegisteredHook<"beforeToolUse">;
  beforeCommit: RegisteredHook<"beforeCommit">;
} {
  return {
    beforeLLMCall: {
      name: "builtin:memory-mutation-prompt",
      point: "beforeLLMCall",
      priority: 6,
      blocking: true,
      failOpen: true,
      timeoutMs: 4_000,
      handler: async (
        args,
        ctx,
      ): Promise<HookResult<HookArgs["beforeLLMCall"]> | void> => {
        if (!isEnabled() || args.iteration !== 0) return;
        const userMessage = latestUserText(args.messages);
        if (!userMessage) return;
        const meta = await getOrClassifyRequestMeta(ctx, { userMessage });
        if (meta.memoryMutation.intent === "none") return;
        const block = [
          "<memory-mutation-contract source=\"runtime\">",
          "The user asked to remove content from persistent Hipocampus memory.",
          "Use the MemoryRedact tool for raw memory file redaction.",
          "Do not use FileEdit, FileWrite, Bash, or scripts to edit memory files for this request.",
          "Before claiming memory was changed, make sure this turn has successful MemoryRedact evidence.",
          meta.memoryMutation.target ? `target: ${meta.memoryMutation.target}` : "target: (unspecified)",
          "</memory-mutation-contract>",
        ].join("\n");
        return {
          action: "replace",
          value: {
            ...args,
            system: args.system ? `${block}\n\n${args.system}` : block,
          },
        };
      },
    },
    beforeToolUse: {
      name: "builtin:memory-mutation-tool-boundary",
      point: "beforeToolUse",
      priority: 17,
      blocking: true,
      timeoutMs: 500,
      handler: async ({ toolName, input }, ctx) => {
        if (!isEnabled()) return { action: "continue" };
        if (!hasActiveMemoryMutation(ctx)) return { action: "continue" };
        if (toolName === "MemoryRedact") return { action: "continue" };
        if (!isGenericMemoryMutationTool(toolName, input)) return { action: "continue" };
        return {
          action: "block",
          reason: [
            "[RETRY:MEMORY_MUTATION_TOOL_BOUNDARY] Memory file deletion/redaction must use MemoryRedact.",
            "Do not mutate memory files with FileEdit, FileWrite, Bash, SafeCommand, or scripts for this request.",
            "Call MemoryRedact with the exact target_text and memory path scope instead.",
          ].join("\n"),
        };
      },
    },
    beforeCommit: {
      name: "builtin:memory-mutation-gate",
      point: "beforeCommit",
      priority: 88,
      blocking: true,
      failOpen: true,
      timeoutMs: 6_000,
      handler: async ({ assistantText, retryCount, userMessage }, ctx) => {
        if (!isEnabled()) return { action: "continue" };
        const requestMeta = await getOrClassifyRequestMeta(ctx, { userMessage });
        const requestNeedsMutation = requestMeta.memoryMutation.intent !== "none";
        if (!requestNeedsMutation && !mayClaimMemoryMutation(assistantText)) {
          return { action: "continue" };
        }

        const finalMeta = await getOrClassifyFinalAnswerMeta(ctx, {
          userMessage,
          assistantText,
        });
        const answerClaimsMutation = finalMeta.assistantClaimsMemoryMutation;
        if (!requestNeedsMutation && !answerClaimsMutation) {
          return { action: "continue" };
        }

        const evidence = memoryRedactEvidenceForTurn(await readTranscript(opts, ctx), ctx.turnId);
        if (!evidence.present) {
          return {
            action: "block",
            reason: retryCount >= MAX_RETRIES
              ? [
                  "[RULE:MEMORY_MUTATION_TOOL_REQUIRED] Memory deletion/redaction was requested or claimed without MemoryRedact evidence.",
                  "The turn cannot finish with a memory mutation claim unless MemoryRedact ran in this turn.",
                ].join("\n")
              : [
                  "[RETRY:MEMORY_MUTATION_TOOL_REQUIRED] Memory deletion/redaction requires MemoryRedact evidence in the current turn.",
                  "Call MemoryRedact with the exact target_text and then re-draft the answer from its output.",
                ].join("\n"),
          };
        }

        if (answerClaimsMutation && !evidence.redacted) {
          return {
            action: "block",
            reason: [
              "[RETRY:MEMORY_MUTATION_NOT_REDACTED] The answer claims memory was removed, but MemoryRedact did not redact any target text.",
              "Say the target was not found or that deletion was not completed, unless MemoryRedact reports matchedCount > 0.",
            ].join("\n"),
          };
        }

        if (requestNeedsMutation && evidence.attemptedButNoMatch && !finalMeta.assistantReportsMemoryMutationFailure) {
          return {
            action: "block",
            reason: [
              "[RETRY:MEMORY_MUTATION_RESULT_MISMATCH] MemoryRedact found no matching memory content.",
              "Report that the target was not found instead of claiming deletion.",
            ].join("\n"),
          };
        }

        ctx.emit({
          type: "rule_check",
          ruleId: "memory-mutation-gate",
          verdict: "ok",
          detail: evidence.redacted
            ? "memory mutation claim has MemoryRedact evidence"
            : "memory mutation request has MemoryRedact no-match evidence",
        });
        return { action: "continue" };
      },
    },
  };
}

function mayClaimMemoryMutation(text: string): boolean {
  const normalized = text.trim().toLowerCase();
  if (!normalized) return false;
  return (
    /\b(memory|memories|stored context|hipocampus)\b.*\b(deleted|removed|erased|redacted|forgotten|cleared)\b/i.test(normalized) ||
    /\b(deleted|removed|erased|redacted|forgot|cleared)\b.*\b(memory|memories|stored context|hipocampus)\b/i.test(normalized) ||
    /(?:메모리|기억|저장된\s*내용|히포캠퍼스).*(?:삭제|제거|지웠|잊었|초기화|비웠|redact)/i.test(text) ||
    /(?:삭제|제거|지웠|잊었|초기화|비웠).*(?:메모리|기억|저장된\s*내용|히포캠퍼스)/i.test(text)
  );
}

async function readTranscript(
  opts: MemoryMutationGateOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript;
  try {
    return (await opts.agent.readSessionTranscript(ctx.sessionKey)) ?? ctx.transcript;
  } catch (err) {
    ctx.log("warn", "[memory-mutation-gate] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript;
  }
}

function hasActiveMemoryMutation(ctx: HookContext): boolean {
  const classifications =
    ctx.executionContract?.snapshot().taskState.requestMetaClassifications ?? [];
  return classifications.some(
    (record) =>
      record.turnId === ctx.turnId &&
      record.result.memoryMutation.intent !== "none",
  );
}

function isGenericMemoryMutationTool(toolName: string, input: unknown): boolean {
  if (toolName === "FileEdit" || toolName === "FileWrite") {
    const pathValue = pathFromInput(input);
    return pathValue !== null && isMemoryPath(pathValue);
  }
  if (toolName === "Bash" || toolName === "SafeCommand") {
    return true;
  }
  return false;
}

function pathFromInput(input: unknown): string | null {
  if (!input || typeof input !== "object") return null;
  const value = (input as { path?: unknown }).path;
  return typeof value === "string" ? value : null;
}

function isMemoryPath(value: string): boolean {
  const normalized = value.replace(/\\/g, "/").replace(/^\.\/+/, "");
  return normalized === "MEMORY.md" || normalized.startsWith("memory/");
}

function memoryRedactEvidenceForTurn(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): MemoryRedactEvidence {
  const resultById = new Map<string, Extract<TranscriptEntry, { kind: "tool_result" }>>();
  for (const entry of transcript) {
    if (entry.kind === "tool_result" && entry.turnId === turnId) {
      resultById.set(entry.toolUseId, entry);
    }
  }

  let present = false;
  let redacted = false;
  let attemptedButNoMatch = false;
  for (const entry of transcript) {
    if (entry.kind !== "tool_call" || entry.turnId !== turnId || entry.name !== "MemoryRedact") {
      continue;
    }
    const result = resultById.get(entry.toolUseId);
    if (!result || result.status !== "ok" || result.isError === true) continue;
    present = true;
    const output = parseToolOutput(result.output);
    const matchedCount = typeof output?.matchedCount === "number" ? output.matchedCount : 0;
    const verification = output?.verification;
    const targetStillPresent =
      verification && typeof verification === "object"
        ? (verification as { targetStillPresent?: unknown }).targetStillPresent === true
        : true;
    if (matchedCount > 0 && !targetStillPresent) redacted = true;
    if (matchedCount === 0) attemptedButNoMatch = true;
  }
  return { present, redacted, attemptedButNoMatch };
}

function parseToolOutput(raw: string | undefined): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function latestUserText(messages: HookArgs["beforeLLMCall"]["messages"]): string {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (!msg || msg.role !== "user") continue;
    if (typeof msg.content === "string") return msg.content;
    if (!Array.isArray(msg.content)) return "";
    return msg.content
      .map((block) =>
        block && typeof block === "object" && "type" in block && block.type === "text" && "text" in block
          ? String(block.text)
          : "",
      )
      .filter(Boolean)
      .join("\n");
  }
  return "";
}
