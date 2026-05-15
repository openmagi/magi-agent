import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ProvisioningInput } from "./controller";
import type { ContainerSpec } from "./k8s-client";

// Track call order for step verification
const callOrder: string[] = [];

const mockK8sClient = {
  createNamespace: vi.fn().mockImplementation(async () => {
    callOrder.push("createNamespace");
  }),
  deleteNamespace: vi.fn().mockResolvedValue(undefined),
  namespaceExists: vi.fn().mockResolvedValue(false),
  createPVC: vi.fn().mockImplementation(async () => {
    callOrder.push("createPVC");
  }),
  createSecret: vi.fn().mockImplementation(async () => {
    callOrder.push("createSecret");
  }),
  applyNetworkPolicy: vi.fn().mockImplementation(async () => {
    callOrder.push("applyNetworkPolicy");
  }),
  createPod: vi.fn().mockImplementation(async () => {
    callOrder.push("createPod");
  }),
  deletePod: vi.fn().mockResolvedValue(undefined),
  getPodStatus: vi.fn().mockImplementation(async () => {
    callOrder.push("getPodStatus");
    return "Running";
  }),
  getPodLogs: vi.fn().mockImplementation(async (_ns: string, _pod: string, container?: string) => {
    callOrder.push("getPodLogs");
    if (container === "node-host") {
      return "node host PATH: /some/path";
    }
    return "gateway started";
  }),
};

const mockUpdate = vi.fn().mockReturnValue({
  eq: vi.fn().mockResolvedValue({ error: null }),
});

// Create a chainable eq mock that supports .eq().eq().single() etc.
function createChainableEq(): ReturnType<typeof vi.fn> {
  const eqFn: ReturnType<typeof vi.fn> = vi.fn();
  const chainable = {
    eq: eqFn,
    single: vi.fn().mockResolvedValue({ data: { privy_wallet_id: null }, error: null }),
    limit: vi.fn().mockResolvedValue({ data: [], error: null }),
  };
  eqFn.mockReturnValue(chainable);
  return eqFn;
}

const mockSelect = vi.fn().mockImplementation(() => ({
  eq: createChainableEq(),
}));

const mockInsert = vi.fn().mockResolvedValue({ error: null });

const mockFrom = vi.fn().mockImplementation((table: string) => {
  if (table === "bot_wallet_policies") {
    return { select: mockSelect, insert: mockInsert };
  }
  return {
    update: mockUpdate,
    select: mockSelect,
  };
});

vi.mock("./k8s-client", () => ({
  createK8sClient: () => mockK8sClient,
}));

vi.mock("@/lib/supabase/admin", () => ({
  createAdminClient: () => ({
    from: mockFrom,
  }),
}));

// Mock crypto.randomUUID
vi.mock("crypto", () => ({
  randomUUID: () => "test-gateway-token-uuid",
}));

// Mock wallet service (steps skip when PRIVY_AUTHORIZATION_KEY_ID is unset)
vi.mock("@/lib/privy/wallet-service", () => ({
  createAgentWallet: vi.fn(),
  createWalletPolicy: vi.fn(),
  attachPolicyToWallet: vi.fn(),
  buildDefaultPolicy: vi.fn(),
}));

const validInput: ProvisioningInput = {
  botId: "00000000-0000-4000-8000-000000000123",
  userId: "user-456",
  botName: "TestBot",
  telegramBotToken: "123456:ABC-DEF",
  modelSelection: "sonnet",
  apiKeyMode: "byok",
  anthropicApiKey: "sk-ant-test-key",
  botPurpose: "Help with coding",
  purposePreset: "coding_assistant",
  displayName: "Kevin",
};

describe("controller", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    callOrder.length = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("completes provisioning successfully with valid input", async () => {
    const { provisionBot } = await import("./controller");

    const resultPromise = provisionBot(validInput);
    await vi.advanceTimersByTimeAsync(60_000);
    const result = await resultPromise;

    expect(result.success).toBe(true);
    expect(result.namespace).toBe(`clawy-${validInput.botId}`);
    expect(result.gatewayToken).toBe("test-gateway-token-uuid");
    expect(result.completedSteps).toBe(14);
    expect(result.error).toBeUndefined();
  });

  it("createNamespace is invoked with required clawy-bot=true label", async () => {
    // Regression guard: NetworkPolicy on api-proxy, chat-proxy, browser-worker,
    // and x402-gateway selects namespaceSelector matchLabels clawy-bot=true.
    // Dropping this label => silent ECONNREFUSED from bot pods.
    const { provisionBot } = await import("./controller");

    const resultPromise = provisionBot(validInput);
    await vi.advanceTimersByTimeAsync(60_000);
    await resultPromise;

    expect(mockK8sClient.createNamespace).toHaveBeenCalledTimes(1);
    const [nsName, labels] = mockK8sClient.createNamespace.mock.calls[0];
    // Derive expected namespace from fixture so this test survives fixture
    // churn (e.g. botId shape changes). The load-bearing assertion is the
    // label, not the namespace-naming convention.
    expect(nsName).toBe(`clawy-${validInput.botId}`);
    expect(labels).toEqual({ "clawy-bot": "true" });
  });

  it("sets status to error when a step fails", async () => {
    mockK8sClient.createNamespace.mockRejectedValueOnce(
      new Error("namespace already exists")
    );

    const { provisionBot } = await import("./controller");

    const result = await provisionBot(validInput);

    expect(result.success).toBe(false);
    expect(result.error).toContain("create_namespace");
    expect(result.error).toContain("namespace already exists");
    expect(result.completedSteps).toBe(0);

    // Verify error status was set in Supabase
    expect(mockFrom).toHaveBeenCalledWith("bots");
    const lastUpdateCall = mockUpdate.mock.calls[mockUpdate.mock.calls.length - 1];
    expect(lastUpdateCall[0].status).toBe("error");
  });

  it("executes all 14 steps in order", async () => {
    const { provisionBot } = await import("./controller");

    const resultPromise = provisionBot(validInput);
    await vi.advanceTimersByTimeAsync(60_000);
    await resultPromise;

    // Verify the k8s calls happened in the expected order:
    // Step 1: createNamespace
    // Step 2: createPVC
    // Step 3: createSecret (bot secrets)
    // Step 3.5: create_wallet (skipped — no PRIVY_AUTHORIZATION_KEY_ID)
    // Step 3.6: create_default_policy (skipped — no wallet)
    // Step 4: applyNetworkPolicy
    // Step 5: createSecret (static templates)
    // Step 6: createSecret (dynamic files)
    // Step 7: createSecret (config)
    // Step 8: createSecret (skills)
    // Step 9: createSecret (specialist templates)
    // Step 10: createSecret (lifecycle scripts)
    // Step 11: createPod
    expect(callOrder).toEqual([
      "createNamespace",
      "createPVC",
      "createSecret",         // step 3: bot secrets
      // wallet steps skipped (no env var / no wallet)
      "applyNetworkPolicy",   // step 4: network policy
      "createSecret",         // step 5: static templates
      "createSecret",         // step 6: dynamic files
      "createSecret",         // step 7: config
      "createSecret",         // step 8: skills
      "createSecret",         // step 9: specialist templates
      "createSecret",         // step 10: lifecycle scripts
      "createPod",            // step 11
    ]);
  });

  it("copies LEARNING.md into the static workspace template Secret", async () => {
    const { provisionBot } = await import("./controller");

    const resultPromise = provisionBot(validInput);
    await vi.advanceTimersByTimeAsync(60_000);
    await resultPromise;

    const staticTemplateCall = mockK8sClient.createSecret.mock.calls.find(
      ([, name]) => name === `static-templates-${validInput.botId}`,
    );

    expect(staticTemplateCall).toBeDefined();
    expect(staticTemplateCall?.[2]).toEqual(
      expect.objectContaining({
        "LEARNING.md": expect.stringContaining("Hipocampus"),
      }),
    );
  });

  it("gates OAuth-backed skills consistently when collecting bundled skill data", async () => {
    const { collectSkillData } = await import("./controller");

    const withoutIntegrations = collectSkillData([]);
    expect(withoutIntegrations["google-gmail__SKILL.md"]).toBeUndefined();
    expect(withoutIntegrations["google-drive__SKILL.md"]).toBeUndefined();
    expect(withoutIntegrations["google-ads__SKILL.md"]).toBeUndefined();
    expect(withoutIntegrations["twitter__SKILL.md"]).toBeUndefined();

    const withIntegrations = collectSkillData(["google", "twitter"]);
    expect(withIntegrations["google-gmail__SKILL.md"]).toContain("google-gmail");
    expect(withIntegrations["google-drive__SKILL.md"]).toContain("google-drive");
    expect(withIntegrations["google-ads__SKILL.md"]).toContain("google-ads");
    expect(withIntegrations["twitter__SKILL.md"]).toContain("twitter");
  });

  it("updates bot status for each step", async () => {
    const { provisionBot } = await import("./controller");

    const resultPromise = provisionBot(validInput);
    await vi.advanceTimersByTimeAsync(60_000);
    await resultPromise;

    // Should have called from("bots") for each step (14 provisioning updates)
    // plus 1 final "provisioning" status update = 15 total
    // Note: wallet steps also call from("bots") for select, so we check update calls
    expect(mockFrom).toHaveBeenCalledWith("bots");
    expect(mockUpdate.mock.calls.length).toBe(15);

    // Final call should set status to "provisioning" (status API transitions to active)
    const finalCall = mockUpdate.mock.calls[mockUpdate.mock.calls.length - 1];
    expect(finalCall[0].status).toBe("provisioning");
  });

  it("includes iblai-router sidecar for smart_routing model", async () => {
    const smartRoutingInput: ProvisioningInput = {
      ...validInput,
      modelSelection: "smart_routing",
    };

    const { provisionBot } = await import("./controller");

    const resultPromise = provisionBot(smartRoutingInput);
    await vi.advanceTimersByTimeAsync(60_000);
    await resultPromise;

    // Verify createPod was called with 3 containers (gateway, node-host, iblai-router)
    const podCall = mockK8sClient.createPod.mock.calls[0];
    const podSpec = podCall[2]; // third argument is PodSpec
    expect(podSpec.containers).toHaveLength(3);
    expect(podSpec.containers[2].name).toBe("iblai-router");
  });

  it("does not include iblai-router sidecar for non-smart_routing model", async () => {
    const { provisionBot } = await import("./controller");

    const resultPromise = provisionBot(validInput); // uses "sonnet"
    await vi.advanceTimersByTimeAsync(60_000);
    await resultPromise;

    const podCall = mockK8sClient.createPod.mock.calls[0];
    const podSpec = podCall[2];
    expect(podSpec.containers).toHaveLength(2);
    expect(podSpec.containers.map((c: ContainerSpec) => c.name)).toEqual([
      "gateway",
      "node-host",
    ]);
  });

  it("collects promoted custom skills as first-class SKILL.md files", async () => {
    const { collectSkillData } = await import("./controller");

    const skills = collectSkillData([], [
      {
        skill_name: "custom-invoice-review",
        content: "---\nname: custom-invoice-review\n---\n\n# Invoice Review\n",
      },
    ]);

    expect(skills["custom-invoice-review__SKILL.md"]).toContain(
      "custom-invoice-review"
    );
  });

  it("stops execution on first failing step", async () => {
    // Make step 2 (createPVC) fail
    mockK8sClient.createPVC.mockRejectedValueOnce(
      new Error("storage class not found")
    );

    const { provisionBot } = await import("./controller");

    const result = await provisionBot(validInput);

    expect(result.success).toBe(false);
    expect(result.completedSteps).toBe(1); // only namespace was created
    expect(result.error).toContain("create_pvc");

    // Pod should never have been created
    expect(mockK8sClient.createPod).not.toHaveBeenCalled();
  });
});
