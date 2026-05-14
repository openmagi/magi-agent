import { buildRuntimePolicyBlock, type PolicyKernel } from "../../policy/PolicyKernel.js";
import type { ResponseLanguagePolicy } from "../../policy/policyTypes.js";
import type { LLMContentBlock, LLMMessage } from "../../transport/LLMClient.js";
import {
  languageDescription,
  resolveLanguagePolicy,
} from "./responseLanguageGate.js";
import type { RuntimePolicySnapshot } from "../../policy/policyTypes.js";
import type { HookContext, RegisteredHook } from "../types.js";

export interface PolicyPromptBlockOptions {
  policy: Pick<PolicyKernel, "current">;
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_POLICY_PROMPT;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

async function loadSnapshot(
  policy: PolicyPromptBlockOptions["policy"],
  ctx: HookContext,
): Promise<RuntimePolicySnapshot | null> {
  try {
    return await policy.current();
  } catch (err) {
    ctx.log("warn", "[policy-prompt-block] policy load failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return null;
  }
}

function contentBlockText(block: LLMContentBlock): string {
  if (block.type === "text") return block.text;
  if (block.type === "tool_result") {
    if (typeof block.content === "string") return block.content;
    return block.content.map((item) => item.text).join("\n");
  }
  return "";
}

function messageText(message: LLMMessage): string {
  if (typeof message.content === "string") return message.content;
  return message.content.map(contentBlockText).filter(Boolean).join("\n");
}

function latestUserMessageText(messages: LLMMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.role !== "user") continue;
    const text = messageText(message).trim();
    if (text) return text;
  }
  return "";
}

function buildResponseLanguageTurnContract(
  language: ResponseLanguagePolicy | undefined,
  userMessage: string,
): string {
  if (!language || !userMessage.trim()) return "";
  const resolved = resolveLanguagePolicy(language, userMessage);
  return [
    '<response_language_contract source="policy-kernel">',
    `response.configured_language=${resolved.original}`,
    `response.target_language=${resolved.target}`,
    `response.language_resolution=${resolved.reason}`,
    languageDescription(resolved.target),
    "Apply response.target_language to every user-visible surface in this turn: streamed progress, public thinking summaries, tool/result narration, retry text, and the final answer.",
    "Do not inherit a different response language from older messages, generated identity files, examples, or tool outputs unless the latest user message explicitly requests that output language.",
    "</response_language_contract>",
  ].join("\n");
}

export function makePolicyPromptBlockHook(
  opts: PolicyPromptBlockOptions,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:policy-prompt-block",
    point: "beforeLLMCall",
    priority: 5,
    blocking: true,
    timeoutMs: 300,
    handler: async (args, ctx) => {
      if (!isEnabled()) return { action: "continue" };
      if (args.iteration > 0) return { action: "continue" };

      const snapshot = await loadSnapshot(opts.policy, ctx);
      if (!snapshot) return { action: "continue" };

      return {
        action: "replace",
        value: {
          ...args,
          system: [
            args.system,
            buildRuntimePolicyBlock(snapshot),
            buildResponseLanguageTurnContract(
              snapshot.policy.responseMode.language,
              latestUserMessageText(args.messages),
            ),
          ]
            .filter((part) => part.trim().length > 0)
            .join("\n\n"),
        },
      };
    },
  };
}
