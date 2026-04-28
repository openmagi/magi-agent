import { describe, it, expect, beforeEach, afterEach } from "vitest";
import type { HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import {
  collectCreatedArtifacts,
  hasArtifactDeliveryEvidence,
  makeArtifactDeliveryGateHook,
} from "./artifactDeliveryGate.js";

function makeCtx(transcript: TranscriptEntry[] = []): HookContext {
  return {
    botId: "bot-test",
    userId: "user-test",
    sessionKey: "session-test",
    turnId: "turn-test",
    llm: {} as HookContext["llm"],
    transcript,
    emit: () => {},
    log: () => {},
    agentModel: "test-model",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
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
    const artifacts = collectCreatedArtifacts(successfulFileWrite("memory/daily/2026-04-23.md"), "turn-test");
    expect(artifacts).toEqual([]);
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
      makeCtx(successfulFileWrite("workspace/duol-debate/debate-verdict.md")),
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
      makeCtx(successfulFileWrite("workspace/duol-debate/debate-verdict.md")),
    );
    expect(result).toEqual({ action: "continue" });
  });

  it("blocks KB save requests unless there is KB write or attachment evidence", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 생성했습니다.", "이 리포트 KB에 저장해줘"),
      makeCtx(successfulFileWrite("workspace/duol-debate/debate-verdict.md")),
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
      makeCtx(transcript),
    );
    expect(result).toEqual({ action: "continue" });
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
      makeCtx(transcript),
    );
    expect(result?.action).toBe("block");
  });

  it("fails open after one retry to avoid infinite loops", async () => {
    const hook = makeArtifactDeliveryGateHook();
    const result = await hook.handler(
      args("파일을 생성했습니다.", "파일 첨부해줘", 1),
      makeCtx(successfulFileWrite("workspace/duol-debate/debate-verdict.md")),
    );
    expect(result).toEqual({ action: "continue" });
  });
});
