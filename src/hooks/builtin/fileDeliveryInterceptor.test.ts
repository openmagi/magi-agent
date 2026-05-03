import { describe, it, expect, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileDeliveryInterceptor } from "./fileDeliveryInterceptor.js";
import type { HookContext } from "../types.js";
import type { LLMClient, LLMEvent, LLMStreamRequest } from "../../transport/LLMClient.js";

class ClassifierLlm implements LLMClient {
  async *stream(_req: LLMStreamRequest): AsyncGenerator<LLMEvent, void, void> {
    yield {
      kind: "text_delta",
      blockIndex: 0,
      delta: JSON.stringify({
        turnMode: { label: "other", confidence: 1 },
        skipTdd: false,
        implementationIntent: false,
        documentOrFileOperation: true,
        deterministic: {
          requiresDeterministic: false,
          kinds: [],
          reason: "",
          suggestedTools: [],
          acceptanceCriteria: [],
        },
        fileDelivery: {
          intent: "deliver_existing",
          path: "report.pdf",
          wantsChatDelivery: true,
          wantsKbDelivery: false,
          wantsFileOutput: true,
        },
      }),
    };
    yield {
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 1, outputTokens: 1 },
    };
  }
}

function makeHookCtx(overrides: Partial<HookContext> = {}): HookContext {
  return {
    botId: "bot-1",
    userId: "user-1",
    sessionKey: "s-1",
    turnId: "t-1",
    llm: new ClassifierLlm(),
    transcript: [],
    emit: () => {},
    log: () => {},
    agentModel: "claude-haiku",
    abortSignal: new AbortController().signal,
    deadlineMs: 5_000,
    ...overrides,
  };
}

describe("fileDeliveryInterceptor", () => {
  it("creates a registered hook with correct metadata", () => {
    const hook = fileDeliveryInterceptor({
      workspaceRoot: "/home/clawy/.clawy/workspace",
    });

    expect(hook.name).toBe("builtin:file-delivery-interceptor");
    expect(hook.point).toBe("beforeLLMCall");
    expect(hook.priority).toBe(1);
    expect(hook.blocking).toBe(true);
    expect(typeof hook.handler).toBe("function");
  });

  it("skips non-zero iterations", async () => {
    const hook = fileDeliveryInterceptor({
      workspaceRoot: "/home/clawy/.clawy/workspace",
    });

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "send report.pdf" }],
        tools: [],
        system: "",
        iteration: 1,
      } as never,
      {} as never,
    );

    expect(result).toBeUndefined();
  });

  it("skips messages without file extensions", async () => {
    const hook = fileDeliveryInterceptor({
      workspaceRoot: "/home/clawy/.clawy/workspace",
    });

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "안녕하세요 보내줘" }],
        tools: [],
        system: "",
        iteration: 0,
      } as never,
      {} as never,
    );

    expect(result).toBeUndefined();
  });

  it("skips messages longer than 500 chars", async () => {
    const hook = fileDeliveryInterceptor({
      workspaceRoot: "/home/clawy/.clawy/workspace",
    });

    const result = await hook.handler(
      {
        messages: [{ role: "user", content: "a".repeat(501) + " report.pdf 보내줘" }],
        tools: [],
        system: "",
        iteration: 0,
      } as never,
      {} as never,
    );

    expect(result).toBeUndefined();
  });

  it("delivers an existing file to the current Telegram source channel before calling the main model", async () => {
    const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), "file-delivery-hook-"));
    try {
      await fs.writeFile(path.join(workspaceRoot, "report.pdf"), "PDF");
      const sendFile = vi.fn(async () => {});
      const emitted: string[] = [];
      const hook = fileDeliveryInterceptor({
        workspaceRoot,
        getSourceChannel: () => ({ type: "telegram", channelId: "777" }),
        sendFile,
      });

      const result = await hook.handler(
        {
          messages: [{ role: "user", content: "report.pdf 텔레그램에 첨부해서 보내줘" }],
          tools: [],
          system: "base system",
          iteration: 0,
        } as never,
        makeHookCtx({
          emit: (event) => {
            if (event.type === "text_delta") emitted.push(event.delta);
          },
        }) as never,
      );

      expect(sendFile).toHaveBeenCalledWith(
        { type: "telegram", channelId: "777" },
        path.join(workspaceRoot, "report.pdf"),
        "report.pdf",
        "document",
      );
      expect(result?.action).toBe("replace");
      expect(result?.value.system).toContain("File delivery already completed");
      expect(emitted.join("")).toContain("sent to Telegram chat");
    } finally {
      await fs.rm(workspaceRoot, { recursive: true, force: true });
    }
  });
});
