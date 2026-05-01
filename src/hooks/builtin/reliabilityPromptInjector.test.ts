import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { HookContext } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";
import {
  buildReliabilityPolicyBlock,
  makeReliabilityPromptInjectorHook,
} from "./reliabilityPromptInjector.js";

function makeCtx(): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as HookContext["llm"],
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
}

function userMessage(text: string): LLMMessage[] {
  return [{ role: "user", content: text }];
}

describe("reliabilityPromptInjector", () => {
  const originalEnv = process.env.CORE_AGENT_RELIABILITY_PROMPT;

  beforeEach(() => {
    delete process.env.CORE_AGENT_RELIABILITY_PROMPT;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.CORE_AGENT_RELIABILITY_PROMPT;
    } else {
      process.env.CORE_AGENT_RELIABILITY_PROMPT = originalEnv;
    }
  });

  it("builds debugging policy for failure turns", () => {
    const block = buildReliabilityPolicyBlock("테스트 실패 원인을 찾아서 고쳐줘");
    expect(block).toContain("systematic-debugging");
    expect(block).toContain("verification-before-completion");
  });

  it("builds evidence policy for current/source-sensitive turns", () => {
    const block = buildReliabilityPolicyBlock("최신 가격을 검색하고 출처도 달아줘");
    expect(block).toContain("product reliability and benchmark evaluation");
    expect(block).toContain("tool/file evidence");
    expect(block).toContain("evidence-router");
    expect(block).toContain("current sources");
  });

  it("does not turn simple file understanding into evidence routing", () => {
    const block = buildReliabilityPolicyBlock("WSJ 파이프라인 파일 뭐하는건지 알려줘");
    expect(block).toContain("runtime-evidence-policy");
    expect(block).not.toContain("evidence-router");
  });

  it("still builds evidence policy for document requests that need citations or verification", () => {
    const block = buildReliabilityPolicyBlock("이 PDF 문서에서 근거를 추출하고 출처도 표시해줘");
    expect(block).toContain("evidence-router");
  });

  it("injects policy into the system prompt on first iteration", async () => {
    const hook = makeReliabilityPromptInjectorHook();
    expect(hook.blocking).toBe(true);
    const result = await hook.handler(
      {
        messages: userMessage("빌드 에러 고쳐줘"),
        tools: [],
        system: "base system",
        iteration: 0,
      },
      makeCtx(),
    );
    expect(result?.action).toBe("replace");
    if (result?.action === "replace") {
      expect(result.value.system).toContain("<reliability-policy>");
      expect(result.value.system).toContain("systematic-debugging");
    }
  });

  it("injects the runtime evidence policy even when no conditional trigger matches", async () => {
    const hook = makeReliabilityPromptInjectorHook();
    const result = await hook.handler(
      {
        messages: userMessage("안녕"),
        tools: [],
        system: "base system",
        iteration: 0,
      },
      makeCtx(),
    );
    expect(result?.action).toBe("replace");
    if (result?.action === "replace") {
      expect(result.value.system).toContain("<runtime-evidence-policy>");
      expect(result.value.system).toContain("product reliability and benchmark evaluation");
      expect(result.value.system).not.toContain("evidence-router");
    }
  });

  it("respects CORE_AGENT_RELIABILITY_PROMPT=off", async () => {
    process.env.CORE_AGENT_RELIABILITY_PROMPT = "off";
    const hook = makeReliabilityPromptInjectorHook();
    const result = await hook.handler(
      {
        messages: userMessage("테스트 실패 원인을 찾아줘"),
        tools: [],
        system: "base system",
        iteration: 0,
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });
});
