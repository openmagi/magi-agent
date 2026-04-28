import { buildRuntimePolicyBlock, type PolicyKernel } from "../../policy/PolicyKernel.js";
import type { RuntimePolicySnapshot } from "../../policy/policyTypes.js";
import type { HookContext, RegisteredHook } from "../types.js";

export interface PolicyPromptBlockOptions {
  policy: Pick<PolicyKernel, "current">;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_POLICY_PROMPT;
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
          system: `${args.system}\n\n${buildRuntimePolicyBlock(snapshot)}`,
        },
      };
    },
  };
}
