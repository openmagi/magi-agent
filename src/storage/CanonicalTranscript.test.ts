import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Transcript, type TranscriptEntry } from "./Transcript.js";
import { ContextEngine } from "../services/compact/ContextEngine.js";
import { ControlEventLedger } from "../control/ControlEventLedger.js";
import { ControlRequestStore } from "../control/ControlRequestStore.js";
import { projectControlEvents } from "../control/ControlProjection.js";
import type {
  LLMClient,
  LLMEvent,
  LLMStreamRequest,
} from "../transport/LLMClient.js";
import type { Session } from "../Session.js";

const tempRoots: string[] = [];

afterEach(async () => {
  await Promise.all(
    tempRoots.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })),
  );
});

async function tempDir(prefix: string): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), prefix));
  tempRoots.push(dir);
  return dir;
}

function llmReturningSummary(summary: string): LLMClient {
  async function* stream(
    _req: LLMStreamRequest,
  ): AsyncGenerator<LLMEvent, void, void> {
    yield { kind: "text_delta", blockIndex: 0, delta: summary };
    yield {
      kind: "message_end",
      stopReason: "end_turn",
      usage: { inputTokens: 1, outputTokens: 1 },
    };
  }
  return { stream } as unknown as LLMClient;
}

function userEntry(turnId: string, text: string, ts: number): TranscriptEntry {
  return { kind: "user_message", turnId, ts, text };
}

function assistantEntry(
  turnId: string,
  text: string,
  ts: number,
): TranscriptEntry {
  return { kind: "assistant_text", turnId, ts, text };
}

function toolCallEntry(
  turnId: string,
  toolUseId: string,
  name: string,
  input: unknown,
  ts: number,
): TranscriptEntry {
  return { kind: "tool_call", turnId, ts, toolUseId, name, input };
}

function toolResultEntry(
  turnId: string,
  toolUseId: string,
  output: string,
  ts: number,
): TranscriptEntry {
  return { kind: "tool_result", turnId, ts, toolUseId, status: "ok", output };
}

function committedEntry(turnId: string, ts: number): TranscriptEntry {
  return {
    kind: "turn_committed",
    turnId,
    ts,
    inputTokens: 10,
    outputTokens: 5,
  };
}

describe("canonical transcript replay", () => {
  it("keeps canonical and control entries after a malformed JSONL tail", async () => {
    const root = await tempDir("canonical-transcript-");
    const transcript = new Transcript(root, "agent:main:test:canonical");

    await transcript.append(userEntry("turn-1", "hello", 1));
    await transcript.append({
      kind: "canonical_message",
      ts: 2,
      turnId: "turn-1",
      messageId: "cm-1",
      role: "assistant",
      content: [{ type: "text", text: "canonical assistant state" }],
    });
    await fs.appendFile(
      transcript.filePath,
      [
        "{\"kind\":\"partial\"",
        JSON.stringify({
          kind: "control_event",
          ts: 3,
          turnId: "turn-1",
          seq: 1,
          eventId: "ce-1",
          eventType: "task_board_snapshot",
        } satisfies TranscriptEntry),
        "",
      ].join("\n"),
      "utf8",
    );

    const entries = await transcript.readAll();
    expect(entries.map((entry) => entry.kind)).toEqual([
      "user_message",
      "canonical_message",
      "control_event",
    ]);
  });

  it("includes structural canonical and compaction entries appended after the last committed turn", async () => {
    const root = await tempDir("canonical-committed-");
    const transcript = new Transcript(root, "agent:main:test:structural-tail");

    await transcript.append(userEntry("turn-1", "before compact", 1));
    await transcript.append(committedEntry("turn-1", 2));
    await transcript.append({
      kind: "canonical_message",
      ts: 3,
      turnId: "agent:main:test:structural-tail",
      messageId: "cm-boundary",
      role: "system",
      content: [{ type: "text", text: "boundary canonical state" }],
    });
    await transcript.append({
      kind: "compaction_boundary",
      ts: 4,
      turnId: "agent:main:test:structural-tail",
      boundaryId: "01STRUCTURAL",
      beforeTokenCount: 10_000,
      afterTokenCount: 10,
      summaryHash: "hash",
      summaryText: "summary survives committed read",
      createdAt: 4,
    });
    await transcript.append({
      kind: "control_event",
      ts: 5,
      seq: 1,
      eventId: "ce-1",
      eventType: "compaction_boundary",
    });

    const committed = await transcript.readCommitted();
    expect(committed.map((entry) => entry.kind)).toEqual([
      "user_message",
      "turn_committed",
      "canonical_message",
      "compaction_boundary",
      "control_event",
    ]);
  });

  it("replays multi-tool turns with assistant tool_use before user tool_results", () => {
    const engine = new ContextEngine(llmReturningSummary("unused"));
    const entries: TranscriptEntry[] = [
      userEntry("turn-1", "collect repo facts", 1),
      assistantEntry("turn-1", "I will inspect both files.", 2),
      toolCallEntry("turn-1", "tu-a", "FileRead", { path: "a.ts" }, 3),
      toolCallEntry("turn-1", "tu-b", "FileRead", { path: "b.ts" }, 4),
      toolResultEntry("turn-1", "tu-a", "a contents", 5),
      toolResultEntry("turn-1", "tu-b", "b contents", 6),
      assistantEntry("turn-1", "Both files are available.", 7),
      committedEntry("turn-1", 8),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    expect(messages.map((message) => message.role)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    const toolUseBlocks = messages[1]!.content as Array<{ type: string; id?: string }>;
    expect(toolUseBlocks.map((block) => block.type)).toEqual([
      "text",
      "tool_use",
      "tool_use",
    ]);
    const resultBlocks = messages[2]!.content as Array<{
      type: string;
      tool_use_id?: string;
    }>;
    expect(resultBlocks.map((block) => block.tool_use_id)).toEqual(["tu-a", "tu-b"]);
  });

  it("drops historical thinking blocks and replays tool_use blocks in assistant order", () => {
    const engine = new ContextEngine(llmReturningSummary("unused"));
    const entries: TranscriptEntry[] = [
      userEntry("turn-1", "run a command", 1),
      {
        kind: "canonical_message",
        ts: 2,
        turnId: "turn-1",
        messageId: "cm-thinking",
        role: "assistant",
        content: [
          { type: "thinking", thinking: "need shell state", signature: "sig-1" },
          { type: "tool_use", id: "tu-shell", name: "Bash", input: { cmd: "pwd" } },
        ],
      },
      toolResultEntry("turn-1", "tu-shell", "/workspace", 3),
      assistantEntry("turn-1", "The workspace is /workspace.", 4),
      committedEntry("turn-1", 5),
    ];

    const messages = engine.buildMessagesFromTranscript(entries);
    expect(messages.map((message) => message.role)).toEqual([
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    const assistantBlocks = messages[1]!.content as Array<{ type: string }>;
    expect(assistantBlocks.map((block) => block.type)).toEqual(["tool_use"]);
  });

  it("resumes an aborted turn after streamed assistant text", async () => {
    const root = await tempDir("canonical-aborted-");
    const transcript = new Transcript(root, "agent:main:test:aborted");

    await transcript.append(userEntry("turn-1", "debug this", 1));
    await transcript.append(assistantEntry("turn-1", "I found the issue.", 2));
    await transcript.append({
      kind: "turn_aborted",
      turnId: "turn-1",
      ts: 3,
      reason: "beforeCommit blocked",
    });

    const committed = await transcript.readCommitted();
    const messages = new ContextEngine(llmReturningSummary("unused")).buildMessagesFromTranscript(
      committed,
    );
    expect(committed.map((entry) => entry.kind)).toEqual([
      "user_message",
      "assistant_text",
      "turn_aborted",
    ]);
    expect(messages.map((message) => message.role)).toEqual(["user", "assistant"]);
    expect(JSON.stringify(messages[1]!.content)).toContain("I found the issue.");
  });
});

describe("control state survives restart and reconnect", () => {
  it("projects a pending ControlRequest after process restart", async () => {
    const root = await tempDir("canonical-control-request-");
    const sessionKey = "agent:main:test:request-restart";
    const firstStore = new ControlRequestStore({
      ledger: new ControlEventLedger({ rootDir: root, sessionKey }),
    });
    const request = await firstStore.create({
      kind: "tool_permission",
      sessionKey,
      source: "turn",
      prompt: "Allow Bash?",
      expiresAt: Date.now() + 60_000,
    });

    const restartedStore = new ControlRequestStore({
      ledger: new ControlEventLedger({ rootDir: root, sessionKey }),
    });
    const pending = await restartedStore.pending();
    expect(pending.map((item) => item.requestId)).toEqual([request.requestId]);
  });

  it("keeps approved plan verification state and emits compaction control event", async () => {
    const root = await tempDir("canonical-plan-compaction-");
    const sessionKey = "agent:main:test:plan-compaction";
    const transcript = new Transcript(root, sessionKey);
    const controlEvents = new ControlEventLedger({
      rootDir: root,
      sessionKey,
      transcript,
    });
    await transcript.append(userEntry("turn-1", "x".repeat(20_000), 1));
    await transcript.append(committedEntry("turn-1", 2));
    await controlEvents.append({
      type: "plan_lifecycle",
      turnId: "turn-1",
      planId: "plan-1",
      state: "approved",
      plan: "do the work",
    });
    await controlEvents.append({
      type: "verification",
      turnId: "turn-1",
      status: "pending",
      reason: "approved plan requires verification",
    });
    await controlEvents.append({
      type: "plan_lifecycle",
      turnId: "turn-1",
      planId: "plan-1",
      state: "verification_pending",
      plan: "do the work",
    });

    const session = {
      meta: { sessionKey },
      transcript,
      controlEvents,
    } as unknown as Session;
    const engine = new ContextEngine(llmReturningSummary("compact plan summary"));
    const boundary = await engine.maybeCompact(
      session,
      await transcript.readCommitted(),
      100,
    );

    expect(boundary).not.toBeNull();
    const projection = projectControlEvents(await controlEvents.readAll());
    expect(projection.activePlan?.state).toBe("verification_pending");
    expect(projection.verification).toMatchObject({ status: "pending" });
    expect((await controlEvents.readAll()).some((event) => event.type === "compaction_boundary")).toBe(true);
  });

  it("replays task-board state after reconnect from the control ledger", async () => {
    const root = await tempDir("canonical-taskboard-");
    const sessionKey = "agent:main:test:taskboard";
    const ledger = new ControlEventLedger({ rootDir: root, sessionKey });
    await ledger.append({
      type: "task_board_snapshot",
      turnId: "turn-1",
      taskBoard: {
        tasks: [{ id: "task-1", title: "Verify", status: "completed" }],
      },
    });

    const replay = await ledger.readSince(0);
    const projection = projectControlEvents(replay);
    expect(replay).toHaveLength(1);
    expect(projection.taskBoard).toEqual({
      tasks: [{ id: "task-1", title: "Verify", status: "completed" }],
    });
  });
});
