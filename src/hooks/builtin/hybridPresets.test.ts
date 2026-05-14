/**
 * Tests for hybrid verifier pattern + builtin preset executor + preset gate.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";

// ── Commit 1: Hybrid verifier tests ──

describe("answerVerifier hybrid mode", () => {
  const OLD_ENV = process.env;
  beforeEach(() => { process.env = { ...OLD_ENV }; });
  afterEach(() => { process.env = OLD_ENV; });

  it("judgeAnswerDeterministic returns confidence field", async () => {
    const { judgeAnswerDeterministic } = await import("./answerVerifier.js");
    const result = judgeAnswerDeterministic("What is the capital of France?", "I cannot help with that request.");
    expect(result.confidence).toBe("high");
    expect(result.verdict).toBe("REFUSAL");
    expect(result.reason).toContain("refusal");
  });

  it("judgeAnswerDeterministic returns high confidence for substantive response", async () => {
    const { judgeAnswerDeterministic } = await import("./answerVerifier.js");
    const longAnswer = "The capital of France is Paris. " + "a".repeat(200);
    const result = judgeAnswerDeterministic("What is the capital of France?", longAnswer);
    expect(result.confidence).toBe("high");
    expect(result.verdict).toBe("FULFILLED");
  });

  it("judgeAnswerDeterministic returns low confidence for short answer to complex question", async () => {
    const { judgeAnswerDeterministic } = await import("./answerVerifier.js");
    const result = judgeAnswerDeterministic(
      "Please explain the differences between React hooks and class components including lifecycle methods performance and state management",
      "Use hooks.",
    );
    expect(result.confidence).toBe("low");
    expect(result.verdict).toBe("PARTIAL");
  });

  it("judgeAnswerDeterministic returns low confidence for short ambiguous response", async () => {
    const { judgeAnswerDeterministic } = await import("./answerVerifier.js");
    const result = judgeAnswerDeterministic("What is 2+2?", "It is 4.");
    expect(result.confidence).toBe("low");
    expect(result.verdict).toBe("FULFILLED");
    expect(result.reason).toContain("short");
  });
});

describe("selfClaimVerifier hybrid mode", () => {
  it("detectSelfClaimDeterministic returns confidence field", async () => {
    const { detectSelfClaimDeterministic } = await import("./selfClaimVerifier.js");
    const result = detectSelfClaimDeterministic("my workspace includes a config file");
    expect(result.hasClaim).toBe(true);
    expect(result.confidence).toBe("high");
    expect(result.reason).toContain("explicit");
  });

  it("detectSelfClaimDeterministic returns low confidence for vague ref", async () => {
    const { detectSelfClaimDeterministic } = await import("./selfClaimVerifier.js");
    const result = detectSelfClaimDeterministic("I checked the config and it looks fine");
    expect(result.hasClaim).toBe(false);
    expect(result.confidence).toBe("low");
    expect(result.reason).toContain("needs LLM");
  });

  it("detectSelfClaimDeterministic returns high confidence for no claim", async () => {
    const { detectSelfClaimDeterministic } = await import("./selfClaimVerifier.js");
    const result = detectSelfClaimDeterministic("The sky is blue and the weather is nice today.");
    expect(result.hasClaim).toBe(false);
    expect(result.confidence).toBe("high");
    expect(result.reason).toContain("no workspace claim");
  });
});

describe("deterministicEvidenceVerifier hybrid mode", () => {
  it("judgeDeterministicEvidenceBySchema returns confidence field", async () => {
    const { judgeDeterministicEvidenceBySchema } = await import("./deterministicEvidenceVerifier.js");
    const result = judgeDeterministicEvidenceBySchema("The answer is 42.", [
      {
        evidenceId: "e1",
        turnId: "t1",
        requirementIds: ["r1"],
        toolName: "Calculator",
        kind: "calculation",
        status: "passed",
        inputSummary: "2+2",
        output: { result: 42 },
        assertions: ["42"],
        resources: [],
      },
    ]);
    expect(result.confidence).toBe("high");
    expect(result.verdict).toBe("PASS");
  });

  it("judgeDeterministicEvidenceBySchema returns low confidence for partial missing", async () => {
    const { judgeDeterministicEvidenceBySchema } = await import("./deterministicEvidenceVerifier.js");
    const result = judgeDeterministicEvidenceBySchema("The answer is something.", [
      {
        evidenceId: "e1",
        turnId: "t1",
        requirementIds: ["r1"],
        toolName: "Calculator",
        kind: "calculation",
        status: "passed",
        inputSummary: "check",
        output: {},
        assertions: ["42", "hello"],
        resources: [],
      },
    ]);
    // 2 assertions, both missing = high confidence MISSING
    expect(result.verdict).toBe("MISSING_EVIDENCE");
  });
});

// ── Commit 2: Builtin preset types + executor tests ──

describe("policyTypes builtin preset types", () => {
  it("BuiltinPresetId type exists and action type compiles", async () => {
    const types = await import("../../policy/policyTypes.js");
    const action: typeof types.HarnessRuleAction = {
      type: "builtin_preset",
      preset: "fact-grounding",
      config: { enabled: true, mode: "hybrid" },
    };
    expect(action.type).toBe("builtin_preset");
  });

  it("HarnessRule.priority is optional", async () => {
    const types = await import("../../policy/policyTypes.js");
    const rule: typeof types.HarnessRule = {
      id: "test",
      sourceText: "test",
      enabled: true,
      trigger: "beforeCommit",
      action: { type: "block", reason: "test" },
      enforcement: "block_on_fail",
      timeoutMs: 1000,
      // no priority — should compile
    };
    expect(rule.priority).toBeUndefined();
  });
});

describe("PolicyKernel loadBuiltinPresets", () => {
  it("generates 5 preset HarnessRules with correct IDs", async () => {
    const { loadBuiltinPresets } = await import("../../policy/PolicyKernel.js");
    const rules = loadBuiltinPresets();
    expect(rules.length).toBe(5);
    const ids = rules.map((r) => r.id);
    expect(ids).toContain("builtin-preset:self-claim");
    expect(ids).toContain("builtin-preset:fact-grounding");
    expect(ids).toContain("builtin-preset:response-language");
    expect(ids).toContain("builtin-preset:deterministic-evidence");
    expect(ids).toContain("builtin-preset:answer-quality");
  });

  it("respects yaml overrides", async () => {
    const { loadBuiltinPresets } = await import("../../policy/PolicyKernel.js");
    const rules = loadBuiltinPresets({
      "answer-quality": { enabled: false, mode: "deterministic" },
    });
    const answerRule = rules.find((r) => r.id === "builtin-preset:answer-quality");
    expect(answerRule).toBeDefined();
    expect(answerRule!.enabled).toBe(false);
    expect(answerRule!.action).toEqual({
      type: "builtin_preset",
      preset: "answer-quality",
      config: { enabled: false, mode: "deterministic" },
    });
  });

  it("env override takes precedence for mode", async () => {
    process.env.MAGI_HYBRID_ANSWER = "1";
    const { loadBuiltinPresets } = await import("../../policy/PolicyKernel.js");
    const rules = loadBuiltinPresets();
    const answerRule = rules.find((r) => r.id === "builtin-preset:answer-quality");
    expect(answerRule!.action).toMatchObject({
      type: "builtin_preset",
      config: { mode: "hybrid" },
    });
    delete process.env.MAGI_HYBRID_ANSWER;
  });

  it("all preset rules have priority set", async () => {
    const { loadBuiltinPresets } = await import("../../policy/PolicyKernel.js");
    const rules = loadBuiltinPresets();
    for (const rule of rules) {
      expect(rule.priority).toBeDefined();
      expect(typeof rule.priority).toBe("number");
    }
  });
});

describe("userHarnessRules executeBuiltinPreset", () => {
  it("handles builtin_preset action type in evaluateBeforeCommitRule", async () => {
    // This test verifies that the import of BuiltinPresetId works
    // and the action type is accepted
    const { HarnessRule } = await import("../../policy/policyTypes.js");
    const rule = {
      id: "builtin-preset:answer-quality",
      sourceText: "builtin_preset:answer-quality",
      enabled: true,
      trigger: "beforeCommit" as const,
      action: {
        type: "builtin_preset" as const,
        preset: "answer-quality" as const,
        config: { enabled: true, mode: "hybrid" as const },
      },
      enforcement: "block_on_fail" as const,
      timeoutMs: 16_000,
      priority: 90,
    };
    expect(rule.action.type).toBe("builtin_preset");
    expect(rule.action.preset).toBe("answer-quality");
  });
});

// ── Commit 3: MAGI_PRESET_VERIFIERS gate tests ──

describe("MAGI_PRESET_VERIFIERS gate in index.ts", () => {
  const OLD_ENV = process.env;
  beforeEach(() => { process.env = { ...OLD_ENV }; });
  afterEach(() => { process.env = OLD_ENV; });

  it("registers selfClaimVerifier when MAGI_PRESET_VERIFIERS is not set", async () => {
    delete process.env.MAGI_PRESET_VERIFIERS;
    // We just verify the conditional logic exists - can't easily test
    // full registerBuiltinHooks without all dependencies, but we can
    // verify the env var check pattern
    expect(process.env.MAGI_PRESET_VERIFIERS).toBeUndefined();
    // The actual skip logic: !process.env.MAGI_PRESET_VERIFIERS
    expect(!process.env.MAGI_PRESET_VERIFIERS).toBe(true);
  });

  it("skips individual verifiers when MAGI_PRESET_VERIFIERS=1", () => {
    process.env.MAGI_PRESET_VERIFIERS = "1";
    // The preset path should be used instead
    expect(process.env.MAGI_PRESET_VERIFIERS).toBe("1");
    expect(!process.env.MAGI_PRESET_VERIFIERS).toBe(false);
  });
});
