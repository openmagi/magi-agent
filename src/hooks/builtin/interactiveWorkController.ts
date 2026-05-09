/**
 * Interactive work controller — browser/GUI tasks are prone to two
 * failure modes:
 *   1. the model says it will open/click/test, but ends the turn
 *      before using the Browser tools;
 *   2. it keeps taking slow browser actions until an external request
 *      timeout kills the turn before any usable checkpoint is committed.
 *
 * The semantic decision is delegated to the shared LLM request
 * classifier (`goalProgress.actionKinds=browser_interaction`) so this
 * hook does not add another classifier call or language-specific
 * keyword rules. Runtime enforcement is deterministic: current-turn
 * Browser/SocialBrowser tool evidence and elapsed/tool budgets.
 */

import type { LLMMessage, LLMToolDef } from "../../transport/LLMClient.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { HookContext, RegisteredHook } from "../types.js";
import { getOrClassifyRequestMeta } from "./turnMetaClassifier.js";

const DEFAULT_CHECKPOINT_MS = 120_000;
const DEFAULT_MAX_TOOL_RESULTS = 6;
const MAX_RETRIES = 1;
const INTERACTIVE_TOOL_NAMES = new Set(["Browser", "SocialBrowser"]);
const INTERACTIVE_KIND_LABELS = new Set([
  "browser_interaction",
  "browser_testing",
  "browser",
  "gui_interaction",
  "interactive_browser",
  "manual_ui",
  "ui_interaction",
  "web_ui",
  "web_ui_testing",
  "website_testing",
]);

export interface InteractiveWorkControllerOptions {
  now?: () => number;
  checkpointMs?: number;
  maxToolResults?: number;
  agent?: InteractiveWorkControllerAgent;
}

export interface InteractiveWorkControllerAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

interface InteractiveToolStats {
  callCount: number;
  resultCount: number;
  failedResultCount: number;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_INTERACTIVE_WORK_CONTROLLER;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function positiveIntFromEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function checkpointMs(opts: InteractiveWorkControllerOptions): number {
  return opts.checkpointMs ?? positiveIntFromEnv(
    "CORE_AGENT_INTERACTIVE_CHECKPOINT_MS",
    DEFAULT_CHECKPOINT_MS,
  );
}

function maxToolResults(opts: InteractiveWorkControllerOptions): number {
  return opts.maxToolResults ?? positiveIntFromEnv(
    "CORE_AGENT_INTERACTIVE_MAX_TOOL_RESULTS",
    DEFAULT_MAX_TOOL_RESULTS,
  );
}

function contentToString(content: LLMMessage["content"]): string {
  if (typeof content === "string") return content;
  return content
    .map((block) => (block.type === "text" ? block.text : ""))
    .filter((text) => text.length > 0)
    .join("\n");
}

function currentTurnUserMessage(
  ctx: HookContext,
  transcript: ReadonlyArray<TranscriptEntry>,
  messages?: readonly LLMMessage[],
): string {
  for (let i = transcript.length - 1; i >= 0; i -= 1) {
    const entry = transcript[i];
    if (entry?.kind === "user_message" && entry.turnId === ctx.turnId) {
      return entry.text;
    }
  }

  if (messages) {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const msg = messages[i];
      if (msg?.role === "user") return contentToString(msg.content);
    }
  }
  return "";
}

function turnStartedAt(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): number | null {
  for (const entry of transcript) {
    if (entry.kind === "turn_started" && entry.turnId === turnId) {
      return entry.ts;
    }
  }
  return null;
}

function interactiveToolStats(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): InteractiveToolStats {
  const toolUseIds = new Set<string>();
  let callCount = 0;
  let resultCount = 0;
  let failedResultCount = 0;

  for (const entry of transcript) {
    if (entry.turnId !== turnId) continue;
    if (entry.kind === "tool_call" && INTERACTIVE_TOOL_NAMES.has(entry.name)) {
      callCount += 1;
      toolUseIds.add(entry.toolUseId);
      continue;
    }
    if (entry.kind !== "tool_result" || !toolUseIds.has(entry.toolUseId)) continue;
    resultCount += 1;
    if (entry.isError === true || entry.status === "error") failedResultCount += 1;
  }

  return { callCount, resultCount, failedResultCount };
}

async function requestNeedsInteractiveController(
  ctx: HookContext,
  userMessage: string,
): Promise<boolean> {
  const meta = await getOrClassifyRequestMeta(ctx, { userMessage });
  if (!meta.goalProgress.requiresAction) return false;
  return meta.goalProgress.actionKinds.some((kind) =>
    INTERACTIVE_KIND_LABELS.has(kind.trim().toLowerCase()),
  );
}

function withInteractiveContract(system: string): string {
  if (system.includes("<interactive_work_contract>")) return system;
  return [
    system,
    "",
    "<interactive_work_contract>",
    "This turn has been classified as browser/GUI interactive work.",
    "Use Browser/SocialBrowser tools for concrete progress before making action claims.",
    "Work in short evidence-backed loops: act, observe, decide the next action.",
    "Do not end the turn by only saying what you will open, click, test, or do next.",
    "If a runtime interactive checkpoint message appears, stop calling browser tools in this turn and summarize only actual evidence, current blocker, and the exact next action. Do not claim final completion unless the requested objective is actually complete.",
    "</interactive_work_contract>",
  ].join("\n");
}

function withoutInteractiveTools(tools: readonly LLMToolDef[]): LLMToolDef[] {
  return tools.filter((tool) => !INTERACTIVE_TOOL_NAMES.has(tool.name));
}

function checkpointMessage(input: {
  elapsedMs: number;
  stats: InteractiveToolStats;
  limitMs: number;
  maxResults: number;
}): string {
  return [
    "Runtime interactive checkpoint:",
    `- elapsedMs=${Math.max(0, Math.round(input.elapsedMs))}`,
    `- browserToolResults=${input.stats.resultCount}/${input.maxResults}`,
    `- browserFailedResults=${input.stats.failedResultCount}`,
    `- checkpointMs=${input.limitMs}`,
    "",
    "Do not call more Browser or SocialBrowser tools in this turn.",
    "Return a concise checkpoint now:",
    "- what concrete UI/browser actions actually completed",
    "- what the latest observation showed",
    "- whether the user goal is complete",
    "- if incomplete, the exact next browser action needed",
    "Do not say the task is complete unless the requested goal is actually complete.",
  ].join("\n");
}

function shouldCheckpoint(input: {
  elapsedMs: number;
  stats: InteractiveToolStats;
  limitMs: number;
  maxResults: number;
}): boolean {
  if (input.stats.resultCount <= 0) return false;
  return input.elapsedMs >= input.limitMs || input.stats.resultCount >= input.maxResults;
}

async function readTranscript(
  ctx: HookContext,
  agent?: InteractiveWorkControllerAgent,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!agent) return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  try {
    const entries = await agent.readSessionTranscript(ctx.sessionKey);
    return entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
  } catch (err) {
    ctx.log("warn", "[interactive-work-controller] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  }
}

export function makeInteractiveWorkControllerHooks(
  opts: InteractiveWorkControllerOptions = {},
): {
  beforeLLMCall: RegisteredHook<"beforeLLMCall">;
  beforeCommit: RegisteredHook<"beforeCommit">;
} {
  const now = opts.now ?? Date.now;
  return {
    beforeLLMCall: {
      name: "builtin:interactive-work-controller",
      point: "beforeLLMCall",
      priority: 8,
      blocking: true,
      failOpen: true,
      timeoutMs: 8_000,
      handler: async (args, ctx) => {
        if (!isEnabled()) return { action: "continue" };
        const transcript = await readTranscript(ctx, opts.agent);
        const userMessage = currentTurnUserMessage(ctx, transcript, args.messages);
        if (!userMessage.trim()) return { action: "continue" };
        const needsController = await requestNeedsInteractiveController(ctx, userMessage);
        if (!needsController) return { action: "continue" };

        const stats = interactiveToolStats(transcript, ctx.turnId);
        const startedAt = turnStartedAt(transcript, ctx.turnId);
        const elapsedMs = startedAt === null ? 0 : now() - startedAt;
        const limitMs = checkpointMs(opts);
        const maxResults = maxToolResults(opts);
        const system = withInteractiveContract(args.system);

        if (shouldCheckpoint({ elapsedMs, stats, limitMs, maxResults })) {
          const nextTools = withoutInteractiveTools(args.tools);
          ctx.emit({
            type: "rule_check",
            ruleId: "interactive-work-controller",
            verdict: "violation",
            detail: `forcing checkpoint elapsedMs=${Math.round(elapsedMs)} browserToolResults=${stats.resultCount}`,
          });
          ctx.log("warn", "[interactive-work-controller] forcing browser checkpoint", {
            elapsedMs,
            browserToolResults: stats.resultCount,
            browserFailedResults: stats.failedResultCount,
            remainingTools: nextTools.map((tool) => tool.name),
          });
          return {
            action: "replace",
            value: {
              ...args,
              system,
              tools: nextTools,
              messages: [
                ...args.messages,
                {
                  role: "user",
                  content: checkpointMessage({ elapsedMs, stats, limitMs, maxResults }),
                },
              ],
            },
          };
        }

        if (system !== args.system) {
          return {
            action: "replace",
            value: { ...args, system },
          };
        }
        return { action: "continue" };
      },
    },
    beforeCommit: {
      name: "builtin:interactive-work-evidence-gate",
      point: "beforeCommit",
      priority: 84,
      blocking: true,
      failOpen: true,
      timeoutMs: 8_000,
      handler: async ({ assistantText, userMessage, retryCount }, ctx) => {
        if (!isEnabled()) return { action: "continue" };
        if (!assistantText.trim()) return { action: "continue" };
        const needsController = await requestNeedsInteractiveController(ctx, userMessage);
        if (!needsController) return { action: "continue" };

        const transcript = await readTranscript(ctx, opts.agent);
        const stats = interactiveToolStats(transcript, ctx.turnId);
        if (stats.callCount > 0) return { action: "continue" };
        const retryExhausted = retryCount >= MAX_RETRIES;

        ctx.emit({
          type: "rule_check",
          ruleId: "interactive-work-evidence-gate",
          verdict: "violation",
          detail: `interactive browser/GUI request ended with no current-turn Browser tool call; retryExhausted=${retryExhausted}`,
        });
        ctx.log("warn", "[interactive-work-controller] blocking interactive turn with no tool evidence");
        return {
          action: "block",
          reason: [
            retryExhausted
              ? "[RULE:INTERACTIVE_TOOL_REQUIRED]"
              : "[RETRY:INTERACTIVE_TOOL_REQUIRED]",
            "The user asked for browser/GUI interaction, but this turn has no",
            "current-turn Browser/SocialBrowser tool-call evidence.",
            "Do not finish by saying you will open, click, inspect, or test.",
            "Use the Browser/SocialBrowser tool now for the next concrete action,",
            "then report the actual observation or a hard blocker with evidence.",
          ].join("\n"),
        };
      },
    },
  };
}
