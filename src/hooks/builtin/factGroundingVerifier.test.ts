import { describe, it, expect, beforeEach, afterAll } from "vitest";
import {
  makeFactGroundingVerifierHook,
  judgeGrounding,
  parseGroundingVerdict,
} from "./factGroundingVerifier.js";
import type { GroundingVerdict, FactGroundingAgent } from "./factGroundingVerifier.js";
import type { HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { LLMClient } from "../../transport/LLMClient.js";

function makeCtx(
  transcript: TranscriptEntry[] = [],
  overrides: Partial<HookContext> = {},
): HookContext {
  return {
    botId: "test-bot",
    userId: "test-user",
    sessionKey: "test-session",
    turnId: "turn-1",
    llm: makeMockLlm("GROUNDED"),
    transcript,
    emit: () => {},
    log: () => {},
    abortSignal: new AbortController().signal,
    deadlineMs: 15000,
    ...overrides,
  };
}

function makeMockLlm(verdict: string): LLMClient {
  return {
    stream: () => {
      const events = [
        { kind: "text_delta" as const, delta: verdict },
        { kind: "message_end" as const },
      ];
      return (async function* () {
        for (const e of events) yield e;
      })();
    },
  } as unknown as LLMClient;
}

function toolCall(turnId: string, name: string, input: unknown): TranscriptEntry {
  return {
    kind: "tool_call",
    ts: Date.now(),
    turnId,
    toolUseId: `tu-${Math.random().toString(36).slice(2)}`,
    name,
    input,
  };
}

function toolResult(turnId: string, toolUseId: string, output: string): TranscriptEntry {
  return {
    kind: "tool_result",
    ts: Date.now(),
    turnId,
    toolUseId,
    status: "success",
    output,
  };
}

function makeToolPair(
  turnId: string,
  name: string,
  input: unknown,
  output: string,
): TranscriptEntry[] {
  const tuId = `tu-${Math.random().toString(36).slice(2)}`;
  return [
    { kind: "tool_call", ts: Date.now(), turnId, toolUseId: tuId, name, input },
    { kind: "tool_result", ts: Date.now(), turnId, toolUseId: tuId, status: "success", output },
  ];
}

describe("parseGroundingVerdict", () => {
  it("parses GROUNDED", () => {
    expect(parseGroundingVerdict("GROUNDED")).toBe("GROUNDED");
    expect(parseGroundingVerdict("  grounded  ")).toBe("GROUNDED");
  });

  it("parses DISTORTED", () => {
    expect(parseGroundingVerdict("DISTORTED")).toBe("DISTORTED");
  });

  it("parses FABRICATED", () => {
    expect(parseGroundingVerdict("FABRICATED")).toBe("FABRICATED");
  });

  it("defaults to GROUNDED on unrecognized output", () => {
    expect(parseGroundingVerdict("UNKNOWN")).toBe("GROUNDED");
    expect(parseGroundingVerdict("")).toBe("GROUNDED");
  });
});

describe("factGroundingVerifier hook", () => {
  const env = process.env;

  beforeEach(() => {
    // Default is OFF in production; tests need it ON to exercise the hook.
    process.env.MAGI_FACT_GROUNDING = "on";
  });

  afterAll(() => {
    process.env = env;
  });

  it("skips when no tool results this turn", async () => {
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "안녕하세요! React는 Virtual DOM을 사용합니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "React 설명해줘",
        retryCount: 0,
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("continues when Haiku says GROUNDED", async () => {
    const transcript = makeToolPair(
      "turn-1",
      "FileRead",
      { file_path: "/workspace/config.json" },
      '{"model": "gpt-4o", "temperature": 0.7}',
    );
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "config.json에 따르면 모델은 gpt-4o이고 temperature는 0.7입니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "설정 알려줘",
        retryCount: 0,
      },
      makeCtx(transcript, { llm: makeMockLlm("GROUNDED") }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks when Haiku says DISTORTED", async () => {
    const transcript = makeToolPair(
      "turn-1",
      "FileRead",
      { file_path: "/workspace/config.json" },
      '{"model": "gemini-2.5-pro", "temperature": 0.3}',
    );
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "config.json에 따르면 모델은 GPT-4o이고 temperature는 0.7입니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "설정 알려줘",
        retryCount: 0,
      },
      makeCtx(transcript, { llm: makeMockLlm("DISTORTED") }),
    );
    expect(result).toHaveProperty("action", "block");
  });

  it("blocks when Haiku says FABRICATED", async () => {
    const transcript = makeToolPair(
      "turn-1",
      "FileRead",
      { file_path: "/workspace/README.md" },
      "# My Project\nA simple todo app.",
    );
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "README.md에 따르면 이 프로젝트는 AI 기반 추천 엔진입니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "프로젝트 뭐야?",
        retryCount: 0,
      },
      makeCtx(transcript, { llm: makeMockLlm("FABRICATED") }),
    );
    expect(result).toHaveProperty("action", "block");
  });

  it("fails open after retry budget exhausted", async () => {
    const transcript = makeToolPair("turn-1", "FileRead", {}, "content");
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "파일에 따르면 뭔가 있습니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "test",
        retryCount: 1,
      },
      makeCtx(transcript, { llm: makeMockLlm("DISTORTED") }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("fails open on LLM timeout/error", async () => {
    const transcript = makeToolPair("turn-1", "FileRead", {}, "content");
    const errorLlm = {
      stream: () => {
        throw new Error("connection refused");
      },
    } as unknown as LLMClient;
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "파일에 따르면 뭔가 있습니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "test",
        retryCount: 0,
      },
      makeCtx(transcript, { llm: errorLlm }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("skips when disabled via env", async () => {
    process.env.MAGI_FACT_GROUNDING = "off";
    const transcript = makeToolPair("turn-1", "FileRead", {}, "content");
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "fabricated content",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "test",
        retryCount: 0,
      },
      makeCtx(transcript, { llm: makeMockLlm("FABRICATED") }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("uses delegate agent for transcript when provided", async () => {
    const transcript = makeToolPair(
      "turn-1",
      "FileRead",
      { file_path: "/workspace/data.json" },
      '{"count": 5}',
    );
    const agent: FactGroundingAgent = {
      readSessionTranscript: async () => transcript,
    };
    const hook = makeFactGroundingVerifierHook({ agent });
    const result = await hook.handler(
      {
        assistantText: "data.json에 count는 5입니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "데이터 확인해줘",
        retryCount: 0,
      },
      makeCtx([], { llm: makeMockLlm("GROUNDED") }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("truncates tool results exceeding 8K tokens", async () => {
    const longOutput = "x".repeat(40_000); // ~40KB
    const transcript = makeToolPair("turn-1", "FileRead", {}, longOutput);
    const capturedPrompts: string[] = [];
    const captureLlm = {
      stream: (opts: { messages: Array<{ content: string }> }) => {
        capturedPrompts.push(opts.messages[0]?.content ?? "");
        const events = [
          { kind: "text_delta" as const, delta: "GROUNDED" },
          { kind: "message_end" as const },
        ];
        return (async function* () {
          for (const e of events) yield e;
        })();
      },
    } as unknown as LLMClient;

    const hook = makeFactGroundingVerifierHook();
    await hook.handler(
      {
        assistantText: "파일 내용 확인했습니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "파일 읽어줘",
        retryCount: 0,
      },
      makeCtx(transcript, { llm: captureLlm }),
    );

    // The prompt sent to Haiku should be truncated
    expect(capturedPrompts.length).toBe(1);
    expect(capturedPrompts[0]!.length).toBeLessThan(40_000);
  });

  it("blocks ungrounded file claims when no tools used (mode B)", async () => {
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "파일을 다시 읽어보니 이미지 리사이즈는 1200픽셀로 설정되어 있습니다",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "해상도 몇이야?",
        retryCount: 0,
      },
      makeCtx([], { llm: makeMockLlm("FABRICATED") }),
    );
    expect(result).toHaveProperty("action", "block");
  });

  it("continues for general knowledge when no tools used (mode B)", async () => {
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "React는 Virtual DOM을 사용해서 효율적으로 렌더링합니다",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "React 설명해줘",
        retryCount: 0,
      },
      makeCtx([], { llm: makeMockLlm("GROUNDED") }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("treats DISTORTED as GROUNDED in mode B (no tools)", async () => {
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "설정이 이렇게 되어있는 것 같습니다",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "설정 확인해줘",
        retryCount: 0,
      },
      makeCtx([], { llm: makeMockLlm("DISTORTED") }),
    );
    // DISTORTED → GROUNDED in mode B
    expect(result).toEqual({ action: "continue" });
  });

  it("uses Mode B when tools fired but no read tools (e.g. NotifyUser only)", async () => {
    // This was the critical bug: toolCallCount > 0 but toolReadHappened
    // = false → old code routed to Mode A which failed open on empty
    // transcript. New code routes to Mode B (Haiku ungrounded claims).
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "파일을 확인해보니 AEF 3단계로 구성되어 있고 제미나이 3.1 프로를 사용합니다",
        toolCallCount: 2, // e.g. NotifyUser + CronCreate
        toolReadHappened: false,
        userMessage: "WSJ 파이프라인 설명해줘",
        retryCount: 0,
      },
      makeCtx([], { llm: makeMockLlm("FABRICATED") }),
    );
    expect(result).toHaveProperty("action", "block");
  });

  it("skips when assistant text is empty", async () => {
    const hook = makeFactGroundingVerifierHook();
    const result = await hook.handler(
      {
        assistantText: "",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "test",
        retryCount: 0,
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });
});
