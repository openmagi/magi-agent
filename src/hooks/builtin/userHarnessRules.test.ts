import { describe, expect, it } from "vitest";
import type { HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { RuntimePolicySnapshot } from "../../policy/policyTypes.js";
import {
  makeUserHarnessRuleHooks,
  type UserHarnessRuleAgent,
} from "./userHarnessRules.js";

function makeSnapshot(
  harnessRules: RuntimePolicySnapshot["policy"]["harnessRules"],
): RuntimePolicySnapshot {
  return {
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
      harnessRules,
    },
    status: {
      executableDirectives: [],
      userDirectives: [],
      harnessDirectives: [],
      advisoryDirectives: [],
      warnings: [],
    },
  };
}

function successfulTool(
  name: string,
  toolUseId: string,
  input: unknown = {},
  output = "{}",
  opts: {
    turnId?: string;
    status?: string;
    isError?: boolean;
  } = {},
): TranscriptEntry[] {
  const turnId = opts.turnId ?? "turn";
  return [
    { kind: "tool_call", ts: 1, turnId, toolUseId, name, input },
    {
      kind: "tool_result",
      ts: 2,
      turnId,
      toolUseId,
      status: opts.status ?? "ok",
      output,
      isError: opts.isError ?? false,
    },
  ];
}

function makeCtx(
  transcript: TranscriptEntry[],
  events: unknown[] = [],
  llmOutput = "PASS",
): HookContext {
  return {
    botId: "bot",
    userId: "user",
    sessionKey: "session",
    turnId: "turn",
    llm: {
      stream: async function* () {
        yield { kind: "text_delta", blockIndex: 0, delta: llmOutput } as const;
        yield {
          kind: "message_end",
          stopReason: "end_turn",
          usage: { inputTokens: 1, outputTokens: 1 },
        } as const;
      },
    } as HookContext["llm"],
    transcript,
    emit: (event) => events.push(event),
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
  };
}

describe("userHarnessRules", () => {
  it("blocks beforeCommit when a required follow-up tool is missing", async () => {
    const transcript = successfulTool(
      "DocumentWrite",
      "tu_doc",
      { filename: "report.docx" },
      JSON.stringify({ filename: "report.docx", path: "report.docx" }),
    );
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "user-harness:file-delivery-after-create",
              sourceText: "파일 만들면 채팅에 첨부",
              enabled: true,
              trigger: "beforeCommit",
              condition: {
                anyToolUsed: ["DocumentWrite"],
              },
              action: { type: "require_tool", toolName: "FileDeliver" },
              enforcement: "block_on_fail",
              timeoutMs: 2_000,
            },
          ]),
      },
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "문서를 생성했습니다.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "docx 만들어줘",
        retryCount: 0,
        filesChanged: [],
      },
      makeCtx(transcript),
    );

    expect(out).toMatchObject({ action: "block" });
    expect(out && "reason" in out ? out.reason : "").toContain(
      "USER_HARNESS_RULE",
    );
  });

  it("continues when the required follow-up tool succeeded in the same turn", async () => {
    const transcript = [
      ...successfulTool(
        "DocumentWrite",
        "tu_doc",
        { filename: "report.docx" },
        JSON.stringify({ filename: "report.docx", path: "report.docx" }),
      ),
      ...successfulTool(
        "FileDeliver",
        "tu_deliver",
        { path: "report.docx", target: "chat" },
        JSON.stringify({ deliveries: [{ target: "chat", status: "sent" }] }),
      ),
    ];
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "user-harness:file-delivery-after-create",
              sourceText: "파일 만들면 채팅에 첨부",
              enabled: true,
              trigger: "beforeCommit",
              condition: { anyToolUsed: ["DocumentWrite"] },
              action: { type: "require_tool", toolName: "FileDeliver" },
              enforcement: "block_on_fail",
              timeoutMs: 2_000,
            },
          ]),
      },
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "문서를 생성하고 첨부했습니다.",
        toolCallCount: 2,
        toolReadHappened: false,
        userMessage: "docx 만들어줘",
        retryCount: 0,
        filesChanged: [],
      },
      makeCtx(transcript),
    );

    expect(out).toEqual({ action: "continue" });
  });

  it("runs llm verifier rules and blocks on FAIL", async () => {
    const events: unknown[] = [];
    const transcript: TranscriptEntry[] = [];
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "user-harness:final-answer-verifier",
              sourceText: "최종 답변 전 검사",
              enabled: true,
              trigger: "beforeCommit",
              action: {
                type: "llm_verifier",
                prompt: "Check whether the answer satisfies the request.",
              },
              enforcement: "block_on_fail",
              timeoutMs: 2_000,
            },
          ]),
      },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "다 됐습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "세부 분석을 해줘",
        retryCount: 0,
        filesChanged: [],
      },
      makeCtx(transcript, events, "FAIL: missing detail"),
    );

    expect(out).toMatchObject({ action: "block" });
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "rule_check",
        ruleId: "user-harness:final-answer-verifier",
        verdict: "violation",
      }),
    );
  });

  it("audits afterToolUse verifier rules without blocking tool execution", async () => {
    const events: unknown[] = [];
    const agent: UserHarnessRuleAgent = {
      readSessionTranscript: async () => [],
    };
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "user-harness:after-document-audit",
              sourceText: "문서 작성 후 검사",
              enabled: true,
              trigger: "afterToolUse",
              condition: { toolName: "DocumentWrite" },
              action: {
                type: "llm_verifier",
                prompt: "Check whether the document write result is usable.",
              },
              enforcement: "audit",
              timeoutMs: 2_000,
            },
          ]),
      },
      agent,
    });

    const out = await hooks.afterToolUse.handler(
      {
        toolName: "DocumentWrite",
        toolUseId: "tu_doc",
        input: { filename: "report.docx" },
        result: { status: "ok", output: "{}" },
      },
      makeCtx([], events, "FAIL"),
    );

    expect(out).toEqual({ action: "continue" });
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "rule_check",
        ruleId: "user-harness:after-document-audit",
        verdict: "violation",
      }),
    );
  });

  it("blocks beforeCommit when a required tool input match is missing", async () => {
    const transcript: TranscriptEntry[] = [];
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "tossplace-merchant-grounding",
              sourceText: "Toss POS 연결 상태 질문은 my-merchants로 확인",
              enabled: true,
              trigger: "beforeCommit",
              condition: {
                userMessageMatches:
                  "(토스|토스플레이스|POS).*(연결|연동|해제|등록|매장)",
              },
              action: {
                type: "require_tool_input_match",
                toolName: "Bash",
                inputPath: "command",
                pattern:
                  "integration\\.sh\\s+[\"']?tossplace/my-merchants",
              },
              enforcement: "block_on_fail",
              timeoutMs: 2_000,
            },
          ]),
      },
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "새 세션이라 연결 상태가 reset됐습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "토스 연결이 해제된거야?",
        retryCount: 0,
        filesChanged: [],
      },
      makeCtx(transcript),
    );

    expect(out).toMatchObject({ action: "block" });
    expect(out && "reason" in out ? out.reason : "").toContain(
      "required Bash input command matching",
    );
  });

  it("continues when the required tool input match succeeded in the same turn", async () => {
    const transcript = successfulTool("Bash", "tu_bash", {
      command: 'integration.sh "tossplace/my-merchants"',
    });
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "tossplace-merchant-grounding",
              sourceText: "Toss POS 연결 상태 질문은 my-merchants로 확인",
              enabled: true,
              trigger: "beforeCommit",
              condition: {
                userMessageMatches:
                  "(토스|토스플레이스|POS).*(연결|연동|해제|등록|매장)",
              },
              action: {
                type: "require_tool_input_match",
                toolName: "Bash",
                inputPath: "command",
                pattern:
                  "integration\\.sh\\s+[\"']?tossplace/my-merchants",
              },
              enforcement: "block_on_fail",
              timeoutMs: 2_000,
            },
          ]),
      },
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "두 매장이 연결되어 있습니다.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "토스 POS 매장 연결 상태 알려줘",
        retryCount: 0,
        filesChanged: [],
      },
      makeCtx(transcript),
    );

    expect(out).toEqual({ action: "continue" });
  });

  it("does not accept a different endpoint for required tool input match", async () => {
    const transcript = successfulTool("Bash", "tu_bash", {
      command: 'integration.sh "tossplace/sales-summary?merchantId=210391"',
    });
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "tossplace-merchant-grounding",
              sourceText: "Toss POS 연결 상태 질문은 my-merchants로 확인",
              enabled: true,
              trigger: "beforeCommit",
              condition: {
                userMessageMatches:
                  "(토스|토스플레이스|POS).*(연결|연동|해제|등록|매장)",
              },
              action: {
                type: "require_tool_input_match",
                toolName: "Bash",
                inputPath: "command",
                pattern:
                  "integration\\.sh\\s+[\"']?tossplace/my-merchants",
              },
              enforcement: "block_on_fail",
              timeoutMs: 2_000,
            },
          ]),
      },
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "매장 연결 상태입니다.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "POS 연동 상태 알려줘",
        retryCount: 0,
        filesChanged: [],
      },
      makeCtx(transcript),
    );

    expect(out).toMatchObject({ action: "block" });
  });

  it("skips required tool input match when user message regex does not match", async () => {
    const transcript: TranscriptEntry[] = [];
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "tossplace-merchant-grounding",
              sourceText: "Toss POS 연결 상태 질문은 my-merchants로 확인",
              enabled: true,
              trigger: "beforeCommit",
              condition: {
                userMessageMatches:
                  "(토스|토스플레이스|POS).*(연결|연동|해제|등록|매장)",
              },
              action: {
                type: "require_tool_input_match",
                toolName: "Bash",
                inputPath: "command",
                pattern:
                  "integration\\.sh\\s+[\"']?tossplace/my-merchants",
              },
              enforcement: "block_on_fail",
              timeoutMs: 2_000,
            },
          ]),
      },
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "오늘 날씨는 확인할 수 없습니다.",
        toolCallCount: 0,
        toolReadHappened: false,
        userMessage: "오늘 날씨 어때?",
        retryCount: 0,
        filesChanged: [],
      },
      makeCtx(transcript),
    );

    expect(out).toEqual({ action: "continue" });
  });

  it("does not accept failed or previous-turn tool input matches", async () => {
    const transcript = [
      ...successfulTool(
        "Bash",
        "tu_old",
        { command: 'integration.sh "tossplace/my-merchants"' },
        "{}",
        { turnId: "previous-turn" },
      ),
      ...successfulTool(
        "Bash",
        "tu_failed",
        { command: 'integration.sh "tossplace/my-merchants"' },
        "{}",
        { status: "error", isError: true },
      ),
    ];
    const hooks = makeUserHarnessRuleHooks({
      policy: {
        current: async () =>
          makeSnapshot([
            {
              id: "tossplace-merchant-grounding",
              sourceText: "Toss POS 연결 상태 질문은 my-merchants로 확인",
              enabled: true,
              trigger: "beforeCommit",
              condition: {
                userMessageMatches:
                  "(토스|토스플레이스|POS).*(연결|연동|해제|등록|매장)",
              },
              action: {
                type: "require_tool_input_match",
                toolName: "Bash",
                inputPath: "command",
                pattern:
                  "integration\\.sh\\s+[\"']?tossplace/my-merchants",
              },
              enforcement: "block_on_fail",
              timeoutMs: 2_000,
            },
          ]),
      },
      agent: { readSessionTranscript: async () => transcript },
    });

    const out = await hooks.beforeCommit.handler(
      {
        assistantText: "두 매장이 연결되어 있습니다.",
        toolCallCount: 1,
        toolReadHappened: false,
        userMessage: "토스 매장 등록 상태 알려줘",
        retryCount: 0,
        filesChanged: [],
      },
      makeCtx(transcript),
    );

    expect(out).toMatchObject({ action: "block" });
  });
});
