/**
 * /v1/chat/completions + /v1/turns/:id/ask-response tests.
 *
 * Uses a stub agent whose `getOrCreateSession` returns a Session-like
 * object whose `runTurn` simply writes one SSE event + ends. Verifies
 * the route framing (200 + SSE text/event-stream) without running the
 * real Turn machinery.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { HttpServer } from "../HttpServer.js";
import { AuditLog } from "../../storage/AuditLog.js";
import { ResetCounterStore } from "../../slash/resetCounters.js";
import { ControlEventLedger } from "../../control/ControlEventLedger.js";
import { ControlRequestStore } from "../../control/ControlRequestStore.js";
import { extractLastUserMessage, extractReplyTo, extractRuntimeModelOverride } from "./turns.js";

interface SseLike {
  legacyDelta(_t: string): void;
  legacyFinish(): void;
  end(): void;
}

interface StubSession {
  runTurn(_userMsg: unknown, sse: SseLike, _opts: unknown): Promise<void>;
  getStructuredOutputContract?: () => unknown;
  setStructuredOutputContract?: (spec: unknown) => void;
}

interface StubSessionWithInject extends StubSession {
  injectMessage?: (
    text: string,
    source?: string,
  ) => { injectionId: string; queuedCount: number } | null;
  requestInterrupt?: (
    handoffRequested?: boolean,
    source?: string,
  ) => {
    status: "accepted" | "noop";
    handoffRequested: boolean;
  };
  controlRequests?: ControlRequestStore;
  controlEvents?: ControlEventLedger;
}

interface StubActiveTurn {
  resolveAsk(questionId: string, answer: { selectedId?: string; freeText?: string }): boolean;
}

interface StubAgent {
  config: { botId: string; workspaceRoot: string };
  auditLog: AuditLog;
  /** Slash-command reset-counter sidecar. Route handler reads it on
   * every POST to apply any `/reset` bump to the sessionKey. */
  resetCounters: ResetCounterStore;
  listSessions(): Array<{ meta: { sessionKey: string } }>;
  sessionKeyIndex(): Map<string, string>;
  tools: { list(): []; skillReport(): null };
  hooks: { list(): [] };
  getActiveTurn(turnId: string): StubActiveTurn | undefined;
  getSession?: (sessionKey: string) => StubSessionWithInject | undefined;
  hasActiveTurnForSession?: (sessionKey: string) => boolean;
  getOrCreateSession(
    _sessionKey: string,
    _channel: unknown,
  ): Promise<StubSession>;
}

interface InjectStubState {
  /** sessionKey → whether a turn is currently "active" for inject tests. */
  activeSessions: Set<string>;
  /** sessionKey → session stub instance used by getSession(). */
  sessions: Map<string, StubSessionWithInject>;
  /** turnId → active turn stub used by ask_user response tests. */
  activeTurns: Map<string, StubActiveTurn>;
}

function makeStubAgent(
  workspaceRoot: string,
  capture?: {
    userMessage?: unknown;
    runOptions?: unknown;
    structuredSpecs?: unknown[];
    interruptCalls?: Array<{ handoffRequested?: boolean; source?: string }>;
    runTurnDelayMs?: number;
  },
  injectState?: InjectStubState,
): StubAgent {
  const botId = "bot-test";
  return {
    config: { botId, workspaceRoot },
    auditLog: new AuditLog(workspaceRoot, botId),
    resetCounters: new ResetCounterStore(
      path.join(workspaceRoot, "core-agent", "sessions"),
    ),
    listSessions: () => [],
    sessionKeyIndex: () => new Map(),
    tools: { list: () => [], skillReport: () => null },
    hooks: { list: () => [] },
    getActiveTurn: (turnId) => injectState?.activeTurns.get(turnId),
    getSession: injectState
      ? (key) => injectState.sessions.get(key)
      : undefined,
    hasActiveTurnForSession: injectState
      ? (key) => injectState.activeSessions.has(key)
      : undefined,
    async getOrCreateSession(): Promise<StubSession> {
      return {
        getStructuredOutputContract: () => null,
        setStructuredOutputContract: (spec) => {
          capture?.structuredSpecs?.push(spec);
        },
        async runTurn(userMsg, sse, opts) {
          if (capture) capture.userMessage = userMsg;
          if (capture) capture.runOptions = opts;
          // Emit one legacy OpenAI-style delta so the SSE body contains
          // the expected `data:` framing; the route harness calls
          // sse.end() in its finally block.
          sse.legacyDelta("hi");
          if (capture?.runTurnDelayMs) {
            await new Promise((resolve) => setTimeout(resolve, capture.runTurnDelayMs));
          }
          sse.legacyFinish();
        },
        requestInterrupt(handoffRequested?: boolean, source?: string) {
          capture?.interruptCalls?.push({ handoffRequested, source });
          return { status: "accepted", handoffRequested: handoffRequested === true };
        },
      };
    },
  };
}

function rawRequest(
  method: string,
  url: string,
  headers: Record<string, string> = {},
  body?: string,
): Promise<{ status: number; contentType: string; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method, headers }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        resolve({
          status: res.statusCode ?? 0,
          contentType: String(res.headers["content-type"] ?? ""),
          body: Buffer.concat(chunks).toString("utf8"),
        });
      });
    });
    req.on("error", reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

describe("HttpServer /v1/chat/completions + /v1/turns/:id/ask-response", () => {
  let tmp: string;
  let server: HttpServer;
  let port: number;
  let capture: { userMessage?: unknown; runOptions?: unknown; structuredSpecs?: unknown[] };
  const TOKEN = "test-bearer-token";

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "core-agent-turns-"));
    capture = { structuredSpecs: [] };
    const agent = makeStubAgent(tmp, capture) as unknown as ConstructorParameters<
      typeof HttpServer
    >[0]["agent"];
    server = new HttpServer({ port: 0, agent, bearerToken: TOKEN });
    await server.start();
    const anyServer = server as unknown as { server: http.Server };
    const addr = anyServer.server.address();
    port = typeof addr === "object" && addr ? addr.port : 0;
  });

  afterEach(async () => {
    await server.stop();
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("POST /v1/chat/completions opens an SSE stream with stub turn", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({
        messages: [{ role: "user", content: "hello" }],
      }),
    );
    expect(r.status).toBe(200);
    expect(r.contentType).toContain("text/event-stream");
    // SseWriter should have produced at least one "data:" line.
    expect(r.body).toContain("data:");
  });

  it("interrupts the active turn when the SSE client disconnects mid-turn", async () => {
    capture.interruptCalls = [];
    capture.runTurnDelayMs = 50;

    await new Promise<void>((resolve, reject) => {
      const req = http.request(
        `http://127.0.0.1:${port}/v1/chat/completions`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${TOKEN}`,
            "Content-Type": "application/json",
          },
        },
        (res) => {
          res.once("data", () => {
            req.destroy();
          });
          res.once("close", () => {
            setTimeout(resolve, 10);
          });
        },
      );
      req.on("error", (err: NodeJS.ErrnoException) => {
        if (err.code === "ECONNRESET") return;
        reject(err);
      });
      req.write(JSON.stringify({ messages: [{ role: "user", content: "hello" }] }));
      req.end();
    });

    expect(capture.interruptCalls).toContainEqual({
      handoffRequested: false,
      source: "http_close",
    });
  });

  it("POST /v1/chat/completions without bearer token → 401", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      { "Content-Type": "application/json" },
      JSON.stringify({ messages: [{ role: "user", content: "hi" }] }),
    );
    expect(r.status).toBe(401);
  });

  it("POST /v1/chat/completions with no user message → 400", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({ messages: [] }),
    );
    expect(r.status).toBe(400);
  });

  it("POST /v1/turns/:id/ask-response → 404 when turn unknown", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/turns/unknown-turn/ask-response`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({ questionId: "q1", selectedId: "a" }),
    );
    expect(r.status).toBe(404);
    expect(r.body).toContain("turn_not_found");
  });

  it("POST /v1/turns/:id/ask-response missing questionId → 400", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/turns/t1/ask-response`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({ selectedId: "a" }),
    );
    expect(r.status).toBe(400);
    expect(r.body).toContain("missing_questionId");
  });

  it("POST /v1/chat/completions propagates replyTo into userMessage.metadata", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({
        messages: [{ role: "user", content: "follow-up question" }],
        replyTo: {
          messageId: "msg-42",
          preview: "the original statement",
          role: "assistant",
        },
      }),
    );
    expect(r.status).toBe(200);
    const userMsg = capture.userMessage as
      | { text: string; metadata?: { replyTo?: unknown } }
      | undefined;
    expect(userMsg).toBeDefined();
    expect(userMsg?.metadata?.replyTo).toEqual({
      messageId: "msg-42",
      preview: "the original statement",
      role: "assistant",
    });
  });

  it("POST /v1/chat/completions without replyTo leaves metadata unset", async () => {
    await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({
        messages: [{ role: "user", content: "plain message" }],
      }),
    );
    const userMsg = capture.userMessage as
      | { metadata?: unknown }
      | undefined;
    expect(userMsg).toBeDefined();
    expect(userMsg?.metadata).toBeUndefined();
  });

  it("POST /v1/chat/completions passes explicit model override to the turn", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({
        model: "openai/gpt-5.5-pro",
        messages: [{ role: "user", content: "use premium router here" }],
      }),
    );

    expect(r.status).toBe(200);
    expect(capture.runOptions).toMatchObject({
      runtimeModelOverride: "openai/gpt-5.5-pro",
    });
  });

  it("POST /v1/chat/completions treats router aliases as automatic local routing", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({
        model: "big-dic-router/auto",
        messages: [{ role: "user", content: "use premium router here" }],
      }),
    );

    expect(r.status).toBe(200);
    expect(capture.runOptions).not.toHaveProperty("runtimeModelOverride");
  });

  it("POST /v1/chat/completions preserves kb-context system addendum and image blocks", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({
        messages: [
          { role: "system", content: "[Channel: general]" },
          { role: "system", content: "[Current Time: 2026-04-24 09:00 UTC]" },
          {
            role: "system",
            content:
              "<kb-context>\n[file: report.pdf]\nRevenue was up 12%.\n</kb-context>",
          },
          {
            role: "user",
            content: [
              { type: "text", text: "please analyze this file" },
              {
                type: "image_url",
                image_url: {
                  url: "data:image/png;base64,QUJD",
                },
              },
            ],
          },
        ],
      }),
    );
    expect(r.status).toBe(200);
    const userMsg = capture.userMessage as
      | {
          text: string;
          imageBlocks?: Array<{
            type: "image";
            source: { type: "base64"; media_type: string; data: string };
          }>;
          metadata?: { systemPromptAddendum?: unknown };
        }
      | undefined;
    expect(userMsg).toBeDefined();
    expect(userMsg?.text).toBe("please analyze this file");
    expect(userMsg?.imageBlocks).toEqual([
      {
        type: "image",
        source: {
          type: "base64",
          media_type: "image/png",
          data: "QUJD",
        },
      },
    ]);
    expect(userMsg?.metadata?.systemPromptAddendum).toBe(
      "<kb-context>\n[file: report.pdf]\nRevenue was up 12%.\n</kb-context>",
    );
  });

  it("POST /v1/chat/completions installs and restores structured output contract", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify({
        messages: [{ role: "user", content: "return json" }],
        response_format: {
          type: "json_schema",
          json_schema: {
            name: "answer",
            schema: {
              type: "object",
              required: ["ok"],
              properties: { ok: { type: "boolean" } },
            },
            maxAttempts: 2,
          },
        },
      }),
    );

    expect(r.status).toBe(200);
    expect(capture.structuredSpecs?.[0]).toEqual({
      schemaName: "answer",
      schema: {
        type: "object",
        required: ["ok"],
        properties: { ok: { type: "boolean" } },
      },
      maxAttempts: 2,
    });
    expect(capture.structuredSpecs?.[1]).toBeNull();
  });

  it("POST /v1/chat/completions supports deterministic structured-output canary header", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/completions`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
        "x-core-agent-structured-output-canary": "retry-exhausted",
      },
      JSON.stringify({
        messages: [{ role: "user", content: "return invalid json" }],
      }),
    );

    expect(r.status).toBe(200);
    expect(capture.structuredSpecs?.[0]).toMatchObject({
      schemaName: "canary_retry_exhausted",
      maxAttempts: 1,
    });
    expect(capture.structuredSpecs?.[1]).toBeNull();
  });
});

describe("POST /v1/chat/inject", () => {
  let tmp: string;
  let server: HttpServer;
  let port: number;
  let injectState: InjectStubState;
  const TOKEN = "test-bearer-token";
  const SESSION_KEY = "agent:main:app:default:botTest1";

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "core-agent-inject-"));
    injectState = {
      activeSessions: new Set(),
      sessions: new Map(),
      activeTurns: new Map(),
    };
    const agent = makeStubAgent(tmp, undefined, injectState) as unknown as ConstructorParameters<
      typeof HttpServer
    >[0]["agent"];
    server = new HttpServer({ port: 0, agent, bearerToken: TOKEN });
    await server.start();
    const anyServer = server as unknown as { server: http.Server };
    const addr = anyServer.server.address();
    port = typeof addr === "object" && addr ? addr.port : 0;
  });

  afterEach(async () => {
    await server.stop();
    await fs.rm(tmp, { recursive: true, force: true });
  });

  async function postInject(payload: unknown): Promise<{
    status: number;
    body: string;
  }> {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/inject`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify(payload),
    );
    return { status: r.status, body: r.body };
  }

  it("rejects missing bearer token with 401", async () => {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/inject`,
      { "Content-Type": "application/json" },
      JSON.stringify({ sessionKey: SESSION_KEY, text: "hi" }),
    );
    expect(r.status).toBe(401);
  });

  it("rejects missing sessionKey with 400", async () => {
    const r = await postInject({ text: "hi" });
    expect(r.status).toBe(400);
    expect(r.body).toContain("missing_sessionKey");
  });

  it("rejects empty text with 400", async () => {
    const r = await postInject({ sessionKey: SESSION_KEY, text: "   " });
    expect(r.status).toBe(400);
    expect(r.body).toContain("empty_text");
  });

  it("returns 404 when session does not exist", async () => {
    const r = await postInject({ sessionKey: SESSION_KEY, text: "hi" });
    expect(r.status).toBe(404);
    expect(r.body).toContain("session_not_found");
  });

  it("returns 409 when session exists but no active turn", async () => {
    injectState.sessions.set(SESSION_KEY, {
      runTurn: async () => undefined,
      injectMessage: () => ({ injectionId: "inj-1", queuedCount: 1 }),
    });
    // activeSessions left empty — no active turn
    const r = await postInject({ sessionKey: SESSION_KEY, text: "hi" });
    expect(r.status).toBe(409);
    expect(r.body).toContain("no_active_turn");
  });

  it("returns 200 + injectionId when session has an active turn", async () => {
    injectState.sessions.set(SESSION_KEY, {
      runTurn: async () => undefined,
      injectMessage: () => ({ injectionId: "inj-abc-1", queuedCount: 1 }),
    });
    injectState.activeSessions.add(SESSION_KEY);
    const r = await postInject({
      sessionKey: SESSION_KEY,
      text: "queue me",
      source: "web",
    });
    expect(r.status).toBe(200);
    expect(r.body).toContain("inj-abc-1");
    expect(r.body).toContain("\"queuedCount\":1");
  });

  it("returns 429 when the session queue is full", async () => {
    injectState.sessions.set(SESSION_KEY, {
      runTurn: async () => undefined,
      injectMessage: () => null,
    });
    injectState.activeSessions.add(SESSION_KEY);
    const r = await postInject({ sessionKey: SESSION_KEY, text: "overflow" });
    expect(r.status).toBe(429);
    expect(r.body).toContain("queue_full");
  });
});

describe("POST /v1/chat/interrupt", () => {
  let tmp: string;
  let server: HttpServer;
  let port: number;
  let injectState: InjectStubState;
  const TOKEN = "test-bearer-token";
  const SESSION_KEY = "agent:main:app:default:botTest1";

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "core-agent-interrupt-"));
    injectState = {
      activeSessions: new Set(),
      sessions: new Map(),
      activeTurns: new Map(),
    };
    const agent = makeStubAgent(tmp, undefined, injectState) as unknown as ConstructorParameters<
      typeof HttpServer
    >[0]["agent"];
    server = new HttpServer({ port: 0, agent, bearerToken: TOKEN });
    await server.start();
    const anyServer = server as unknown as { server: http.Server };
    const addr = anyServer.server.address();
    port = typeof addr === "object" && addr ? addr.port : 0;
  });

  afterEach(async () => {
    await server.stop();
    await fs.rm(tmp, { recursive: true, force: true });
  });

  async function postInterrupt(payload: unknown): Promise<{
    status: number;
    body: string;
  }> {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/chat/interrupt`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify(payload),
    );
    return { status: r.status, body: r.body };
  }

  it("returns 404 when interrupt session does not exist", async () => {
    const r = await postInterrupt({ sessionKey: SESSION_KEY, handoffRequested: true });
    expect(r.status).toBe(404);
    expect(r.body).toContain("session_not_found");
  });

  it("returns 409 when no active turn exists for interrupt", async () => {
    injectState.sessions.set(SESSION_KEY, {
      runTurn: async () => undefined,
      requestInterrupt: () => ({ status: "accepted", handoffRequested: true }),
    });
    const r = await postInterrupt({ sessionKey: SESSION_KEY, handoffRequested: true });
    expect(r.status).toBe(409);
    expect(r.body).toContain("no_active_turn");
  });

  it("returns 200 + accepted interrupt status when the turn is active", async () => {
    injectState.sessions.set(SESSION_KEY, {
      runTurn: async () => undefined,
      requestInterrupt: (handoffRequested) => ({
        status: "accepted",
        handoffRequested: handoffRequested === true,
      }),
    });
    injectState.activeSessions.add(SESSION_KEY);
    const r = await postInterrupt({
      sessionKey: SESSION_KEY,
      handoffRequested: true,
      source: "web",
    });
    expect(r.status).toBe(200);
    expect(r.body).toContain("\"status\":\"accepted\"");
    expect(r.body).toContain("\"handoffRequested\":true");
  });
});

describe("HttpServer /v1/control-requests", () => {
  let tmp: string;
  let server: HttpServer;
  let port: number;
  let state: InjectStubState;
  const TOKEN = "test-bearer-token";
  const SESSION_KEY = "agent:main:app:general:botTest1";
  const OTHER_SESSION_KEY = "agent:main:app:general:botTest2";

  beforeEach(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "core-agent-control-"));
    state = {
      activeSessions: new Set(),
      sessions: new Map(),
      activeTurns: new Map(),
    };
    const agent = makeStubAgent(tmp, undefined, state) as unknown as ConstructorParameters<
      typeof HttpServer
    >[0]["agent"];
    server = new HttpServer({ port: 0, agent, bearerToken: TOKEN });
    await server.start();
    const anyServer = server as unknown as { server: http.Server };
    const addr = anyServer.server.address();
    port = typeof addr === "object" && addr ? addr.port : 0;
  });

  afterEach(async () => {
    await server.stop();
    await fs.rm(tmp, { recursive: true, force: true });
  });

  async function makeControlSession(sessionKey: string): Promise<StubSessionWithInject> {
    const rootDir = path.join(tmp, "core-agent", "sessions", sessionKey.replace(/[^a-z0-9]/gi, "_"));
    await fs.mkdir(rootDir, { recursive: true });
    const ledger = new ControlEventLedger({ rootDir, sessionKey });
    const session: StubSessionWithInject = {
      runTurn: async () => undefined,
      controlRequests: new ControlRequestStore({ ledger }),
      controlEvents: ledger,
    };
    state.sessions.set(sessionKey, session);
    return session;
  }

  async function postResponse(
    requestId: string,
    payload: Record<string, unknown>,
  ): Promise<{ status: number; body: string }> {
    const r = await rawRequest(
      "POST",
      `http://127.0.0.1:${port}/v1/control-requests/${requestId}/response`,
      {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
      JSON.stringify(payload),
    );
    return { status: r.status, body: r.body };
  }

  it("approves a pending tool permission request with updated input", async () => {
    const session = await makeControlSession(SESSION_KEY);
    const req = await session.controlRequests!.create({
      kind: "tool_permission",
      sessionKey: SESSION_KEY,
      channelName: "general",
      source: "turn",
      prompt: "Allow Bash?",
      proposedInput: { command: "pwd" },
      expiresAt: Date.now() + 60_000,
    });

    const r = await postResponse(req.requestId, {
      sessionKey: SESSION_KEY,
      decision: "approved",
      updatedInput: { command: "npm test" },
      feedback: "focused tests only",
    });

    expect(r.status).toBe(200);
    const body = JSON.parse(r.body) as { request: { state: string; decision: string; updatedInput: unknown } };
    expect(body.request.state).toBe("approved");
    expect(body.request.decision).toBe("approved");
    expect(body.request.updatedInput).toEqual({ command: "npm test" });
  });

  it("denies a pending request", async () => {
    const session = await makeControlSession(SESSION_KEY);
    const req = await session.controlRequests!.create({
      kind: "tool_permission",
      sessionKey: SESSION_KEY,
      source: "turn",
      prompt: "Allow FileWrite?",
      expiresAt: Date.now() + 60_000,
    });

    const r = await postResponse(req.requestId, {
      sessionKey: SESSION_KEY,
      decision: "denied",
      feedback: "not now",
    });

    expect(r.status).toBe(200);
    const body = JSON.parse(r.body) as { request: { state: string; decision: string; feedback: string } };
    expect(body.request.state).toBe("denied");
    expect(body.request.decision).toBe("denied");
    expect(body.request.feedback).toBe("not now");
  });

  it("answers a user_question request", async () => {
    const session = await makeControlSession(SESSION_KEY);
    const req = await session.controlRequests!.create({
      kind: "user_question",
      sessionKey: SESSION_KEY,
      source: "turn",
      prompt: "Which file?",
      expiresAt: Date.now() + 60_000,
    });

    const r = await postResponse(req.requestId, {
      sessionKey: SESSION_KEY,
      decision: "answered",
      answer: "report.md",
    });

    expect(r.status).toBe(200);
    const body = JSON.parse(r.body) as { request: { state: string; answer: string } };
    expect(body.request.state).toBe("answered");
    expect(body.request.answer).toBe("report.md");
  });

  it("resolves legacy ask_user cards through the control-request response route", async () => {
    const session = await makeControlSession(SESSION_KEY);
    state.sessions.set(SESSION_KEY, session);
    const resolved: Array<{
      questionId: string;
      answer: { selectedId?: string; freeText?: string };
    }> = [];
    state.activeTurns.set("turn_legacy", {
      resolveAsk(questionId, answer) {
        resolved.push({ questionId, answer });
        return true;
      },
    });

    const r = await postResponse("turn_legacy:ask:1", {
      sessionKey: SESSION_KEY,
      decision: "answered",
      answer: "regenerate",
    });

    expect(r.status).toBe(200);
    expect(JSON.parse(r.body)).toEqual({ ok: true });
    expect(resolved).toEqual([
      {
        questionId: "turn_legacy:ask:1",
        answer: { selectedId: "regenerate" },
      },
    ]);
  });

  it("duplicate responses return the original resolved state", async () => {
    const session = await makeControlSession(SESSION_KEY);
    const req = await session.controlRequests!.create({
      kind: "tool_permission",
      sessionKey: SESSION_KEY,
      source: "turn",
      prompt: "Allow Bash?",
      expiresAt: Date.now() + 60_000,
    });

    expect((await postResponse(req.requestId, {
      sessionKey: SESSION_KEY,
      decision: "approved",
    })).status).toBe(200);
    const second = await postResponse(req.requestId, {
      sessionKey: SESSION_KEY,
      decision: "denied",
      feedback: "too late",
    });

    expect(second.status).toBe(200);
    const body = JSON.parse(second.body) as { request: { state: string; decision: string; feedback?: string } };
    expect(body.request.state).toBe("approved");
    expect(body.request.decision).toBe("approved");
    expect(body.request.feedback).toBeUndefined();
  });

  it("expired requests return 409 and become timed_out", async () => {
    const session = await makeControlSession(SESSION_KEY);
    const req = await session.controlRequests!.create({
      kind: "tool_permission",
      sessionKey: SESSION_KEY,
      source: "turn",
      prompt: "Allow stale Bash?",
      expiresAt: Date.now() - 1,
    });

    const r = await postResponse(req.requestId, {
      sessionKey: SESSION_KEY,
      decision: "approved",
    });

    expect(r.status).toBe(409);
    const body = JSON.parse(r.body) as { error: string; request: { state: string } };
    expect(body.error).toBe("control_request_expired");
    expect(body.request.state).toBe("timed_out");
  });

  it("wrong session cannot resolve another session's request", async () => {
    const session = await makeControlSession(SESSION_KEY);
    await makeControlSession(OTHER_SESSION_KEY);
    const req = await session.controlRequests!.create({
      kind: "tool_permission",
      sessionKey: SESSION_KEY,
      source: "turn",
      prompt: "Allow Bash?",
      expiresAt: Date.now() + 60_000,
    });

    const r = await postResponse(req.requestId, {
      sessionKey: OTHER_SESSION_KEY,
      decision: "approved",
    });

    expect(r.status).toBe(404);
    expect(r.body).toContain("control_request_not_found");
  });

  it("GET returns pending requests scoped by session and channel", async () => {
    const session = await makeControlSession(SESSION_KEY);
    const other = await makeControlSession(OTHER_SESSION_KEY);
    const keep = await session.controlRequests!.create({
      kind: "tool_permission",
      sessionKey: SESSION_KEY,
      channelName: "general",
      source: "turn",
      prompt: "Allow Bash?",
      expiresAt: Date.now() + 60_000,
    });
    await session.controlRequests!.create({
      kind: "tool_permission",
      sessionKey: SESSION_KEY,
      channelName: "private",
      source: "turn",
      prompt: "Other channel",
      expiresAt: Date.now() + 60_000,
    });
    await other.controlRequests!.create({
      kind: "tool_permission",
      sessionKey: OTHER_SESSION_KEY,
      channelName: "general",
      source: "turn",
      prompt: "Other session",
      expiresAt: Date.now() + 60_000,
    });

    const r = await rawRequest(
      "GET",
      `http://127.0.0.1:${port}/v1/control-requests?sessionKey=${encodeURIComponent(SESSION_KEY)}&channelName=general`,
      { Authorization: `Bearer ${TOKEN}` },
    );

    expect(r.status).toBe(200);
    const body = JSON.parse(r.body) as { requests: Array<{ requestId: string; channelName?: string }> };
    expect(body.requests.map((request) => request.requestId)).toEqual([keep.requestId]);
    expect(body.requests[0]?.channelName).toBe("general");
  });

  it("GET /v1/control-events returns replayable events since lastSeq", async () => {
    const session = await makeControlSession(SESSION_KEY);
    await session.controlEvents!.append({
      type: "task_board_snapshot",
      turnId: "turn-1",
      taskBoard: { tasks: [{ id: "t1", status: "completed" }] },
    });
    const second = await session.controlEvents!.append({
      type: "verification",
      turnId: "turn-1",
      status: "pending",
      reason: "awaiting tests",
    });

    const r = await rawRequest(
      "GET",
      `http://127.0.0.1:${port}/v1/control-events?sessionKey=${encodeURIComponent(SESSION_KEY)}&lastSeq=1`,
      { Authorization: `Bearer ${TOKEN}` },
    );

    expect(r.status).toBe(200);
    const body = JSON.parse(r.body) as {
      lastSeq: number;
      events: Array<{ seq: number; type: string }>;
    };
    expect(body.lastSeq).toBe(second.seq);
    expect(body.events.map((event) => event.type)).toEqual(["verification"]);
  });

  it("GET /v1/control-events streams SSE replay when requested", async () => {
    const session = await makeControlSession(SESSION_KEY);
    await session.controlEvents!.append({
      type: "task_board_snapshot",
      turnId: "turn-1",
      taskBoard: { tasks: [{ id: "t1", status: "completed" }] },
    });

    const r = await rawRequest(
      "GET",
      `http://127.0.0.1:${port}/v1/control-events?sessionKey=${encodeURIComponent(SESSION_KEY)}&lastSeq=0&stream=1`,
      {
        Authorization: `Bearer ${TOKEN}`,
        Accept: "text/event-stream",
      },
    );

    expect(r.status).toBe(200);
    expect(r.contentType).toContain("text/event-stream");
    expect(r.body).toContain("\"type\":\"control_event\"");
    expect(r.body).toContain("\"type\":\"control_replay_complete\"");
  });
});

describe("extractReplyTo", () => {
  it("returns undefined for non-object bodies", () => {
    expect(extractReplyTo(null)).toBeUndefined();
    expect(extractReplyTo("nope")).toBeUndefined();
    expect(extractReplyTo(123)).toBeUndefined();
  });

  it("returns undefined when replyTo field is missing", () => {
    expect(extractReplyTo({ messages: [] })).toBeUndefined();
  });

  it("returns undefined on malformed role", () => {
    expect(
      extractReplyTo({
        replyTo: { messageId: "m1", preview: "p", role: "system" },
      }),
    ).toBeUndefined();
  });

  it("returns undefined on empty messageId", () => {
    expect(
      extractReplyTo({
        replyTo: { messageId: "", preview: "p", role: "user" },
      }),
    ).toBeUndefined();
  });

  it("returns parsed ReplyToRef for a valid object", () => {
    const out = extractReplyTo({
      replyTo: { messageId: "m1", preview: "hello", role: "user" },
      messages: [],
    });
    expect(out).toEqual({ messageId: "m1", preview: "hello", role: "user" });
  });
});

describe("extractRuntimeModelOverride", () => {
  it("returns trimmed explicit model names", () => {
    expect(extractRuntimeModelOverride({ model: "  openai/gpt-5.5-pro  " })).toBe(
      "openai/gpt-5.5-pro",
    );
  });

  it("drops empty and automatic model sentinel values", () => {
    expect(extractRuntimeModelOverride(null)).toBeUndefined();
    expect(extractRuntimeModelOverride({ model: "" })).toBeUndefined();
    expect(extractRuntimeModelOverride({ model: "auto" })).toBeUndefined();
    expect(extractRuntimeModelOverride({ model: "magi-smart-router/auto" })).toBeUndefined();
    expect(extractRuntimeModelOverride({ model: "big-dic-router/auto" })).toBeUndefined();
  });
});

describe("extractLastUserMessage", () => {
  it("extracts text and image blocks from mixed user content", () => {
    const out = extractLastUserMessage({
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: "What is in this image?" },
            {
              type: "image_url",
              image_url: { url: "data:image/png;base64,ZmFrZQ==" },
            },
          ],
        },
      ],
    });

    expect(out?.text).toBe("What is in this image?");
    expect(out?.imageBlocks).toEqual([
      {
        type: "image",
        source: {
          type: "base64",
          media_type: "image/png",
          data: "ZmFrZQ==",
        },
      },
    ]);
  });
});
