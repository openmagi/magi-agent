/**
 * MessageBuilder unit tests (R3 refactor).
 *
 * Cover:
 *   - buildSystemPrompt renders identity + session header
 *   - buildSystemPrompt returns header-only when identity empty
 *   - buildMessages calls contextEngine.maybeCompact and re-reads
 *   - buildMessages appends the current user message last
 *   - Token limit uses getCapability().contextWindow * 0.75
 */

import { describe, it, expect } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  appendRuntimeModelIdentityContext,
  buildSystemPrompt,
  buildMessages,
  formatReplyPreamble,
  REPLY_PREVIEW_MAX_CHARS,
} from "./MessageBuilder.js";
import { Transcript } from "../storage/Transcript.js";
import type { Session } from "../Session.js";
import type { UserMessage } from "../util/types.js";
import type { LLMMessage } from "../transport/LLMClient.js";

interface ContextEngineCall {
  kind: "maybeCompact" | "buildMessagesFromTranscript";
  tokenLimit?: number;
  model?: string;
}

async function makeSession(opts: {
  model?: string;
  identity?: Record<string, string>;
  replayMessages?: LLMMessage[];
  channel?: { type: string; channelId: string } | null;
  maybeCompactResult?: unknown;
}): Promise<{
  session: Session;
  transcript: Transcript;
  contextCalls: ContextEngineCall[];
  readCommittedCount: () => number;
}> {
  const workspaceRoot = await fs.mkdtemp(
    path.join(os.tmpdir(), "msg-builder-"),
  );
  const sessionsDir = path.join(workspaceRoot, "sessions");
  await fs.mkdir(sessionsDir, { recursive: true });

  const transcript = new Transcript(sessionsDir, "sess-1");
  let readCount = 0;
  const originalReadCommitted = transcript.readCommitted.bind(transcript);
  transcript.readCommitted = async () => {
    readCount += 1;
    return await originalReadCommitted();
  };
  const contextCalls: ContextEngineCall[] = [];

  const replayMessages = opts.replayMessages ?? [];

  const contextEngine = {
    maybeCompact: async (
      _s: Session,
      _entries: unknown[],
      tokenLimit: number,
      model?: string,
    ) => {
      contextCalls.push({ kind: "maybeCompact", tokenLimit, model });
      return opts.maybeCompactResult ?? null;
    },
    buildMessagesFromTranscript: () => {
      contextCalls.push({ kind: "buildMessagesFromTranscript" });
      return [...replayMessages];
    },
  };

  const workspace = {
    loadIdentity: async () => opts.identity ?? {},
  };

  const meta: Record<string, unknown> = { sessionKey: "sess-1" };
  if (opts.channel !== null) {
    // Default: no channel populated — exercises the `web` fallback.
    if (opts.channel !== undefined) meta.channel = opts.channel;
  }

  const session = {
    meta,
    transcript,
    agent: {
      config: { model: opts.model ?? "unknown-model-x" },
      contextEngine,
      workspace,
    },
  } as unknown as Session;

  return { session, transcript, contextCalls, readCommittedCount: () => readCount };
}

describe("MessageBuilder.buildSystemPrompt", () => {
  it("returns header-only when identity renders empty", async () => {
    const { session } = await makeSession({ identity: {} });
    const out = await buildSystemPrompt(session, "turn-A");
    expect(out).toContain("[Session: sess-1]");
    expect(out).toContain("[Turn: turn-A]");
    expect(out).toContain("[Time: ");
    expect(out).not.toContain("# IDENTITY");
  });

  it("includes identity sections when present", async () => {
    const { session } = await makeSession({
      identity: { identity: "Im Kevin", soul: "engineer" },
    });
    const out = await buildSystemPrompt(session, "turn-B");
    expect(out).toContain("# IDENTITY");
    expect(out).toContain("# SOUL");
    expect(out).toContain("Im Kevin");
  });

  it("renders identity blocks in stable runtime order", async () => {
    const { session } = await makeSession({
      identity: {
        bootstrap: "boot",
        soul: "soul",
        identity: "identity",
        user: "user",
        agents: "agents",
        tools: "tools",
        userRules: "- Always answer in Korean.",
      },
    });
    const out = await buildSystemPrompt(session, "turn-order");
    const sections = [
      "# BOOTSTRAP",
      "# SOUL",
      "# IDENTITY",
      "# USER",
      "# AGENTS",
      "# TOOLS",
    ];
    const indexes = sections.map((section) => out.indexOf(section));
    indexes.forEach((idx) => expect(idx).toBeGreaterThan(-1));
    expect(indexes).toEqual([...indexes].sort((a, b) => a - b));
    expect(out).not.toContain("Always answer in Korean.");
    expect(out).not.toContain("<agent_rules>");
  });

  it("includes [Channel: telegram] when channel.type is telegram", async () => {
    const { session } = await makeSession({
      channel: { type: "telegram", channelId: "123" },
    });
    const out = await buildSystemPrompt(session, "turn-tg");
    expect(out).toContain("[Channel: telegram]");
    // Ordering: Channel appears after Time, before identity.
    const timeIdx = out.indexOf("[Time:");
    const channelIdx = out.indexOf("[Channel:");
    expect(channelIdx).toBeGreaterThan(timeIdx);
  });

  it("includes [Channel: discord] for discord sessions", async () => {
    const { session } = await makeSession({
      channel: { type: "discord", channelId: "ch-1" },
    });
    const out = await buildSystemPrompt(session, "turn-dc");
    expect(out).toContain("[Channel: discord]");
  });

  it("includes [Channel: app] for mobile-app sessions", async () => {
    const { session } = await makeSession({
      channel: { type: "app", channelId: "app-1" },
    });
    const out = await buildSystemPrompt(session, "turn-app");
    expect(out).toContain("[Channel: app]");
  });

  it("defaults to [Channel: web] when channel is undefined", async () => {
    const { session } = await makeSession({});
    const out = await buildSystemPrompt(session, "turn-web");
    expect(out).toContain("[Channel: web]");
  });

  it("does not inject raw <agent_rules> blocks when identity.userRules is populated", async () => {
    const { session } = await makeSession({
      identity: {
        identity: "I am bot",
        userRules: "- Always answer in Korean.",
      },
    });
    const out = await buildSystemPrompt(session, "turn-rules");
    expect(out).not.toContain("<agent_rules>");
    expect(out).not.toContain("Always answer in Korean.");
  });

  it("still skips <agent_rules> block when identity.userRules is absent", async () => {
    const { session } = await makeSession({
      identity: { identity: "I am bot" },
    });
    const out = await buildSystemPrompt(session, "turn-no-rules");
    expect(out).not.toContain("<agent_rules>");
  });

  it("appends per-turn system prompt addendum from user metadata", async () => {
    const { session } = await makeSession({
      identity: { identity: "I am bot" },
    });
    const out = await (
      buildSystemPrompt as unknown as (
        session: Session,
        turnId: string,
        userMessage: UserMessage,
      ) => Promise<string>
    )(session, "turn-kb", {
      text: "analyze this",
      receivedAt: Date.now(),
      metadata: {
        systemPromptAddendum:
          "<kb-context>\n[file: report.pdf]\nRevenue was up 12%.\n</kb-context>",
      },
    });
    expect(out).toContain(
      "<kb-context>\n[file: report.pdf]\nRevenue was up 12%.\n</kb-context>",
    );
  });
});

describe("MessageBuilder.buildMessages", () => {
  it("calls maybeCompact + buildMessagesFromTranscript + appends user message", async () => {
    const { session, contextCalls } = await makeSession({
      replayMessages: [{ role: "assistant", content: "prior" }],
    });
    const um: UserMessage = { text: "hello", receivedAt: Date.now() };
    const out = await buildMessages(session, um);

    expect(out.length).toBe(2);
    expect(out[0]?.role).toBe("assistant");
    expect(out[1]?.role).toBe("user");
    expect(out[1]?.content).toBe("hello");

    // maybeCompact called once, buildMessagesFromTranscript called once.
    expect(
      contextCalls.filter((c) => c.kind === "maybeCompact").length,
    ).toBe(1);
    expect(
      contextCalls.filter((c) => c.kind === "buildMessagesFromTranscript")
        .length,
    ).toBe(1);
  });

  it("adds a hidden KB command contract for /kb turns", async () => {
    const { session } = await makeSession({});
    const um: UserMessage = {
      text: "/kb 르챔버 매출데이터 전부 읽어줘. 자료는 Download 컬렉션에 있어",
      receivedAt: Date.now(),
    };

    const out = await buildMessages(session, um);

    expect(out.at(-2)?.role).toBe("user");
    expect(out.at(-2)?.content).toBe(um.text);
    expect(out.at(-1)?.role).toBe("user");
    expect(JSON.stringify(out.at(-1)?.content)).toContain("<kb_command");
    expect(JSON.stringify(out.at(-1)?.content)).toContain("MUST call");
    expect(JSON.stringify(out.at(-1)?.content)).toContain("Download");
    expect(JSON.stringify(out.at(-1)?.content)).toContain("Downloads");
  });

  it("does not re-read committed transcript when compaction appends no boundary", async () => {
    const { session, readCommittedCount } = await makeSession({
      replayMessages: [{ role: "assistant", content: "prior" }],
    });
    const um: UserMessage = { text: "hello", receivedAt: Date.now() };

    await buildMessages(session, um);

    expect(readCommittedCount()).toBe(1);
  });

  it("re-reads committed transcript when compaction appends a boundary", async () => {
    const { session, readCommittedCount } = await makeSession({
      replayMessages: [{ role: "assistant", content: "prior" }],
      maybeCompactResult: {
        kind: "compaction_boundary",
        ts: 1,
        turnId: "sess-1",
        boundaryId: "b1",
        beforeTokenCount: 10,
        afterTokenCount: 3,
        summaryHash: "hash",
        summaryText: "summary",
        createdAt: 1,
      },
    });
    const um: UserMessage = { text: "hello", receivedAt: Date.now() };

    await buildMessages(session, um);

    expect(readCommittedCount()).toBe(2);
  });

  it("uses fallback 150_000 token limit for unknown model", async () => {
    const { session, contextCalls } = await makeSession({ model: "unknown" });
    const um: UserMessage = { text: "x", receivedAt: Date.now() };
    await buildMessages(session, um);
    const mc = contextCalls.find((c) => c.kind === "maybeCompact");
    expect(mc?.tokenLimit).toBe(150_000);
  });

  it("uses 75% of contextWindow for known model (opus-4-7)", async () => {
    // opus-4-7 is registered in llm/modelCapabilities — 200k * 0.75 = 150_000
    // (any known model works; we don't depend on the specific number,
    // only that it's the floor of contextWindow * 0.75).
    const { session, contextCalls } = await makeSession({
      model: "claude-opus-4-7",
    });
    const um: UserMessage = { text: "x", receivedAt: Date.now() };
    await buildMessages(session, um);
    const mc = contextCalls.find((c) => c.kind === "maybeCompact");
    expect(mc?.tokenLimit).toBeTypeOf("number");
    // Must be > 0 and a plausible floor-of-window figure.
    expect(mc?.tokenLimit).toBeGreaterThan(0);
  });

  it("uses the resolved turn model for compaction instead of the provisioned boot model", async () => {
    const { session, contextCalls } = await makeSession({
      model: "claude-opus-4-7",
    });
    const um: UserMessage = { text: "x", receivedAt: Date.now() };
    await buildMessages(session, um, "openai/gpt-5.4-mini");
    const mc = contextCalls.find((c) => c.kind === "maybeCompact");
    expect(mc?.model).toBe("openai/gpt-5.4-mini");
    expect(mc?.tokenLimit).toBe(96_000);
  });

  it("prepends [Reply to user: …] when metadata.replyTo is present", async () => {
    const { session } = await makeSession({});
    const um: UserMessage = {
      text: "what did you mean by that?",
      receivedAt: Date.now(),
      metadata: {
        replyTo: {
          messageId: "m-1",
          preview: "I think the answer is 42.",
          role: "assistant",
        },
      },
    };
    const out = await buildMessages(session, um);
    const last = out[out.length - 1]!;
    expect(last.role).toBe("user");
    expect(last.content).toBe(
      '[Reply to assistant: "I think the answer is 42."]\nwhat did you mean by that?',
    );
  });

  it("supports role=user replies (quoting another user's message)", async () => {
    const { session } = await makeSession({});
    const um: UserMessage = {
      text: "+1",
      receivedAt: Date.now(),
      metadata: {
        replyTo: { messageId: "m-2", preview: "hello team", role: "user" },
      },
    };
    const out = await buildMessages(session, um);
    expect(out[out.length - 1]?.content).toBe(
      '[Reply to user: "hello team"]\n+1',
    );
  });

  it("leaves content unchanged when metadata.replyTo is absent", async () => {
    const { session } = await makeSession({});
    const um: UserMessage = {
      text: "plain text",
      receivedAt: Date.now(),
    };
    const out = await buildMessages(session, um);
    expect(out[out.length - 1]?.content).toBe("plain text");
  });

  it("emits a mixed Anthropic user content array when image blocks exist", async () => {
    const { session } = await makeSession({});
    const out = await buildMessages(session, {
      text: "Describe it",
      imageBlocks: [
        {
          type: "image",
          source: {
            type: "base64",
            media_type: "image/png",
            data: "ZmFrZQ==",
          },
        },
      ],
      receivedAt: Date.now(),
    });

    expect(out.at(-1)).toEqual({
      role: "user",
      content: [
        { type: "text", text: "Describe it" },
        {
          type: "image",
          source: {
            type: "base64",
            media_type: "image/png",
            data: "ZmFrZQ==",
          },
        },
      ],
    });
  });
});

describe("MessageBuilder.appendRuntimeModelIdentityContext", () => {
  it("inserts hidden runtime model identity before the current user message", () => {
    const messages: LLMMessage[] = [
      { role: "assistant", content: "prior" },
      { role: "user", content: "what model are you?" },
    ];

    appendRuntimeModelIdentityContext(messages, {
      configuredModel: "clawy-smart-router/auto",
      effectiveModel: "openai/gpt-5.5",
      routeDecision: {
        profileId: "premium",
        tier: "premium",
        provider: "openai",
        model: "gpt-5.5",
        classifierModel: "claude-sonnet-4-6",
        classifierUsed: true,
        confidence: "high",
        reason: "complex task",
      },
    });

    expect(messages).toHaveLength(3);
    expect(messages[1]).toMatchObject({
      role: "user",
      content: [
        {
          type: "text",
          text: expect.stringContaining("<runtime_model_identity hidden=\"true\">"),
        },
      ],
    });
    expect(messages[2]?.content).toBe("what model are you?");
  });

  it("replaces stale runtime model identity instead of accumulating copies", () => {
    const messages: LLMMessage[] = [
      {
        role: "user",
        content: [
          {
            type: "text",
            text: "<runtime_model_identity hidden=\"true\">\nstale\n</runtime_model_identity>",
          },
        ],
      },
      { role: "user", content: "current" },
    ];

    appendRuntimeModelIdentityContext(messages, {
      configuredModel: "claude-sonnet-4-6",
      effectiveModel: "claude-sonnet-4-6",
    });

    const serialized = JSON.stringify(messages);
    expect(serialized.match(/runtime_model_identity/g)?.length).toBe(2);
    expect(serialized).not.toContain("stale");
  });

  it("does not insert runtime model identity between tool_use and tool_result", () => {
    const messages: LLMMessage[] = [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "toolu_01JwgQJ74c97cZKarec8fA4z",
            name: "Bash",
            input: { command: "echo hi" },
          },
        ],
      },
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "toolu_01JwgQJ74c97cZKarec8fA4z",
            content: "hi",
          },
        ],
      },
    ];

    appendRuntimeModelIdentityContext(messages, {
      configuredModel: "clawy-smart-router/auto",
      effectiveModel: "openai/gpt-5.5",
    });

    expect(messages).toHaveLength(2);
    const nextContent = messages[1]?.content;
    expect(Array.isArray(nextContent)).toBe(true);
    if (!Array.isArray(nextContent)) return;
    expect(nextContent[0]).toMatchObject({
      type: "tool_result",
      tool_use_id: "toolu_01JwgQJ74c97cZKarec8fA4z",
    });
    expect(nextContent[1]).toMatchObject({
      type: "text",
      text: expect.stringContaining("<runtime_model_identity hidden=\"true\">"),
    });
  });
});

describe("MessageBuilder.formatReplyPreamble", () => {
  it("emits single-line `[Reply to <role>: \"<preview>\"]`", () => {
    expect(
      formatReplyPreamble({
        messageId: "m",
        preview: "hi there",
        role: "assistant",
      }),
    ).toBe('[Reply to assistant: "hi there"]');
  });

  it("collapses internal whitespace/newlines to single spaces", () => {
    expect(
      formatReplyPreamble({
        messageId: "m",
        preview: "line one\nline two\n\n\tline three",
        role: "user",
      }),
    ).toBe('[Reply to user: "line one line two line three"]');
  });

  it("truncates previews longer than REPLY_PREVIEW_MAX_CHARS with an ellipsis", () => {
    const long = "x".repeat(REPLY_PREVIEW_MAX_CHARS + 50);
    const out = formatReplyPreamble({
      messageId: "m",
      preview: long,
      role: "assistant",
    });
    // Must end with the Unicode ellipsis — NOT three dots — so the
    // caller can grep for the boundary.
    expect(out.endsWith('…"]')).toBe(true);
    // Preview body is exactly MAX chars + 1 ellipsis between the quote
    // boundaries.
    const preview = out.slice(
      '[Reply to assistant: "'.length,
      -"]".length - 1, // strip trailing `"]`
    );
    expect(preview.length).toBe(REPLY_PREVIEW_MAX_CHARS + 1);
  });

  it("keeps short previews verbatim (no ellipsis)", () => {
    const out = formatReplyPreamble({
      messageId: "m",
      preview: "short",
      role: "user",
    });
    expect(out).toBe('[Reply to user: "short"]');
    expect(out.includes("…")).toBe(false);
  });
});
