import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import { ExecutionContractStore } from "../../execution/ExecutionContract.js";
import {
  collectCreatedArtifacts,
  hasArtifactDeliveryEvidence,
  hasKbWriteEvidence,
  makeArtifactDeliveryGateHook,
} from "./artifactDeliveryGate.js";

interface MetaOptions {
  wantsChat?: boolean;
  wantsKb?: boolean;
  wantsFile?: boolean;
  claimsFileCreated?: boolean;
  claimsChat?: boolean;
  claimsKb?: boolean;
  reportsFailure?: boolean;
}

function makeCtx(transcript: TranscriptEntry[] = [], meta: MetaOptions = {}): HookContext {
  const store = new ExecutionContractStore({ now: () => 1 });
  const llm = {
    stream: (request: { system?: string }) =>
      (async function* () {
        const isRequest = String(request.system ?? "").includes("runtime-control classifier");
        yield {
          kind: "text_delta" as const,
          delta: JSON.stringify(
            isRequest
              ? {
                  turnMode: { label: "other", confidence: 0.9 },
                  skipTdd: false,
                  implementationIntent: false,
                  documentOrFileOperation: meta.wantsFile ?? false,
                  deterministic: {
                    requiresDeterministic: false,
                    kinds: [],
                    reason: "No deterministic requirement.",
                    suggestedTools: [],
                    acceptanceCriteria: [],
                  },
                  fileDelivery: {
                    intent: meta.wantsChat ? "deliver_existing" : "none",
                    path: meta.wantsChat ? "report.md" : null,
                    wantsChatDelivery: meta.wantsChat ?? false,
                    wantsKbDelivery: meta.wantsKb ?? false,
                    wantsFileOutput: meta.wantsFile ?? meta.wantsChat ?? meta.wantsKb ?? false,
                  },
                }
              : {
                  internalReasoningLeak: false,
                  lazyRefusal: false,
                  selfClaim: false,
                  deferralPromise: false,
                  assistantClaimsFileCreated: meta.claimsFileCreated ?? false,
                  assistantClaimsChatDelivery: meta.claimsChat ?? false,
                  assistantClaimsKbDelivery: meta.claimsKb ?? false,
                  assistantReportsDeliveryFailure: meta.reportsFailure ?? false,
                  reason: "test classifier output",
                },
          ),
        };
        yield { kind: "message_end" as const };
      })(),
  } as HookContext["llm"];
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm,
    transcript,
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    executionContract: store,
  };
}

function args(assistantText: string, userMessage: string, retryCount = 0) {
  return {
    assistantText,
    toolCallCount: 0,
    toolReadHappened: false,
    userMessage,
    retryCount,
  };
}

function successfulFileWrite(path: string): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: 1,
      turnId: "turn-test",
      toolUseId: "tool-1",
      name: "FileWrite",
      input: { path, content: "report" },
    },
    {
      kind: "tool_result",
      ts: 2,
      turnId: "turn-test",
      toolUseId: "tool-1",
      status: "ok",
      output: JSON.stringify({ path, bytesWritten: 6 }),
    },
  ];
}

function successfulDocumentWrite(filename = "report.md"): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: 1,
      turnId: "turn-test",
      toolUseId: "tool-doc",
      name: "DocumentWrite",
      input: {
        mode: "create",
        format: "md",
        title: "Report",
        filename,
        source: "# Report\n\nBody",
      },
    },
    {
      kind: "tool_result",
      ts: 2,
      turnId: "turn-test",
      toolUseId: "tool-doc",
      status: "ok",
      output: JSON.stringify({
        artifactId: "artifact-1",
        workspacePath: filename,
        filename,
      }),
    },
  ];
}

function successfulFileDeliver(
  deliveries: Array<{ target: "chat" | "kb"; marker?: string; externalId?: string }>,
): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: 3,
      turnId: "turn-test",
      toolUseId: "tool-deliver",
      name: "FileDeliver",
      input: {
        artifactId: "artifact-1",
        target: deliveries.length === 2 ? "both" : deliveries[0]?.target ?? "chat",
      },
    },
    {
      kind: "tool_result",
      ts: 4,
      turnId: "turn-test",
      toolUseId: "tool-deliver",
      status: "ok",
      output: JSON.stringify({
        deliveries: deliveries.map((delivery) => ({
          ...delivery,
          status: "sent",
          attemptCount: 1,
        })),
      }),
    },
  ];
}

function successfulFileSend(): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: 3,
      turnId: "turn-test",
      toolUseId: "tool-send",
      name: "FileSend",
      input: {
        path: "report.md",
      },
    },
    {
      kind: "tool_result",
      ts: 4,
      turnId: "turn-test",
      toolUseId: "tool-send",
      status: "ok",
      output: JSON.stringify({
        filename: "report.md",
        channel: { type: "telegram", channelId: "1234" },
        mode: "document",
      }),
    },
  ];
}

function successfulWebFileSend(marker: string): TranscriptEntry[] {
  return [
    {
      kind: "tool_call",
      ts: 3,
      turnId: "turn-test",
      toolUseId: "tool-send",
      name: "FileSend",
      input: {
        path: "report.md",
      },
    },
    {
      kind: "tool_result",
      ts: 4,
      turnId: "turn-test",
      toolUseId: "tool-send",
      status: "ok",
      output: JSON.stringify({
        id: "00000000-0000-4000-8000-000000000000",
        filename: "report.md",
        marker,
      }),
    },
  ];
}

describe("artifactDeliveryGate helpers", () => {
  it("collects user-facing files created in the current turn", () => {
    const artifacts = collectCreatedArtifacts(successfulFileWrite("workspace/reports/debate-verdict.md"), "turn-test");
    expect(artifacts).toEqual([
      {
        kind: "file",
        name: "debate-verdict.md",
        path: "workspace/reports/debate-verdict.md",
        toolName: "FileWrite",
      },
    ]);
  });

  it("ignores internal workspace state files", () => {
    expect(
      collectCreatedArtifacts(successfulFileWrite("memory/daily/2026-04-23.md"), "turn-test"),
    ).toEqual([]);
    expect(
      collectCreatedArtifacts(successfulFileWrite("LEARNING.md"), "turn-test"),
    ).toEqual([]);
  });

  it("collects user-facing documents created by DocumentWrite", () => {
    const artifacts = collectCreatedArtifacts(successfulDocumentWrite("report.md"), "turn-test");
    expect(artifacts).toEqual([
      {
        kind: "artifact",
        name: "report.md",
        path: "report.md",
        artifactId: "artifact-1",
        toolName: "DocumentWrite",
      },
    ]);
  });

  it("accepts chat attachment markers as delivery evidence", () => {
    expect(
      hasArtifactDeliveryEvidence(
        "생성한 파일입니다. [attachment:00000000-0000-4000-8000-000000000000:debate-verdict.md]",
        [],
        "turn-test",
      ),
    ).toBe(true);
  });

  it("accepts native FileDeliver KB results as KB write evidence", () => {
    expect(
      hasKbWriteEvidence(
        successfulFileDeliver([{ target: "kb", externalId: "artifacts/report.md" }]),
        "turn-test",
      ),
    ).toBe(true);
  });
});

describe("artifactDeliveryGate hook", () => {
  const originalEnv = process.env.CORE_AGENT_ARTIFACT_DELIVERY_GATE;

  beforeEach(() => {
    delete process.env.CORE_AGENT_ARTIFACT_DELIVERY_GATE;
  });

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.CORE_AGENT_ARTIFACT_DELIVERY_GATE;
    } else {
      process.env.CORE_AGENT_ARTIFACT_DELIVERY_GATE = originalEnv;
    }
  });

  it("blocks generated files when the user explicitly asked for a chat attachment", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 생성했습니다.", "파일 KB에 저장하고 여기 채팅에도 첨부해줘"),
      makeCtx(successfulFileWrite("workspace/duol-debate/debate-verdict.md"), {
        wantsChat: true,
        wantsKb: true,
        wantsFile: true,
        claimsFileCreated: true,
      }),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[RETRY:ARTIFACT_DELIVERY]");
      expect(result.reason).toContain("file-send.sh");
    }
  });

  it("continues when a generated file is attached in the final answer", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args(
        "파일을 생성하고 첨부했습니다.\n[attachment:00000000-0000-4000-8000-000000000000:debate-verdict.md]",
        "파일 여기 채팅에도 첨부해줘",
      ),
      makeCtx(successfulFileWrite("workspace/duol-debate/debate-verdict.md"), {
        wantsChat: true,
        wantsFile: true,
        claimsChat: true,
        claimsFileCreated: true,
      }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks KB save requests unless there is KB write or attachment evidence", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 생성했습니다.", "이 리포트 KB에 저장해줘"),
      makeCtx(successfulFileWrite("workspace/duol-debate/debate-verdict.md"), {
        wantsKb: true,
        wantsFile: true,
        claimsFileCreated: true,
      }),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("kb-write.sh");
    }
  });

  it("allows KB save requests with same-turn kb-write evidence", async () => {
    const transcript: TranscriptEntry[] = [
      ...successfulFileWrite("workspace/duol-debate/debate-verdict.md"),
      {
        kind: "tool_call",
        ts: 3,
        turnId: "turn-test",
        toolUseId: "tool-2",
        name: "Bash",
        input: {
          command:
            "cat workspace/duol-debate/debate-verdict.md | kb-write.sh --add 'Reports' 'debate-verdict.md' --stdin",
        },
      },
      {
        kind: "tool_result",
        ts: 4,
        turnId: "turn-test",
        toolUseId: "tool-2",
        status: "ok",
        output: '{"ok":true}',
      },
    ];
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 생성하고 KB에 저장했습니다.", "이 리포트 KB에 저장해줘"),
      makeCtx(transcript, {
        wantsKb: true,
        wantsFile: true,
        claimsFileCreated: true,
        claimsKb: true,
      }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("allows KB save requests with same-turn native FileDeliver KB evidence", async () => {
    const transcript: TranscriptEntry[] = [
      ...successfulDocumentWrite("report.md"),
      ...successfulFileDeliver([{ target: "kb", externalId: "artifacts/report.md" }]),
    ];
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 생성하고 KB에 저장했습니다.", "이 리포트 KB에 저장해줘"),
      makeCtx(transcript, {
        wantsKb: true,
        wantsFile: true,
        claimsFileCreated: true,
        claimsKb: true,
      }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks native FileDeliver chat results unless the returned marker is in the final answer", async () => {
    const transcript: TranscriptEntry[] = [
      ...successfulFileDeliver([
        {
          target: "chat",
          externalId: "00000000-0000-4000-8000-000000000000",
          marker: "[attachment:00000000-0000-4000-8000-000000000000:report.md]",
        },
      ]),
    ];
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 첨부했습니다.", "파일 첨부해줘"),
      makeCtx(transcript, {
        wantsChat: true,
        wantsFile: true,
        claimsChat: true,
      }),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("[attachment:00000000-0000-4000-8000-000000000000:report.md]");
    }
  });

  it("continues when native FileDeliver returned marker is included in the final answer", async () => {
    const marker = "[attachment:00000000-0000-4000-8000-000000000000:report.md]";
    const transcript: TranscriptEntry[] = [
      ...successfulDocumentWrite("report.md"),
      ...successfulFileDeliver([
        { target: "chat", externalId: "00000000-0000-4000-8000-000000000000", marker },
        { target: "kb", externalId: "artifacts/report.md" },
      ]),
    ];
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args(`파일을 생성하고 전달했습니다.\n${marker}`, "파일 KB에 저장하고 여기 채팅에도 첨부해줘"),
      makeCtx(transcript, {
        wantsChat: true,
        wantsKb: true,
        wantsFile: true,
        claimsChat: true,
        claimsKb: true,
        claimsFileCreated: true,
      }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("continues when native FileDeliver sent directly to Telegram without a marker", async () => {
    const transcript: TranscriptEntry[] = [
      ...successfulDocumentWrite("report.md"),
      ...successfulFileDeliver([
        { target: "chat", externalId: "telegram:1234" },
      ]),
    ];
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 텔레그램 채팅에 전달했습니다.", "파일 여기 채팅에도 첨부해줘"),
      makeCtx(transcript, {
        wantsChat: true,
        wantsFile: true,
        claimsChat: true,
        claimsFileCreated: true,
      }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks direct file delivery claims unless a delivery tool succeeded", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("요청하신 report.md 파일을 전달했습니다.", "report.md 파일 보내줘"),
      makeCtx([], {
        wantsChat: true,
        wantsFile: true,
        claimsChat: true,
      }),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("FileSend");
    }
  });

  it("continues direct native file delivery claims with same-turn FileSend evidence", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("요청하신 report.md 파일을 전달했습니다.", "report.md 파일 보내줘"),
      makeCtx(successfulFileSend(), {
        wantsChat: true,
        wantsFile: true,
        claimsChat: true,
      }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks web FileSend delivery claims unless the returned attachment marker is in the final answer", async () => {
    const marker = "[attachment:00000000-0000-4000-8000-000000000000:report.md]";
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("요청하신 report.md 파일을 전달했습니다.", "report.md 파일 보내줘"),
      makeCtx(successfulWebFileSend(marker), {
        wantsChat: true,
        wantsFile: true,
        claimsChat: true,
      }),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain(marker);
    }
  });

  it("continues web FileSend delivery claims when the returned attachment marker is in the final answer", async () => {
    const marker = "[attachment:00000000-0000-4000-8000-000000000000:report.md]";
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args(`요청하신 report.md 파일을 전달했습니다.\n${marker}`, "report.md 파일 보내줘"),
      makeCtx(successfulWebFileSend(marker), {
        wantsChat: true,
        wantsFile: true,
        claimsChat: true,
      }),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks native tool completion claims when no matching tool actually ran", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("`DocumentWrite`로 파일 생성 완료, `FileDeliver(target=\"both\")`로 전달 완료.", "파일 만들어서 KB와 채팅에 전달해줘", 2),
      makeCtx([], {
        wantsChat: true,
        wantsKb: true,
        wantsFile: true,
        claimsChat: true,
        claimsKb: true,
        claimsFileCreated: true,
      }),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("DocumentWrite");
      expect(result.reason).toContain("FileDeliver");
    }
  });

  it("requires both KB write and attachment evidence when both were requested", async () => {
    const transcript: TranscriptEntry[] = [
      ...successfulFileWrite("workspace/duol-debate/debate-verdict.md"),
      {
        kind: "tool_call",
        ts: 3,
        turnId: "turn-test",
        toolUseId: "tool-2",
        name: "Bash",
        input: {
          command:
            "cat workspace/duol-debate/debate-verdict.md | kb-write.sh --add 'Reports' 'debate-verdict.md' --stdin",
        },
      },
      {
        kind: "tool_result",
        ts: 4,
        turnId: "turn-test",
        toolUseId: "tool-2",
        status: "ok",
        output: '{"ok":true}',
      },
    ];
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 생성하고 KB에 저장했습니다.", "파일 KB에 저장하고 여기 채팅에도 첨부해줘"),
      makeCtx(transcript, {
        wantsChat: true,
        wantsKb: true,
        wantsFile: true,
        claimsKb: true,
        claimsFileCreated: true,
      }),
    );
    expect(result?.action).toBe("block");
  });

  it("does not treat KB write evidence as chat delivery evidence", async () => {
    const transcript: TranscriptEntry[] = [
      {
        kind: "tool_call",
        ts: 1,
        turnId: "turn-test",
        toolUseId: "tool-1",
        name: "Bash",
        input: {
          command: "cat report.md | kb-write.sh --add Reports report.md --stdin",
        },
      },
      {
        kind: "tool_result",
        ts: 2,
        turnId: "turn-test",
        toolUseId: "tool-1",
        status: "ok",
        output: '{"ok":true}',
      },
    ];
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("report.md를 채팅에 전달했습니다.", "report.md 보내줘"),
      makeCtx(transcript, {
        wantsChat: true,
        wantsFile: true,
        claimsChat: true,
      }),
    );
    expect(result?.action).toBe("block");
    if (result?.action === "block") {
      expect(result.reason).toContain("no successful chat delivery evidence");
    }
  });

  it("fails open after one retry to avoid infinite loops", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 생성했습니다.", "파일 첨부해줘", 1),
      makeCtx(successfulFileWrite("workspace/duol-debate/debate-verdict.md"), {
        wantsChat: true,
        wantsFile: true,
        claimsFileCreated: true,
      }),
    );
    expect(result).toEqual({ action: "continue" });
  });
});
