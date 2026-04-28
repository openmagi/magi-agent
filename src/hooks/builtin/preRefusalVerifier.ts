/**
 * Pre-refusal verifier (Layer 3 of the meta-cognitive scaffolding —
 * docs/plans/2026-04-20-agent-self-model-design.md).
 *
 * beforeCommit gate, priority 85 (runs BEFORE answerVerifier at 90).
 *
 * Problem: the LLM drafts "I don't have X" / "KB에 없음" without ever
 * running Glob/Grep/FileRead/Bash against the workspace. Layer 1 tells
 * it to check first; Layer 2 gives it a map; Layer 3 is the
 * commit-time enforcement — if the drafted answer matches a refusal
 * pattern AND the turn log shows zero investigation tools used, we
 * block for one retry with an explicit instruction to go check.
 *
 * Retry budget: 1, matching answerVerifier. After that we log + fail
 * open — if the bot insists on refusing after one nudge, maybe it
 * really doesn't exist. Goal: "did you check?" not "you must not
 * refuse."
 *
 * Architectural note: `ctx.transcript` is currently always `[]` at
 * hook dispatch time (see HookContextBuilder — transcript wiring is a
 * future item). We therefore reach the tool-call log via a tiny agent
 * delegate that exposes the Session's on-disk transcript. This keeps
 * the hook pure-function testable (inject the delegate) and avoids
 * taking a hard dep on Session internals.
 *
 * Fail-open: any transcript read / pattern compile error logs a warn
 * and continues. A broken verifier must never block a legitimate
 * commit.
 *
 * Toggle: `CORE_AGENT_PRE_REFUSAL_VERIFY=off` disables globally.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";

/** Tool names whose presence in the turn's transcript means the bot
 * DID investigate before drafting — skips the block. */
const INVESTIGATION_TOOLS = new Set([
  "Glob",
  "Grep",
  "FileRead",
  "Bash",
]);

const MAX_RETRIES = 1;

export interface PreRefusalVerifierAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_PRE_REFUSAL_VERIFY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

const REFUSAL_CLASSIFIER_PROMPT = `You classify whether an AI assistant's response is a LAZY REFUSAL — claiming something doesn't exist or can't be found WITHOUT having actually investigated.

LAZY REFUSAL (YES):
- "KB에 해당 정보가 없습니다" (no info in KB) — without searching
- "I don't have access to that file" — without trying to read it
- "확인할 수 없습니다" (cannot verify) — without running any tools
- "찾을 수 없습니다" (cannot find) — without searching
- "저장되어 있지 않습니다" (not stored) — without checking

NOT A REFUSAL (NO):
- "검색해봤는데 결과가 없습니다" (searched but no results) — investigated first
- "파일을 확인했는데 해당 내용이 없습니다" (checked file, content not there)
- "I searched the KB and found no matching documents"
- Legitimate "not found" after actual tool use
- Normal responses without refusal language

Reply ONLY: YES or NO`;

/** LLM-based refusal classification. No regex fallback. */
export async function matchesRefusal(text: string, ctx?: HookContext): Promise<boolean> {
  if (!text || text.trim().length === 0) return false;
  if (!ctx?.llm) return false; // No LLM = fail-open

  try {
    let result = "";
    for await (const event of ctx.llm.stream({
      model: "claude-haiku-4-5",
      system: REFUSAL_CLASSIFIER_PROMPT,
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

/** Exported for tests — count investigation tool calls in the turn's
 * transcript (only entries tagged with the current turnId). */
export function countInvestigationsThisTurn(
  transcript: ReadonlyArray<{ kind: string; turnId: string; name?: string }>,
  turnId: string,
): number {
  let n = 0;
  for (const entry of transcript) {
    if (entry.kind !== "tool_call") continue;
    if (entry.turnId !== turnId) continue;
    if (typeof entry.name === "string" && INVESTIGATION_TOOLS.has(entry.name)) {
      n++;
    }
  }
  return n;
}

export interface PreRefusalVerifierOptions {
  /** Optional delegate that reads the session transcript from disk.
   * When omitted, the hook falls back to `ctx.transcript` — which is
   * empty in production today but populated in unit tests. */
  agent?: PreRefusalVerifierAgent;
}

export function makePreRefusalVerifierHook(
  opts: PreRefusalVerifierOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:pre-refusal-verifier",
    point: "beforeCommit",
    // Runs BEFORE answerVerifier (90). Cheap deterministic check — no
    // LLM call, so it can gate inexpensively before the Haiku judge.
    priority: 85,
    blocking: true,
    handler: async ({ assistantText, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };

        if (!assistantText || assistantText.trim().length === 0) {
          return { action: "continue" };
        }

        if (!(await matchesRefusal(assistantText, ctx))) {
          return { action: "continue" };
        }

        let entries: ReadonlyArray<TranscriptEntry> | null = null;
        if (opts.agent) {
          try {
            entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
          } catch (err) {
            ctx.log("warn", "[pre-refusal-verifier] transcript read failed", {
              error: err instanceof Error ? err.message : String(err),
            });
            entries = null;
          }
        }
        const source = entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);

        const investigationCount = countInvestigationsThisTurn(
          source as ReadonlyArray<{
            kind: string;
            turnId: string;
            name?: string;
          }>,
          ctx.turnId,
        );

        if (investigationCount > 0) {
          // Refusal with investigation is legitimate — let it through.
          ctx.emit({
            type: "rule_check",
            ruleId: "pre-refusal-verifier",
            verdict: "ok",
            detail: `refusal allowed; investigated=${investigationCount}`,
          });
          return { action: "continue" };
        }

        if (retryCount >= MAX_RETRIES) {
          ctx.log(
            "warn",
            "[pre-refusal-verifier] retry budget exhausted; failing open",
            { retryCount },
          );
          ctx.emit({
            type: "rule_check",
            ruleId: "pre-refusal-verifier",
            verdict: "violation",
            detail: `retry exhausted; failing open`,
          });
          return { action: "continue" };
        }

        ctx.log(
          "warn",
          "[pre-refusal-verifier] blocking refusal without investigation",
          { retryCount },
        );
        ctx.emit({
          type: "rule_check",
          ruleId: "pre-refusal-verifier",
          verdict: "violation",
          detail: `blocked for retry; retryCount=${retryCount}`,
        });
        return {
          action: "block",
          reason: [
            "[RETRY:PRE_REFUSAL_VERIFY] You are refusing / disclaiming",
            "without having checked the workspace this turn. Before",
            "finalising this answer:",
            "1) Glob or Bash(ls) a plausible workspace subtree.",
            "2) Grep for a substring of what the user asked about.",
            "3) FileRead any likely hits.",
            "Then re-draft based on what you actually find. If after",
            "checking the thing really is absent, say so — explicit",
            "refusal after investigation is fine.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[pre-refusal-verifier] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

/**
 * Default singleton — no delegate, falls back to `ctx.transcript`.
 * Convenient for tests that populate `ctx.transcript` manually (the
 * Layer 1/2 style). Production registration in
 * `src/hooks/builtin/index.ts` uses `makePreRefusalVerifierHook`
 * with an agent delegate that reads the on-disk JSONL.
 */
export const preRefusalVerifierHook = makePreRefusalVerifierHook();
