import { describe, expect, it } from "vitest";
import { HookRegistry } from "../HookRegistry.js";
import { registerBuiltinHooks } from "./index.js";
import type { RuntimePolicySnapshot } from "../../policy/policyTypes.js";

const emptyPolicy: RuntimePolicySnapshot = {
  policy: {
    approval: { explicitConsentForExternalActions: true },
    verification: {
      requireCompletionEvidence: true,
      honorTaskContractVerificationMode: true,
    },
    delivery: { requireDeliveredArtifactsBeforeCompletion: true },
    async: { requireRealNotificationMechanism: true },
    retry: { retryTransientToolFailures: true, defaultBackoffSeconds: [0, 10, 30] },
    responseMode: {},
    citations: {},
    harnessRules: [],
  },
  status: {
    executableDirectives: [],
    userDirectives: [],
    harnessDirectives: [],
    advisoryDirectives: [],
    warnings: [],
  },
};

describe("registerBuiltinHooks", () => {
  it("registers cron meta-orchestrator hooks by default", () => {
    const registry = new HookRegistry();

    registerBuiltinHooks(registry, {
      workspaceRoot: "/tmp/workspace",
    });

    expect(registry.list("beforeLLMCall").map((hook) => hook.name)).toContain(
      "builtin:cron-meta-orchestrator",
    );
    expect(registry.list("beforeToolUse").map((hook) => hook.name)).toContain(
      "builtin:cron-meta-orchestrator-tool-guard",
    );
    expect(registry.list("beforeCommit").map((hook) => hook.name)).toContain(
      "builtin:cron-meta-orchestrator-commit-gate",
    );
  });

  it("registers user harness rule hooks when a policy kernel is available", () => {
    const registry = new HookRegistry();

    registerBuiltinHooks(registry, {
      workspaceRoot: "/tmp/workspace",
      policyKernel: { current: async () => emptyPolicy },
    });

    expect(registry.list("beforeCommit").map((hook) => hook.name)).toContain(
      "builtin:user-harness-rules",
    );
    expect(registry.list("afterToolUse").map((hook) => hook.name)).toContain(
      "builtin:user-harness-rules-after-tool",
    );
  });
});
