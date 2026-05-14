import { describe, it, expect, beforeEach, afterAll } from "vitest";
import {
  makeResourceExistenceCheckerHook,
  extractFileReferences,
  hasContentClaim,
  matchesGenericReadClaim,
} from "./resourceExistenceChecker.js";
import type { ResourceCheckAgent } from "./resourceExistenceChecker.js";
import type { HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";

function makeCtx(
  transcript: TranscriptEntry[] = [],
  overrides: Partial<HookContext> = {},
): HookContext {
  return {
    botId: "test-bot",
    userId: "test-user",
    sessionKey: "test-session",
    turnId: "turn-1",
    llm: {} as HookContext["llm"],
    transcript,
    emit: () => {},
    log: () => {},
    abortSignal: new AbortController().signal,
    deadlineMs: 5000,
    ...overrides,
  };
}

function fileReadCall(turnId: string, filePath: string): TranscriptEntry {
  return {
    kind: "tool_call",
    ts: Date.now(),
    turnId,
    toolUseId: `tu-${Math.random().toString(36).slice(2)}`,
    name: "FileRead",
    input: { file_path: filePath },
  };
}

function grepCall(turnId: string, pattern: string, path?: string): TranscriptEntry {
  return {
    kind: "tool_call",
    ts: Date.now(),
    turnId,
    toolUseId: `tu-${Math.random().toString(36).slice(2)}`,
    name: "Grep",
    input: { pattern, path },
  };
}

describe("extractFileReferences", () => {
  it("registers as fail-open so verifier outages do not abort turns", () => {
    const hook = makeResourceExistenceCheckerHook();

    expect(hook.failOpen).toBe(true);
  });

  it("extracts .md file references", () => {
    const refs = extractFileReferences("DAILY_RUNBOOK_v3.md 파일에 따르면 구조는 다음과 같습니다");
    expect(refs).toContain("DAILY_RUNBOOK_v3.md");
  });

  it("extracts path-style references", () => {
    const refs = extractFileReferences("src/config.ts에 명시된 설정값은");
    expect(refs).toContain("src/config.ts");
  });

  it("extracts backtick-quoted file references", () => {
    const refs = extractFileReferences("As shown in `config.json`, the value is 42");
    expect(refs).toContain("config.json");
  });

  it("extracts multiple file references", () => {
    const refs = extractFileReferences(
      "SOUL.md에 따르면 이런 규칙이 있고, TOOLS.md에 명시된 도구는",
    );
    expect(refs).toContain("SOUL.md");
    expect(refs).toContain("TOOLS.md");
  });

  it("does not extract non-file patterns", () => {
    const refs = extractFileReferences("안녕하세요 오늘 날씨가 좋네요");
    expect(refs).toHaveLength(0);
  });

  it("does not extract bare extensions like .com or .org", () => {
    const refs = extractFileReferences("google.com에서 검색해보세요");
    // .com is not a code file extension
    expect(refs.filter((r) => r === "google.com")).toHaveLength(0);
  });
});

describe("hasContentClaim", () => {
  it("detects Korean content claims", () => {
    expect(hasContentClaim("DAILY_RUNBOOK_v3.md", "DAILY_RUNBOOK_v3.md에 따르면 Actor는 Gemini입니다")).toBe(true);
    expect(hasContentClaim("config.json", "config.json에 명시된 값은 42입니다")).toBe(true);
  });

  it("detects English content claims", () => {
    expect(hasContentClaim("config.json", "config.json contains the API key")).toBe(true);
    expect(hasContentClaim("README.md", "As stated in README.md, the project uses React")).toBe(true);
  });

  it("returns false for mere file mention", () => {
    expect(hasContentClaim("config.json", "config.json을 확인해보세요")).toBe(false);
    expect(hasContentClaim("README.md", "Please check README.md for details")).toBe(false);
  });
});

describe("resourceExistenceChecker hook", () => {
  const env = process.env;

  beforeEach(() => {
    delete process.env.MAGI_RESOURCE_CHECK;
  });

  afterAll(() => {
    process.env = env;
  });

  it("continues when no file references in response", async () => {
    const hook = makeResourceExistenceCheckerHook();
    const result = await hook.handler(
      {
        assistantText: "안녕하세요! 무엇을 도와드릴까요?",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "안녕",
        retryCount: 0,
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("continues when file is referenced but no content claim", async () => {
    const hook = makeResourceExistenceCheckerHook();
    const result = await hook.handler(
      {
        assistantText: "config.json을 확인해보시겠어요?",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "설정 어디있어?",
        retryCount: 0,
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks when file content claimed without reading", async () => {
    const hook = makeResourceExistenceCheckerHook();
    const result = await hook.handler(
      {
        assistantText: "DAILY_RUNBOOK_v3.md에 따르면 Actor는 Gemini 2.5 Flash를 사용합니다",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "WSJ 파이프라인 설명해줘",
        retryCount: 0,
      },
      makeCtx(),
    );
    expect(result).toHaveProperty("action", "block");
  });

  it("continues when file was read this turn via FileRead", async () => {
    const transcript: TranscriptEntry[] = [
      fileReadCall("turn-1", "/workspace/DAILY_RUNBOOK_v3.md"),
    ];
    const hook = makeResourceExistenceCheckerHook();
    const result = await hook.handler(
      {
        assistantText: "DAILY_RUNBOOK_v3.md에 따르면 Actor는 Gemini 2.5 Pro를 사용합니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "WSJ 파이프라인 설명해줘",
        retryCount: 0,
      },
      makeCtx(transcript),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("continues when file was found via Grep this turn", async () => {
    const transcript: TranscriptEntry[] = [
      grepCall("turn-1", "Actor", "/workspace/DAILY_RUNBOOK_v3.md"),
    ];
    const hook = makeResourceExistenceCheckerHook();
    const result = await hook.handler(
      {
        assistantText: "DAILY_RUNBOOK_v3.md에 따르면 Actor는 Gemini을 사용합니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "WSJ 파이프라인 설명해줘",
        retryCount: 0,
      },
      makeCtx(transcript),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("fails open after retry budget exhausted", async () => {
    const hook = makeResourceExistenceCheckerHook();
    const result = await hook.handler(
      {
        assistantText: "DAILY_RUNBOOK_v3.md에 따르면 Actor는 Gemini입니다",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "WSJ 파이프라인 설명해줘",
        retryCount: 1,
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("skips when disabled via env", async () => {
    process.env.MAGI_RESOURCE_CHECK = "off";
    const hook = makeResourceExistenceCheckerHook();
    const result = await hook.handler(
      {
        assistantText: "DAILY_RUNBOOK_v3.md에 따르면 뭔가 있습니다",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "test",
        retryCount: 0,
      },
      makeCtx(),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("uses delegate agent for transcript when provided", async () => {
    const transcript: TranscriptEntry[] = [
      fileReadCall("turn-1", "/workspace/DAILY_RUNBOOK_v3.md"),
    ];
    const agent: ResourceCheckAgent = {
      readSessionTranscript: async () => transcript,
    };
    const hook = makeResourceExistenceCheckerHook({ agent });
    const result = await hook.handler(
      {
        assistantText: "DAILY_RUNBOOK_v3.md에 따르면 설정값이 있습니다",
        toolCallCount: 1,
        toolReadHappened: true,
        userMessage: "설정 알려줘",
        retryCount: 0,
      },
      makeCtx([]), // empty ctx.transcript — delegate provides it
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("fails open when delegate throws", async () => {
    const agent: ResourceCheckAgent = {
      readSessionTranscript: async () => {
        throw new Error("disk read failed");
      },
    };
    const hook = makeResourceExistenceCheckerHook({ agent });
    const result = await hook.handler(
      {
        assistantText: "DAILY_RUNBOOK_v3.md에 따르면 뭔가 있습니다",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "test",
        retryCount: 0,
      },
      makeCtx(),
    );
    // Falls back to ctx.transcript (empty) → would block, but delegate
    // error causes fail-open
    expect(result).toEqual({ action: "continue" });
  });

  it("only reports first unread file when multiple are unread", async () => {
    const hook = makeResourceExistenceCheckerHook();
    const result = await hook.handler(
      {
        assistantText:
          "SOUL.md에 따르면 Phase 1이 있고, TOOLS.md에 명시된 도구 목록은 다음과 같습니다",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "봇 설정 알려줘",
        retryCount: 0,
      },
      makeCtx(),
    );
    expect(result).toHaveProperty("action", "block");
    const reason = (result as { action: "block"; reason: string }).reason;
    // Should mention one specific file
    expect(reason).toMatch(/SOUL\.md|TOOLS\.md/);
  });
});
