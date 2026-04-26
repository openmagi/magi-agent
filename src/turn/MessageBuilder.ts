/**
 * MessageBuilder — compose system prompt + messages array for the
 * Anthropic /v1/messages call.
 *
 * Extracted from Turn.buildSystemPrompt + Turn.buildMessages (R3
 * refactor, 2026-04-19). Goes through ContextEngine.maybeCompact +
 * buildMessagesFromTranscript so transcripts exceeding the model's
 * context window get a `compaction_boundary` summary before read.
 *
 * Thinking block preservation sits inside the contextEngine replay,
 * not here — this module is purely about message assembly.
 */

import type { Session } from "../Session.js";
import type { ReplyToRef, UserMessage } from "../util/types.js";
import type { LLMMessage } from "../transport/LLMClient.js";
import { renderIdentitySystem } from "../storage/Workspace.js";
import { getCapability } from "../llm/modelCapabilities.js";

/**
 * Fallback soft cap on transcript tokens before `maybeCompact` is
 * asked to summarise. T4-17 sources the real per-model limit from
 * the capability registry (75% of contextWindow); this constant is
 * only used when the model id is not in the registry.
 */
export const TOKEN_LIMIT_FOR_COMPACTION = 150_000;

/**
 * Max preview length for the `[Reply to …]` preamble (chars, not
 * tokens). Kept small so multi-paragraph quotes don't blow out the
 * turn budget; the LLM only needs enough context to identify which
 * message the user is answering.
 */
export const REPLY_PREVIEW_MAX_CHARS = 200;

/**
 * Format a `ReplyToRef` into a single-line preamble. The line sits
 * above the user's actual text in the LLM user message so the model
 * sees the reply context before the new question.
 *
 *   [Reply to assistant: "…quoted excerpt…"]
 *   {user's actual text}
 *
 * Preview is truncated to {@link REPLY_PREVIEW_MAX_CHARS} with a `…`
 * suffix on overflow. Newlines in the preview collapse to single
 * spaces so the preamble stays exactly one line — simpler for the
 * model to parse and matches the `[Channel: …]` single-line pattern.
 */
export function formatReplyPreamble(replyTo: ReplyToRef): string {
  const collapsed = replyTo.preview.replace(/\s+/g, " ").trim();
  const truncated =
    collapsed.length > REPLY_PREVIEW_MAX_CHARS
      ? `${collapsed.slice(0, REPLY_PREVIEW_MAX_CHARS)}…`
      : collapsed;
  return `[Reply to ${replyTo.role}: "${truncated}"]`;
}

/**
 * Compose the system block from workspace identity files. Phase 1b
 * ships this as a single string — Phase 2 layers rules + memory +
 * live project-state on top via LayeredContext.
 */
export async function buildSystemPrompt(
  session: Session,
  turnId: string,
  userMessage?: UserMessage,
): Promise<string> {
  const identity = await session.agent.workspace.loadIdentity();
  const rendered = renderIdentitySystem(identity);
  const stamp = new Date().toISOString();
  // P3 — `[Channel: <kind>]` hint so the LLM can gate output channel
  // (Telegram file dispatch vs web/app delivery) on the session's
  // origin. Defaults to `web` for sessions without a populated channel
  // (back-compat with legacy stubs + tests). The canonical channel
  // types are `app | telegram | discord`; anything else or undefined
  // collapses to `web` — chat-proxy attaches `[Channel:]` itself for
  // web sessions so the bot sees a consistent header in every prompt.
  const channelType =
    typeof session.meta.channel?.type === "string" &&
    session.meta.channel.type.trim().length > 0
      ? session.meta.channel.type
      : "web";
  const sessionHeader = [
    `[Session: ${session.meta.sessionKey}]`,
    `[Turn: ${turnId}]`,
    `[Time: ${stamp}]`,
    `[Channel: ${channelType}]`,
  ].join("\n");
  const systemPromptAddendum =
    typeof userMessage?.metadata?.systemPromptAddendum === "string" &&
    userMessage.metadata.systemPromptAddendum.trim().length > 0
      ? userMessage.metadata.systemPromptAddendum.trim()
      : "";
  // Thinking vs text boundary rule — prevents the model from putting
  // substantive user-facing content inside the thinking block while
  // only emitting a brief closing line as text. Without this, the user
  // sees a thin response while the detailed analysis lives in thinking
  // (which is ephemeral and not committed to transcript).
  const thinkingBoundary = [
    "",
    "<output-rules>",
    "CRITICAL: The user can only see your TEXT output, not your thinking.",
    "",
    "1. Your thinking block is for internal reasoning ONLY — planning, analysis, deciding what to do.",
    "2. Everything you want the user to read MUST appear in your text response.",
    "3. NEVER put user-facing content (answers, analysis, questions, summaries) only in thinking.",
    "4. If your thinking contains a detailed response, you MUST reproduce the key content in your text output.",
    "5. A text response that is just a brief closing (e.g. '궁금한 점 있으신가요?') while thinking had the full analysis is a FAILURE.",
    "6. NEVER include raw tool output or JSON in your text response. Tool results (e.g. API responses, file contents, search results) are for YOUR reference only. Summarize the results in natural language for the user.",
    "7. Bad example: '{\"ok\":true,\"message\":\"Document added\"}' — NEVER show this to the user.",
    "   Good example: '문서가 성공적으로 저장되었습니다.' — natural language summary.",
    "</output-rules>",
  ].join("\n");
  const base = rendered ? `${sessionHeader}\n\n${rendered}` : sessionHeader;
  const withAddendum = systemPromptAddendum
    ? `${base}\n\n${systemPromptAddendum}`
    : base;
  return `${withAddendum}\n${thinkingBoundary}`;
}

/**
 * Rebuild the LLM messages[] from committed transcript entries plus
 * the current user message.
 *
 * T1-02 (§7.12.b revised): routes through `ContextEngine`, which
 * collapses any `compaction_boundary` entries into a synthetic summary
 * message. Before reading, it gives the engine a chance to write a
 * NEW boundary if the transcript is over `TOKEN_LIMIT_FOR_COMPACTION`
 * — the newly-appended boundary is picked up on the subsequent
 * re-read.
 */
export async function buildMessages(
  session: Session,
  userMessage: UserMessage,
): Promise<LLMMessage[]> {
  const committed = await session.transcript.readCommitted();
  // T4-17: model-aware context limit. Use 75% of the model's
  // contextWindow as the compaction threshold; fall back to the legacy
  // 150k constant for unknown models.
  const cap = getCapability(session.agent.config.model);
  const tokenLimit = cap
    ? Math.floor(cap.contextWindow * 0.75)
    : TOKEN_LIMIT_FOR_COMPACTION;
  // Pass the routed model so ContextEngine can apply the §11.6
  // feasibility check (throws CompactionImpossibleError when the window
  // is too small to fit summary+reserve). Without the model arg the
  // engine silently skips the floor check — see codex P2 (2026-04-20).
  await session.agent.contextEngine.maybeCompact(
    session,
    committed,
    tokenLimit,
    session.agent.config.model,
  );
  // Re-read: if a boundary was appended, it now lives in the transcript
  // and will be collapsed by the context engine.
  const refreshed = await session.transcript.readCommitted();
  const out = session.agent.contextEngine.buildMessagesFromTranscript(refreshed);
  // If the channel / client attached quoted-reply context, prepend a
  // `[Reply to {role}: "{preview}"]` line above the user's text so the
  // model can tell which prior message is being answered. Matches the
  // `[Channel: …]` hint pattern (commit 4b3fa5e0) — both go in the
  // system/user preamble so the model sees them on every turn.
  const replyTo = userMessage.metadata?.replyTo;
  const userContent = replyTo
    ? `${formatReplyPreamble(replyTo)}\n${userMessage.text}`
    : userMessage.text;
  if (userMessage.imageBlocks && userMessage.imageBlocks.length > 0) {
    const content = [
      ...(userContent
        ? [{ type: "text" as const, text: userContent }]
        : []),
      ...userMessage.imageBlocks,
    ];
    out.push({ role: "user", content });
    return out;
  }
  out.push({ role: "user", content: userContent });
  return out;
}
