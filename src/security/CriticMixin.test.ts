import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { HookContext, HookArgs, HookResult } from "../hooks/types.js";
import type { AgentEvent } from "../transport/SseWriter.js";

import {
  makeCriticGateHook,
  HallucinationScorer,
  CompletionScorer,
  SecurityScorer,
  type CriticConfig,
  type CriticInput,
  type CriticScore,
  type CriticScorer,
} from "./CriticMixin.js";

function makeCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "test-bot",
    userId: "test-user",
    sessionKey: "sk-test",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: vi.fn(),
    log: vi.fn(),
    agentModel: "claude-opus-4-6",
    abortSignal: new AbortController().signal,
    deadlineMs: 10_000,
    ...overrides,
  };
}

function makeArgs(
  overrides: Partial<HookArgs["beforeCommit"]> = {},
): HookArgs["beforeCommit"] {
  return {
    assistantText: "The file contains a config for deployment.",
    userMessage: "What's in the config?",
    toolCallCount: 1,
    toolReadHappened: true,
    retryCount: 0,
    filesChanged: [],
    ...overrides,
  };
}

function constantScorer(score: number, name = "test-scorer"): CriticScorer {
  return {
    name,
    score: async () => ({
      score,
      reason: `constant ${score}`,
      suggestions: [],
      scoredBy: name,
    }),
  };
}

describe("CriticMixin", () => {
  let origEnv: string | undefined;

  beforeEach(() => {
    origEnv = process.env.MAGI_CRITIC_GATE;
    process.env.MAGI_CRITIC_GATE = "1";
  });

  afterEach(() => {
    if (origEnv === undefined) {
      delete process.env.MAGI_CRITIC_GATE;
    } else {
      process.env.MAGI_CRITIC_GATE = origEnv;
    }
  });

  describe("makeCriticGateHook", () => {
    it("returns a beforeCommit hook at priority 92", () => {
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: constantScorer(1.0),
        buildFollowup: () => "fix it",
      });
      expect(hook.name).toBe("builtin:critic-gate");
      expect(hook.point).toBe("beforeCommit");
      expect(hook.priority).toBe(92);
      expect(hook.failOpen).toBe(true);
    });

    it("passes when score >= threshold", async () => {
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: constantScorer(0.85),
        buildFollowup: () => "fix it",
      });
      const result = await hook.handler(makeArgs(), makeCtx());
      expect(result).toEqual({ action: "continue" });
    });

    it("blocks with followup when score < threshold and retryCount < maxRetries", async () => {
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: constantScorer(0.3),
        buildFollowup: (score, userMsg) =>
          `[RETRY:CRITIC] score=${score.score} for "${userMsg}"`,
      });
      const result = await hook.handler(
        makeArgs({ retryCount: 0 }),
        makeCtx(),
      );
      expect(result).toEqual({
        action: "block",
        reason: expect.stringContaining("[RETRY:CRITIC]"),
      });
    });

    it("blocks definitively when score < threshold and retryCount >= maxRetries", async () => {
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: constantScorer(0.3),
        buildFollowup: () => "fix it",
      });
      const result = await hook.handler(
        makeArgs({ retryCount: 2 }),
        makeCtx(),
      );
      expect(result).toEqual({ action: "continue" });
    });

    it("retryCount=1 with maxRetries=2 still blocks (not exhausted)", async () => {
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: constantScorer(0.3),
        buildFollowup: () => "fix it",
      });
      const result = await hook.handler(
        makeArgs({ retryCount: 1 }),
        makeCtx(),
      );
      expect(result).toEqual({
        action: "block",
        reason: "fix it",
      });
    });

    it("emits rule_check event on pass", async () => {
      const emitFn = vi.fn();
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: constantScorer(0.9),
        buildFollowup: () => "fix it",
      });
      await hook.handler(makeArgs(), makeCtx({ emit: emitFn }));
      expect(emitFn).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "rule_check",
          ruleId: "critic-gate",
          verdict: "ok",
        }),
      );
    });

    it("emits rule_check event on block", async () => {
      const emitFn = vi.fn();
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: constantScorer(0.2),
        buildFollowup: () => "fix it",
      });
      await hook.handler(
        makeArgs({ retryCount: 0 }),
        makeCtx({ emit: emitFn }),
      );
      expect(emitFn).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "rule_check",
          ruleId: "critic-gate",
          verdict: "violation",
        }),
      );
    });

    it("fails open on scorer error", async () => {
      const failingScorer: CriticScorer = {
        name: "fail-scorer",
        score: async () => {
          throw new Error("scorer crashed");
        },
      };
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: failingScorer,
        buildFollowup: () => "fix it",
      });
      const logFn = vi.fn();
      const result = await hook.handler(
        makeArgs(),
        makeCtx({ log: logFn }),
      );
      expect(result).toEqual({ action: "continue" });
      expect(logFn).toHaveBeenCalledWith(
        "warn",
        expect.stringContaining("critic-gate"),
        expect.any(Object),
      );
    });

    it("skips when MAGI_CRITIC_GATE is not enabled", async () => {
      process.env.MAGI_CRITIC_GATE = "0";
      const scorerFn = vi.fn();
      const scorer: CriticScorer = {
        name: "should-not-run",
        score: scorerFn as unknown as CriticScorer["score"],
      };
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer,
        buildFollowup: () => "fix it",
      });
      const result = await hook.handler(makeArgs(), makeCtx());
      expect(result).toEqual({ action: "continue" });
      expect(scorerFn).not.toHaveBeenCalled();
    });

    it("passes empty assistant text without scoring", async () => {
      const scorerFn = vi.fn();
      const scorer: CriticScorer = {
        name: "should-not-run",
        score: scorerFn as unknown as CriticScorer["score"],
      };
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer,
        buildFollowup: () => "fix it",
      });
      const result = await hook.handler(
        makeArgs({ assistantText: "" }),
        makeCtx(),
      );
      expect(result).toEqual({ action: "continue" });
      expect(scorerFn).not.toHaveBeenCalled();
    });

    it("score exactly at threshold passes", async () => {
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: constantScorer(0.7),
        buildFollowup: () => "fix it",
      });
      const result = await hook.handler(makeArgs(), makeCtx());
      expect(result).toEqual({ action: "continue" });
    });
  });

  describe("HallucinationScorer", () => {
    it("returns 1.0 when both factGrounding=ok and resourceExistence=ok", () => {
      const scorer = new HallucinationScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 1,
        toolReadHappened: true,
        turnId: "t1",
        hookVerdicts: {
          "fact-grounding-verifier": "ok",
          "resource-existence-checker": "ok",
        },
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(1.0);
    });

    it("returns < 0.5 when factGrounding=violation", () => {
      const scorer = new HallucinationScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 1,
        toolReadHappened: true,
        turnId: "t1",
        hookVerdicts: {
          "fact-grounding-verifier": "violation",
          "resource-existence-checker": "ok",
        },
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBeLessThan(0.5);
    });

    it("returns 0.5 when only resourceExistence=violation", () => {
      const scorer = new HallucinationScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 1,
        toolReadHappened: true,
        turnId: "t1",
        hookVerdicts: {
          "fact-grounding-verifier": "ok",
          "resource-existence-checker": "violation",
        },
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(0.5);
    });

    it("returns 1.0 when no verdicts present (no upstream hooks ran)", () => {
      const scorer = new HallucinationScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 0,
        toolReadHappened: false,
        turnId: "t1",
        hookVerdicts: {},
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(1.0);
    });
  });

  describe("CompletionScorer", () => {
    it("returns 1.0 when both completionEvidence=ok and taskContract=ok", () => {
      const scorer = new CompletionScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 1,
        toolReadHappened: true,
        turnId: "t1",
        hookVerdicts: {
          "completion-evidence-gate": "ok",
          "task-contract-gate": "ok",
        },
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(1.0);
    });

    it("returns < 0.5 when completionEvidence=violation", () => {
      const scorer = new CompletionScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 1,
        toolReadHappened: true,
        turnId: "t1",
        hookVerdicts: {
          "completion-evidence-gate": "violation",
          "task-contract-gate": "ok",
        },
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBeLessThan(0.5);
    });
  });

  describe("SecurityScorer", () => {
    it("maps safe/pass → 1.0", () => {
      const scorer = new SecurityScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 0,
        toolReadHappened: false,
        turnId: "t1",
        ensembleSeverity: "pass",
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(1.0);
    });

    it("maps high → 0.2", () => {
      const scorer = new SecurityScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 0,
        toolReadHappened: false,
        turnId: "t1",
        ensembleSeverity: "deny",
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(0.0);
    });

    it("maps ask → 0.5", () => {
      const scorer = new SecurityScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 0,
        toolReadHappened: false,
        turnId: "t1",
        ensembleSeverity: "ask",
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(0.5);
    });

    it("maps unknown → 0.3", () => {
      const scorer = new SecurityScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 0,
        toolReadHappened: false,
        turnId: "t1",
        ensembleSeverity: "unknown",
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(0.3);
    });

    it("returns 1.0 when no ensemble severity present", () => {
      const scorer = new SecurityScorer();
      const input: CriticInput = {
        assistantText: "test",
        userMessage: "test",
        toolCallCount: 0,
        toolReadHappened: false,
        turnId: "t1",
      };
      const result = scorer.scoreSync(input);
      expect(result.score).toBe(1.0);
    });
  });

  describe("CompositeScorer (multiple scorers)", () => {
    it("uses minimum score across multiple scorers", async () => {
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: [constantScorer(0.9, "high"), constantScorer(0.3, "low")],
        buildFollowup: () => "fix it",
      });
      const result = await hook.handler(
        makeArgs({ retryCount: 0 }),
        makeCtx(),
      );
      expect(result).toEqual({
        action: "block",
        reason: "fix it",
      });
    });

    it("passes when all scorers are above threshold", async () => {
      const hook = makeCriticGateHook({
        threshold: 0.7,
        maxRetries: 2,
        scorer: [constantScorer(0.8, "a"), constantScorer(0.9, "b")],
        buildFollowup: () => "fix it",
      });
      const result = await hook.handler(makeArgs(), makeCtx());
      expect(result).toEqual({ action: "continue" });
    });
  });
});
