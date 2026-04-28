/**
 * Deferral blocker hook — beforeCommit, priority 86.
 *
 * Problem (2026-04-20 admin-bot POS case): the LLM drafts an answer
 * that promises "I'll send results when done" / "완료되면 결과
 * 보내드리겠습니다" / "5분 후 결과 드릴게요" AND ends the turn, stranding
 * the user. Claude Code handles long work by running the work
 * synchronously in the same turn and returning the artefact. Clawy
 * should match that.
 *
 * Distinct from preRefusalVerifier (priority 85): that blocks refusals
 * without investigation. This blocks turn endings where the bot
 * narrates future delivery without having delivered. Fires when:
 *   1. Response text matches a deferral-promise pattern, AND
 *   2. The turn either:
 *      (a) invoked a subagent / long-running Bash (i.e. work was
 *          actually started — but no deliverable materialised), OR
 *      (b) invoked NO tool at all (pure narration — deferring without
 *          even trying).
 *
 * Retry budget: 1. Fail-open on error. Operator-gate:
 * `CORE_AGENT_DEFERRAL_BLOCKER=off`.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";

/** Tool names that indicate work was actually started this turn. */
const WORK_TOOLS = new Set([
  "SpawnAgent",
  "Bash",
  "BashExec",
  "FileWrite",
  "FileEdit",
]);

const MAX_RETRIES = 1;

export interface DeferralBlockerAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_DEFERRAL_BLOCKER;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

const DEFERRAL_CLASSIFIER_PROMPT = `You classify whether an AI assistant's response contains a "deferral promise" — a promise to deliver results LATER instead of doing the work NOW in this turn.

Deferral (YES):
- "완료되면 결과 보내드리겠습니다" (will send when done)
- "5분 후 리포트 드릴게요" (will give report in 5 min)
- "I'll send the results when ready"
- "Check back in a few minutes"
- "나중에 알려드리겠습니다" (will let you know later)

NOT deferral (NO):
- "잠시만요, 지금 처리하겠습니다" (wait, processing now — synchronous)
- "결과를 정리해서 바로 보내드릴게요" (organizing and sending right away)
- "Let me run this and show you" (doing it now)
- "잠깐만 기다려주세요" as filler while working (not a future promise)
- Normal response without time-delayed delivery promises

Reply ONLY: YES or NO`;

/** LLM-based deferral classification. No regex fallback. */
export async function matchesDeferral(text: string, ctx?: HookContext): Promise<boolean> {
  if (!text || text.trim().length === 0) return false;
  if (!ctx?.llm) return false; // No LLM = fail-open

  try {
    let result = "";
    for await (const event of ctx.llm.stream({
      model: "claude-haiku-4-5",
      system: DEFERRAL_CLASSIFIER_PROMPT,
      messages: [{ role: "user", content: [{ type: "text", text: text.slice(0, 500) }] }],
      max_tokens: 10,
    })) {
      if (event.kind === "text_delta") result += event.delta;
    }
    return result.trim().toUpperCase().startsWith("YES");
  } catch {
    return false; // Fail-open on LLM error
  }
}

/** Exported for tests — count WORK_TOOLS calls in the turn's transcript. */
export function countWorkToolsThisTurn(
  transcript: ReadonlyArray<{ kind: string; turnId: string; name?: string }>,
  turnId: string,
): number {
  let n = 0;
  for (const entry of transcript) {
    if (entry.kind !== "tool_call") continue;
    if (entry.turnId !== turnId) continue;
    if (typeof entry.name === "string" && WORK_TOOLS.has(entry.name)) {
      n++;
    }
  }
  return n;
}

export interface DeferralBlockerOptions {
  agent?: DeferralBlockerAgent;
}

export function makeDeferralBlockerHook(
  opts: DeferralBlockerOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:deferral-blocker",
    point: "beforeCommit",
    // 86 — one notch after preRefusalVerifier (85), before answerVerifier (90).
    priority: 86,
    blocking: true,
    handler: async ({ assistantText, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };

        if (!assistantText || assistantText.trim().length === 0) {
          return { action: "continue" };
        }

        if (!(await matchesDeferral(assistantText, ctx))) {
          return { action: "continue" };
        }

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[deferral-blocker] retry budget exhausted; failing open", {
            retryCount,
          });
          ctx.emit({
            type: "rule_check",
            ruleId: "deferral-blocker",
            verdict: "violation",
            detail: "retry exhausted; failing open",
          });
          return { action: "continue" };
        }

        // We block for retry regardless of whether tools fired this
        // turn — the response promises future delivery in THIS turn.
        // Whether work already started or not, the bot should
        // complete and deliver inline.
        let entries: ReadonlyArray<TranscriptEntry> | null = null;
        if (opts.agent) {
          try {
            entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
          } catch (err) {
            ctx.log("warn", "[deferral-blocker] transcript read failed", {
              error: err instanceof Error ? err.message : String(err),
            });
            entries = null;
          }
        }
        const source = entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
        const workCount = countWorkToolsThisTurn(
          source as ReadonlyArray<{
            kind: string;
            turnId: string;
            name?: string;
          }>,
          ctx.turnId,
        );

        ctx.log("warn", "[deferral-blocker] blocking deferral promise", {
          retryCount,
          workCount,
        });
        ctx.emit({
          type: "rule_check",
          ruleId: "deferral-blocker",
          verdict: "violation",
          detail: `blocked; retryCount=${retryCount} workToolCalls=${workCount}`,
        });
        return {
          action: "block",
          reason: [
            "[RETRY:DEFERRAL_BLOCKED] You drafted a response that defers",
            "delivery to a later message (\"I'll send results when done\" /",
            "\"완료되면 결과 보내드릴게요\" / \"잠시만요\"). Clawy turns are",
            "synchronous: complete the work in THIS turn and return the",
            "result inline, like Claude Code does. Either:",
            "  (a) Call the remaining tools (SpawnAgent, Bash, FileRead,",
            "      ArtifactRead) NOW and synthesise results in the same",
            "      response, OR",
            "  (b) If the work genuinely cannot complete this turn, say",
            "      so plainly with a concrete reason — do not promise",
            "      future delivery you cannot keep.",
            "Remove the deferral phrasing and re-draft.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[deferral-blocker] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

/** Default singleton — no delegate, falls back to `ctx.transcript`. */
export const deferralBlockerHook = makeDeferralBlockerHook();
