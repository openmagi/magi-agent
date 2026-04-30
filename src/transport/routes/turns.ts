/**
 * Turn routes:
 *   POST /v1/chat/completions         — start a streaming turn (SSE).
 *   POST /v1/turns/:turnId/ask-response — resolve a pending
 *     AskUserQuestion call for a running turn.
 *
 * Both routes bearer-gate via `Authorization: Bearer <token>` (not the
 * `X-Gateway-Token` header used by audit/compliance/session/contexts).
 * Behaviour preserved verbatim from the pre-split HttpServer.ts.
 */

import {
  authorizeBearer,
  parseUrl,
  readJsonBody,
  route,
  writeJson,
  type HttpServerCtx,
  type RouteHandler,
} from "./_helpers.js";
import { SseWriter } from "../SseWriter.js";
import { applyResetToSessionKey } from "../../slash/resetCounters.js";
import type {
  ChannelRef,
  ImageContentBlock,
  ReplyToRef,
  UserMessage,
  UserMessageMetadata,
} from "../../util/types.js";
import type { ControlEvent } from "../../control/ControlEvents.js";
import type { StructuredOutputSpec } from "../../structured/StructuredOutputContract.js";
import type { IncomingMessage, ServerResponse } from "node:http";

type ControlDecision = "approved" | "denied" | "answered";
type ControlResponseInput = {
  decision: ControlDecision;
  feedback?: string;
  updatedInput?: unknown;
  answer?: string;
};
type LegacyAskAnswer = {
  selectedId?: string;
  freeText?: string;
};
type ControlSession = {
  controlRequests?: unknown;
  controlEvents?: unknown;
  getStructuredOutputContract?: () => StructuredOutputSpec | null;
  setStructuredOutputContract?: (spec: StructuredOutputSpec | null) => void;
  resolveControlRequest?: (
    requestId: string,
    input: ControlResponseInput,
  ) => Promise<{ state: string }>;
};

const SUPPORTED_DATA_URL_IMAGE_MIME_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
] satisfies ImageContentBlock["source"]["media_type"][]);

/**
 * Parse an optional `replyTo` descriptor off the chat/completions
 * request body. Validates structurally (object with string fields +
 * role ∈ {user, assistant}) and drops silently on any mismatch so a
 * malformed field never fails the turn — the reply preamble is a
 * best-effort hint, not a contract.
 */
export function extractReplyTo(body: unknown): ReplyToRef | undefined {
  if (!body || typeof body !== "object") return undefined;
  const raw = (body as { replyTo?: unknown }).replyTo;
  if (!raw || typeof raw !== "object") return undefined;
  const obj = raw as {
    messageId?: unknown;
    preview?: unknown;
    role?: unknown;
  };
  if (typeof obj.messageId !== "string" || obj.messageId.length === 0) {
    return undefined;
  }
  if (typeof obj.preview !== "string") return undefined;
  if (obj.role !== "user" && obj.role !== "assistant") return undefined;
  return {
    messageId: obj.messageId,
    preview: obj.preview,
    role: obj.role,
  };
}

export function extractLastUserMessage(body: unknown): UserMessage | null {
  if (!body || typeof body !== "object") return null;
  const messages = (body as { messages?: unknown }).messages;
  if (!Array.isArray(messages)) return null;
  const systemPromptAddendum = extractSystemPromptAddendum(messages);
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as { role?: string; content?: unknown } | undefined;
    if (!m || m.role !== "user") continue;
    const { text, imageBlocks } = extractTextAndImages(m.content);
    const replyTo = extractReplyTo(body);
    const metadata: UserMessageMetadata = {};
    if (replyTo) metadata.replyTo = replyTo;
    if (systemPromptAddendum) {
      metadata.systemPromptAddendum = systemPromptAddendum;
    }
    return {
      text,
      receivedAt: Date.now(),
      ...(imageBlocks.length > 0 ? { imageBlocks } : {}),
      ...(Object.keys(metadata).length > 0 ? { metadata } : {}),
    };
  }
  return null;
}

function extractTextBlocks(content: unknown): string[] {
  if (typeof content === "string") return [content];
  if (!Array.isArray(content)) return [];
  return content
    .filter(
      (block: unknown): block is { type: "text"; text: string } =>
        !!block &&
        typeof block === "object" &&
        (block as { type?: unknown }).type === "text" &&
        typeof (block as { text?: unknown }).text === "string",
    )
    .map((block) => block.text);
}

function parseImageDataUrl(url: string): ImageContentBlock | null {
  const match = url.match(
    /^data:(image\/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/=]+)$/i,
  );
  if (!match) return null;
  const mediaType = match[1]?.toLowerCase();
  const data = match[2];
  if (!mediaType || !data) return null;
  if (!SUPPORTED_DATA_URL_IMAGE_MIME_TYPES.has(mediaType as ImageContentBlock["source"]["media_type"])) {
    return null;
  }
  const supportedMediaType = mediaType as ImageContentBlock["source"]["media_type"];
  return {
    type: "image",
    source: {
      type: "base64",
      media_type: supportedMediaType,
      data,
    },
  };
}

function extractStructuredOutputSpec(
  body: unknown,
  req: IncomingMessage,
): StructuredOutputSpec | null {
  const canary = req.headers["x-core-agent-structured-output-canary"];
  if (typeof canary === "string" && canary.trim().length > 0) {
    return {
      schemaName: `canary_${canary.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_")}`,
      schema: {
        type: "object",
        required: ["ok"],
        properties: { ok: { type: "boolean" } },
      },
      maxAttempts: canary.trim().toLowerCase() === "retry-exhausted" ? 1 : 3,
    };
  }

  if (!body || typeof body !== "object") return null;
  const responseFormat = (body as { response_format?: unknown }).response_format;
  if (!responseFormat || typeof responseFormat !== "object") return null;
  const obj = responseFormat as {
    type?: unknown;
    json_schema?: unknown;
  };
  if (obj.type !== "json_schema" || !obj.json_schema || typeof obj.json_schema !== "object") {
    return null;
  }
  const jsonSchema = obj.json_schema as {
    name?: unknown;
    schema?: unknown;
    maxAttempts?: unknown;
  };
  if (!jsonSchema.schema || typeof jsonSchema.schema !== "object") return null;
  return {
    ...(typeof jsonSchema.name === "string" && jsonSchema.name.length > 0
      ? { schemaName: jsonSchema.name }
      : {}),
    schema: jsonSchema.schema as StructuredOutputSpec["schema"],
    ...(typeof jsonSchema.maxAttempts === "number" && Number.isFinite(jsonSchema.maxAttempts)
      ? { maxAttempts: Math.max(1, Math.floor(jsonSchema.maxAttempts)) }
      : {}),
  };
}

function extractTextAndImages(content: unknown): {
  text: string;
  imageBlocks: ImageContentBlock[];
} {
  if (typeof content === "string") {
    return { text: content, imageBlocks: [] };
  }
  if (!Array.isArray(content)) {
    return { text: "", imageBlocks: [] };
  }
  const textParts: string[] = [];
  const imageBlocks: ImageContentBlock[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    const type = (block as { type?: unknown }).type;
    if (
      type === "text" &&
      typeof (block as { text?: unknown }).text === "string"
    ) {
      textParts.push((block as { text: string }).text);
      continue;
    }
    if (type !== "image_url") continue;
    const rawUrl = (block as { image_url?: { url?: unknown } }).image_url?.url;
    if (typeof rawUrl !== "string") continue;
    const image = parseImageDataUrl(rawUrl);
    if (image) imageBlocks.push(image);
  }
  return {
    text: textParts.join("\n"),
    imageBlocks,
  };
}

function extractSystemPromptAddendum(messages: unknown[]): string | undefined {
  const addenda = messages
    .map((msg) => {
      if (!msg || typeof msg !== "object") return null;
      const role = (msg as { role?: unknown }).role;
      if (role !== "system") return null;
      const parts = extractTextBlocks((msg as { content?: unknown }).content)
        .map((part) => part.trim())
        .filter(Boolean);
      if (parts.length === 0) return null;
      const text = parts.join("\n");
      if (/^\[Current Time:/.test(text)) return null;
      if (/^\[Channel:/.test(text)) return null;
      return text;
    })
    .filter((value): value is string => typeof value === "string");

  return addenda.length > 0 ? addenda.join("\n\n") : undefined;
}

async function handleChatCompletions(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;

  // Session key: accept both the Clawy-native and core-agent-native headers.
  const sessionKey =
    (req.headers["x-core-agent-session-key"] as string | undefined) ??
    (req.headers["x-clawy-session-key"] as string | undefined) ??
    `agent:main:app:default:${ctx.agent.config.botId.slice(0, 8)}`;

  const body = await readJsonBody(req).catch((err: Error) => {
    writeJson(res, 400, { error: "bad_body", message: err.message });
    return null;
  });
  if (body === null) return;

  const userMsg = extractLastUserMessage(body);
  if (!userMsg) {
    writeJson(res, 400, { error: "no_user_message" });
    return;
  }

  const channel: ChannelRef = {
    type: "app",
    channelId:
      sessionKey.match(/^agent:[^:]+:[^:]+:([^:]+)/)?.[1] ?? "default",
  };
  // Apply any per-channel `/reset` counter. Counter == 0 leaves the
  // incoming sessionKey untouched (existing clients unaffected). Once
  // a user has run `/reset` the sessionKey picks up a `:<N>` suffix so
  // subsequent turns land in a fresh session namespace.
  const resetCounter = await ctx.agent.resetCounters.get(channel);
  const effectiveSessionKey = applyResetToSessionKey(sessionKey, resetCounter);
  const session = await ctx.agent.getOrCreateSession(effectiveSessionKey, channel);
  const structuredOutputSpec = extractStructuredOutputSpec(body, req);
  const previousStructuredOutputSpec =
    structuredOutputSpec && typeof session.getStructuredOutputContract === "function"
      ? session.getStructuredOutputContract()
      : null;
  if (structuredOutputSpec) {
    if (typeof session.setStructuredOutputContract !== "function") {
      writeJson(res, 500, { error: "structured_output_contract_unavailable" });
      return;
    }
    session.setStructuredOutputContract(structuredOutputSpec);
  }

  const sse = new SseWriter(res);
  sse.start();

  // If the client disconnects mid-turn, interrupt the live Turn so
  // LLM streams and cooperative tools can stop spending work.
  res.once("close", () => {
    if (!res.writableEnded) {
      session.requestInterrupt(false, "http_close");
    }
  });

  // Plan mode (§7.2) can be toggled via HTTP header. The Turn also
  // detects a `[PLAN_MODE: on]` marker embedded in the user text, so
  // clients that can't set headers (e.g. some channel relays) still
  // have a path.
  const planHeader = (req.headers["x-core-agent-plan-mode"] ?? "")
    .toString()
    .toLowerCase();
  const planMode =
    planHeader === "on" || planHeader === "1" || planHeader === "true";

  try {
    await session.runTurn(userMsg, sse, { planMode });
  } finally {
    if (structuredOutputSpec && typeof session.setStructuredOutputContract === "function") {
      session.setStructuredOutputContract(previousStructuredOutputSpec);
    }
    sse.end();
  }
}

async function handleAskResponse(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const turnId = match[1] as string;
  const body = await readJsonBody(req).catch((err: Error) => {
    writeJson(res, 400, { error: "bad_body", message: err.message });
    return null;
  });
  if (body === null) return;
  const payload = body as {
    questionId?: unknown;
    selectedId?: unknown;
    freeText?: unknown;
  };
  if (typeof payload.questionId !== "string" || payload.questionId.length === 0) {
    writeJson(res, 400, { error: "missing_questionId" });
    return;
  }
  const turn = ctx.agent.getActiveTurn(turnId);
  if (!turn) {
    writeJson(res, 404, { error: "turn_not_found" });
    return;
  }
  const answer: { selectedId?: string; freeText?: string } = {};
  if (typeof payload.selectedId === "string") answer.selectedId = payload.selectedId;
  if (typeof payload.freeText === "string") answer.freeText = payload.freeText;
  if (answer.selectedId === undefined && answer.freeText === undefined) {
    writeJson(res, 400, { error: "empty_answer" });
    return;
  }
  const resolved = turn.resolveAsk(payload.questionId, answer);
  if (!resolved) {
    writeJson(res, 404, { error: "question_not_pending" });
    return;
  }
  writeJson(res, 200, { ok: true });
}

function sessionKeyFromRequest(
  req: IncomingMessage,
  body?: Record<string, unknown>,
): string | null {
  const url = parseUrl(req.url);
  const querySessionKey = url.searchParams.get("sessionKey");
  const headerSessionKey =
    (req.headers["x-core-agent-session-key"] as string | undefined) ??
    (req.headers["x-clawy-session-key"] as string | undefined);
  const bodySessionKey =
    typeof body?.sessionKey === "string" ? body.sessionKey : undefined;
  const sessionKey = bodySessionKey ?? querySessionKey ?? headerSessionKey;
  return sessionKey && sessionKey.length > 0 ? sessionKey : null;
}

function getSessionForControlRequest(
  ctx: HttpServerCtx,
  sessionKey: string,
): ControlSession | null {
  const withGetSession = ctx.agent as unknown as {
    getSession?: (sessionKey: string) => unknown;
  };
  const direct = withGetSession.getSession?.(sessionKey);
  if (direct) return direct as ControlSession;
  const byList = ctx.agent
    .listSessions()
    .find((session) => session.meta.sessionKey === sessionKey);
  return (byList as ControlSession | undefined) ?? null;
}

async function getOrHydrateControlSession(
  ctx: HttpServerCtx,
  sessionKey: string,
  channelName: string | null,
): Promise<ControlSession | null> {
  const existing = getSessionForControlRequest(ctx, sessionKey);
  if (existing) return existing;
  const withCreate = ctx.agent as unknown as {
    getOrCreateSession?: (sessionKey: string, channel: ChannelRef) => Promise<unknown>;
  };
  if (typeof withCreate.getOrCreateSession !== "function") return null;
  return await withCreate.getOrCreateSession(sessionKey, {
    type: "app",
    channelId:
      channelName ??
      sessionKey.match(/^agent:[^:]+:[^:]+:([^:]+)/)?.[1] ??
      "default",
  }) as ControlSession;
}

function isControlDecision(value: unknown): value is ControlDecision {
  return value === "approved" || value === "denied" || value === "answered";
}

function legacyAskTurnId(requestId: string): string | null {
  const marker = ":ask:";
  const idx = requestId.indexOf(marker);
  return idx > 0 ? requestId.slice(0, idx) : null;
}

function legacyAskAnswerFromPayload(
  payload: Record<string, unknown>,
): LegacyAskAnswer | null {
  if (typeof payload.selectedId === "string" && payload.selectedId.length > 0) {
    return { selectedId: payload.selectedId };
  }
  if (typeof payload.freeText === "string" && payload.freeText.trim().length > 0) {
    return { freeText: payload.freeText.trim() };
  }
  if (typeof payload.answer === "string" && payload.answer.trim().length > 0) {
    return { selectedId: payload.answer.trim() };
  }
  return null;
}

function resolveLegacyAskResponse(
  ctx: HttpServerCtx,
  requestId: string,
  payload: Record<string, unknown>,
): "not_legacy" | "empty_answer" | "not_pending" | "resolved" {
  const turnId = legacyAskTurnId(requestId);
  if (!turnId) return "not_legacy";
  if (payload.decision !== "answered") return "empty_answer";
  const answer = legacyAskAnswerFromPayload(payload);
  if (!answer) return "empty_answer";
  const turn = ctx.agent.getActiveTurn(turnId);
  if (!turn) return "not_pending";
  return turn.resolveAsk(requestId, answer) ? "resolved" : "not_pending";
}

function controlStoreOf(
  session: ControlSession,
): {
  pending: () => Promise<Array<{ channelName?: string }>>;
  resolve: (
    requestId: string,
    input: ControlResponseInput,
  ) => Promise<{ state: string }>;
} | null {
  const store = session.controlRequests;
  if (!store || typeof store !== "object") return null;
  const candidate = store as {
    pending?: unknown;
    resolve?: unknown;
  };
  if (typeof candidate.pending !== "function" || typeof candidate.resolve !== "function") {
    return null;
  }
  return store as ReturnType<typeof controlStoreOf>;
}

function controlEventsOf(
  session: ControlSession,
): { readSince: (seq: number) => Promise<ControlEvent[]> } | null {
  const ledger = session.controlEvents;
  if (!ledger || typeof ledger !== "object") return null;
  const candidate = ledger as { readSince?: unknown };
  if (typeof candidate.readSince !== "function") return null;
  return ledger as { readSince: (seq: number) => Promise<ControlEvent[]> };
}

async function handleControlEventsReplay(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const url = parseUrl(req.url);
  const sessionKey = sessionKeyFromRequest(req);
  if (!sessionKey) {
    writeJson(res, 400, { error: "missing_sessionKey" });
    return;
  }
  const lastSeqRaw = url.searchParams.get("lastSeq") ?? "0";
  const lastSeq = Number.parseInt(lastSeqRaw, 10);
  if (!Number.isFinite(lastSeq) || lastSeq < 0) {
    writeJson(res, 400, { error: "invalid_lastSeq" });
    return;
  }
  const session = await getOrHydrateControlSession(
    ctx,
    sessionKey,
    url.searchParams.get("channelName"),
  );
  if (!session) {
    writeJson(res, 404, { error: "session_not_found" });
    return;
  }
  const ledger = controlEventsOf(session);
  if (!ledger) {
    writeJson(res, 500, { error: "control_event_ledger_unavailable" });
    return;
  }

  const events = await ledger.readSince(lastSeq);
  const replayLastSeq = events.reduce(
    (max, event) => Math.max(max, event.seq),
    lastSeq,
  );
  const wantsSse =
    url.searchParams.get("stream") === "1" ||
    String(req.headers.accept ?? "").includes("text/event-stream");
  if (!wantsSse) {
    writeJson(res, 200, {
      ok: true,
      sessionKey,
      lastSeq: replayLastSeq,
      events,
    });
    return;
  }

  const sse = new SseWriter(res);
  sse.start();
  for (const event of events) {
    sse.agent({ type: "control_event", seq: event.seq, event });
  }
  sse.agent({ type: "control_replay_complete", lastSeq: replayLastSeq });
  sse.end();
}

async function handleControlRequestsList(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const sessionKey = sessionKeyFromRequest(req);
  if (!sessionKey) {
    writeJson(res, 400, { error: "missing_sessionKey" });
    return;
  }
  const session = getSessionForControlRequest(ctx, sessionKey);
  if (!session) {
    writeJson(res, 404, { error: "session_not_found" });
    return;
  }
  const store = controlStoreOf(session);
  if (!store) {
    writeJson(res, 500, { error: "control_request_store_unavailable" });
    return;
  }
  const channelName = parseUrl(req.url).searchParams.get("channelName");
  const pending = await store.pending();
  const requests = channelName
    ? pending.filter((request) => request.channelName === channelName)
    : pending;
  writeJson(res, 200, { ok: true, sessionKey, requests });
}

async function handleControlRequestResponse(
  req: IncomingMessage,
  res: ServerResponse,
  match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;
  const requestId = decodeURIComponent(match[1] as string);
  const body = await readJsonBody(req).catch((err: Error) => {
    writeJson(res, 400, { error: "bad_body", message: err.message });
    return null;
  });
  if (body === null) return;
  const payload = body && typeof body === "object"
    ? (body as Record<string, unknown>)
    : {};
  const sessionKey = sessionKeyFromRequest(req, payload);
  if (!sessionKey) {
    writeJson(res, 400, { error: "missing_sessionKey" });
    return;
  }
  if (!isControlDecision(payload.decision)) {
    writeJson(res, 400, { error: "invalid_decision" });
    return;
  }
  const session = getSessionForControlRequest(ctx, sessionKey);
  if (!session) {
    writeJson(res, 404, { error: "session_not_found" });
    return;
  }
  const store = controlStoreOf(session);
  if (!store) {
    writeJson(res, 500, { error: "control_request_store_unavailable" });
    return;
  }

  try {
    const resolve = typeof session.resolveControlRequest === "function"
      ? session.resolveControlRequest.bind(session)
      : store.resolve.bind(store);
    const request = await resolve(requestId, {
      decision: payload.decision,
      ...(typeof payload.feedback === "string"
        ? { feedback: payload.feedback }
        : {}),
      ...(payload.updatedInput !== undefined
        ? { updatedInput: payload.updatedInput }
        : {}),
      ...(typeof payload.answer === "string" ? { answer: payload.answer } : {}),
    });
    if (request.state === "timed_out") {
      writeJson(res, 409, {
        ok: false,
        error: "control_request_expired",
        request,
      });
      return;
    }
    writeJson(res, 200, { ok: true, request });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (/control request not found/.test(msg)) {
      const legacy = resolveLegacyAskResponse(ctx, requestId, payload);
      if (legacy === "resolved") {
        writeJson(res, 200, { ok: true });
        return;
      }
      if (legacy === "empty_answer") {
        writeJson(res, 400, { error: "empty_answer" });
        return;
      }
      if (legacy === "not_pending") {
        writeJson(res, 404, { error: "question_not_pending" });
        return;
      }
      writeJson(res, 404, { error: "control_request_not_found" });
      return;
    }
    writeJson(res, 500, { error: "control_request_response_failed", message: msg });
  }
}

/**
 * POST /v1/chat/inject — queue a message to be absorbed into the next
 * LLM iteration of the currently-streaming turn (#86).
 *
 * 200 { injectionId, queuedCount }  on success
 * 404 { error: "session_not_found" } when no session matches the key
 * 409 { error: "no_active_turn" }    when the session has no streaming
 *                                     turn (caller should POST to
 *                                     /v1/chat/completions instead)
 * 429 { error: "queue_full" }        when MAX_PENDING_INJECTIONS hit
 *
 * Auth: same bearer-token gate as /v1/chat/completions.
 */
async function handleInject(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;

  const body = await readJsonBody(req).catch((err: Error) => {
    writeJson(res, 400, { error: "bad_body", message: err.message });
    return null;
  });
  if (body === null) return;

  const payload = body as {
    sessionKey?: unknown;
    text?: unknown;
    source?: unknown;
  };
  if (typeof payload.sessionKey !== "string" || payload.sessionKey.length === 0) {
    writeJson(res, 400, { error: "missing_sessionKey" });
    return;
  }
  if (typeof payload.text !== "string" || payload.text.trim().length === 0) {
    writeJson(res, 400, { error: "empty_text" });
    return;
  }
  const source =
    payload.source === "web" ||
    payload.source === "mobile" ||
    payload.source === "telegram" ||
    payload.source === "discord" ||
    payload.source === "api"
      ? payload.source
      : "api";

  const session = ctx.agent.getSession(payload.sessionKey);
  if (!session) {
    writeJson(res, 404, { error: "session_not_found" });
    return;
  }

  // "No active turn" = nothing currently streaming. Detected via the
  // agent's active-turn registry. If no turn is live for this session,
  // the client should POST /v1/chat/completions normally.
  if (!ctx.agent.hasActiveTurnForSession(payload.sessionKey)) {
    writeJson(res, 409, {
      error: "no_active_turn",
      hint: "POST /v1/chat/completions instead",
    });
    return;
  }

  const queued = session.injectMessage(payload.text, source);
  if (!queued) {
    writeJson(res, 429, {
      error: "queue_full",
      hint: "wait for the current turn to finish, then retry",
    });
    return;
  }

  writeJson(res, 200, queued);
}

/**
 * POST /v1/chat/interrupt — cooperatively stop the currently-running
 * turn for a session. Used by the web/mobile composer ESC path so the
 * queued follow-up can move immediately instead of waiting for the
 * current tool chain to finish naturally.
 */
async function handleInterrupt(
  req: IncomingMessage,
  res: ServerResponse,
  _match: RegExpMatchArray,
  ctx: HttpServerCtx,
): Promise<void> {
  if (!authorizeBearer(req, res, ctx)) return;

  const body = await readJsonBody(req).catch((err: Error) => {
    writeJson(res, 400, { error: "bad_body", message: err.message });
    return null;
  });
  if (body === null) return;

  const payload = body as {
    sessionKey?: unknown;
    handoffRequested?: unknown;
    source?: unknown;
  };
  if (typeof payload.sessionKey !== "string" || payload.sessionKey.length === 0) {
    writeJson(res, 400, { error: "missing_sessionKey" });
    return;
  }

  const session = ctx.agent.getSession(payload.sessionKey);
  if (!session) {
    writeJson(res, 404, { error: "session_not_found" });
    return;
  }
  if (!ctx.agent.hasActiveTurnForSession(payload.sessionKey)) {
    writeJson(res, 409, { error: "no_active_turn" });
    return;
  }

  const source =
    payload.source === "web" ||
    payload.source === "mobile" ||
    payload.source === "telegram" ||
    payload.source === "discord" ||
    payload.source === "api"
      ? payload.source
      : "api";
  const result = session.requestInterrupt(payload.handoffRequested === true, source);
  writeJson(res, 200, result);
}

export const turnsRoutes: RouteHandler[] = [
  route("POST", /^\/v1\/chat\/completions(?:\?.*)?$/, handleChatCompletions),
  route("GET", /^\/v1\/control-events(?:\?.*)?$/, handleControlEventsReplay),
  route(
    "GET",
    /^\/v1\/control-requests(?:\?.*)?$/,
    handleControlRequestsList,
  ),
  route(
    "POST",
    /^\/v1\/control-requests\/([^/]+)\/response(?:\?.*)?$/,
    handleControlRequestResponse,
  ),
  route(
    "POST",
    /^\/v1\/turns\/([^/]+)\/ask-response$/,
    handleAskResponse,
  ),
  route("POST", /^\/v1\/chat\/inject$/, handleInject),
  route("POST", /^\/v1\/chat\/interrupt$/, handleInterrupt),
];
