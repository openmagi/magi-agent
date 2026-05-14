import type { LLMMessage } from "../../transport/LLMClient.js";
import {
  buildSourceAuthorityPromptBlock,
  detectCurrentTurnSourceKinds,
  resolveEffectiveLongTermMemoryPolicy,
} from "../../reliability/SourceAuthority.js";
import { channelMemoryPolicyFromSessionKey } from "../../reliability/ChannelMemoryPolicy.js";
import type { HookContext, RegisteredHook } from "../types.js";
import { latestUserText } from "./classifyTurnMode.js";
import { getOrClassifyRequestMeta } from "./turnMetaClassifier.js";

function isEnabled(): boolean {
  const raw = process.env.MAGI_SOURCE_AUTHORITY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function messagesHaveImages(messages: readonly LLMMessage[]): boolean {
  for (const message of messages) {
    if (!Array.isArray(message.content)) continue;
    if (message.content.some((block) => block.type === "image")) return true;
  }
  return false;
}

export function makeSourceAuthorityPromptHook(): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:source-authority-prompt",
    point: "beforeLLMCall",
    priority: 4,
    blocking: true,
    failOpen: true,
    timeoutMs: 8_500,
    handler: async (args, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (args.iteration > 0) return { action: "continue" };
        if (!ctx.executionContract) return { action: "continue" };

        const userMessage = latestUserText(args.messages);
        if (!userMessage) return { action: "continue" };

        const classified = await getOrClassifyRequestMeta(ctx, { userMessage });
        const currentSourceKinds = detectCurrentTurnSourceKinds({
          system: args.system,
          userText: userMessage,
          hasImages: messagesHaveImages(args.messages),
        });
        const longTermMemoryPolicy = resolveEffectiveLongTermMemoryPolicy({
          classifierPolicy: classified.sourceAuthority.longTermMemoryPolicy,
          classifierCurrentSourcesAuthoritative:
            classified.sourceAuthority.currentSourcesAuthoritative,
          currentSourceKinds,
        });
        const channelMemoryPolicy = channelMemoryPolicyFromSessionKey(ctx.sessionKey);
        const effectiveLongTermMemoryPolicy =
          channelMemoryPolicy === "disabled" ? "disabled" : longTermMemoryPolicy;
        const classifierReason =
          channelMemoryPolicy === "disabled"
            ? `Channel memory mode is no-memory. ${classified.sourceAuthority.reason}`
            : classified.sourceAuthority.reason;

        ctx.executionContract.replaceSourceAuthorityForTurn(ctx.turnId, [
          {
            turnId: ctx.turnId,
            currentSourceKinds,
            longTermMemoryPolicy: effectiveLongTermMemoryPolicy,
            classifierReason,
          },
        ]);

        if (effectiveLongTermMemoryPolicy === "normal" && currentSourceKinds.length === 0) {
          return { action: "continue" };
        }

        const block = buildSourceAuthorityPromptBlock({
          turnId: ctx.turnId,
          currentSourceKinds,
          longTermMemoryPolicy: effectiveLongTermMemoryPolicy,
          classifierReason,
        });
        return {
          action: "replace",
          value: {
            ...args,
            system: args.system ? `${args.system}\n\n${block}` : block,
          },
        };
      } catch (err) {
        ctx.log("warn", "[source-authority-prompt] failed; continuing", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
