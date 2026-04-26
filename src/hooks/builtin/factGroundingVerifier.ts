/**
 * Fact grounding verifier — beforeCommit, priority 82.
 *
 * Haiku-judged gate that blocks commits where the assistant's response
 * distorts or fabricates information from tool results used in the same
 * turn. Tool-scoped: only checks claims that reference tool output.
 * General knowledge answers pass through unchecked.
 *
 * Example: FileRead returns `{"model": "gemini-2.5-pro"}` but bot
 * writes "GPT-4o를 사용합니다" → DISTORTED → blocked.
 *
 * Retry budget: 1, then fail-open.
 * Toggle: `CORE_AGENT_FACT_GROUNDING=off` disables globally.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { LLMClient } from "../../transport/LLMClient.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";

export type GroundingVerdict = "GROUNDED" | "DISTORTED" | "FABRICATED";

const MAX_RETRIES = 1;
const DEFAULT_TIMEOUT_MS = 15_000;
const JUDGE_MODEL_FALLBACK = "claude-haiku-4-5-20251001";

/** Max chars per tool_result output in the judge prompt. */
const MAX_RESULT_CHARS = 8_000;

const JUDGE_SYSTEM = [
  "You are a fact-grounding auditor. Your job is to compare tool results",
  "against the assistant's draft response and detect distortions or fabrications.",
  "",
  "Respond with EXACTLY ONE WORD — one of:",
  "  GROUNDED   — all claims that reference tool output are accurate.",
  "  DISTORTED  — a claim references tool output but changes or misrepresents specific details.",
  "  FABRICATED — a claim attributes information to tool output that does not appear there at all.",
  "",
  "Rules:",
  "- Only judge claims that reference tool results (file contents, search results, command output).",
  "- Ignore general knowledge claims not tied to any tool output.",
  "- Paraphrasing is fine — only flag substantive factual errors.",
  "- When uncertain, default to GROUNDED.",
  "- Output only the one word, uppercase, no punctuation, no explanation.",
].join("\n");

export function parseGroundingVerdict(raw: string): GroundingVerdict {
  const token = raw.trim().toUpperCase().replace(/[^A-Z]/g, "");
  if (token.startsWith("DISTORTED")) return "DISTORTED";
  if (token.startsWith("FABRICATED")) return "FABRICATED";
  return "GROUNDED";
}

/**
 * Build the tool results summary for the judge prompt.
 * Pairs tool_call with tool_result by toolUseId.
 */
function buildToolResultsSummary(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): string {
  const calls = new Map<string, { name: string; input: unknown }>();
  const results = new Map<string, string>();

  for (const entry of transcript) {
    if (entry.turnId !== turnId) continue;
    if (entry.kind === "tool_call") {
      calls.set(entry.toolUseId, { name: entry.name, input: entry.input });
    }
    if (entry.kind === "tool_result" && entry.output) {
      results.set(
        entry.toolUseId,
        entry.output.slice(0, MAX_RESULT_CHARS),
      );
    }
  }

  const parts: string[] = [];
  for (const [tuId, call] of calls) {
    const output = results.get(tuId);
    if (!output) continue;
    parts.push(
      `[Tool: ${call.name}]\n` +
        `Input: ${JSON.stringify(call.input).slice(0, 500)}\n` +
        `Output:\n${output}`,
    );
  }

  return parts.join("\n\n---\n\n");
}

/** Public for tests. */
export async function judgeGrounding(
  llm: LLMClient,
  toolResultsSummary: string,
  assistantText: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
  model?: string,
): Promise<GroundingVerdict> {
  const deadline = Date.now() + timeoutMs;
  const judgePrompt = [
    "<tool_results>",
    toolResultsSummary.slice(0, 24_000),
    "</tool_results>",
    "",
    "<draft_response>",
    assistantText.slice(0, 6_000),
    "</draft_response>",
    "",
    "Does the draft response distort, fabricate, or contradict any information from the tool results?",
    "Focus ONLY on claims that reference tool output. Ignore general knowledge.",
    "",
    "Verdict (one word):",
  ].join("\n");

  let output = "";
  try {
    const stream = llm.stream({
      model: model ?? JUDGE_MODEL_FALLBACK,
      system: JUDGE_SYSTEM,
      messages: [{ role: "user", content: judgePrompt }],
      max_tokens: 8,
      temperature: 0,
    });
    for await (const evt of stream) {
      if (Date.now() > deadline) break;
      if (evt.kind === "text_delta") output += evt.delta;
      if (evt.kind === "message_end" || evt.kind === "error") break;
    }
  } catch {
    return "GROUNDED";
  }

  return parseGroundingVerdict(output);
}

const UNGROUNDED_JUDGE_SYSTEM = [
  "You judge whether an assistant's response makes specific claims about",
  "file contents, configurations, settings, code, or workspace state",
  "WITHOUT having used any tool (FileRead, Grep, Glob, Bash) to verify.",
  "",
  "Respond with EXACTLY ONE WORD — one of:",
  "  GROUNDED   — response is general knowledge, opinion, or does not",
  "               reference specific file/document/config contents.",
  "  FABRICATED — response claims to know specific details from a file,",
  "               document, script, config, or workspace resource",
  "               (e.g. exact numbers, model names, settings, code",
  "               snippets) but NO tool was used to read that resource.",
  "",
  "Examples of FABRICATED:",
  '  - "파일을 읽어보니 1200픽셀로 설정되어 있습니다" (no FileRead)',
  '  - "The config uses GPT-4o with temperature 0.7" (no FileRead)',
  '  - "스크립트에 따르면 3단계로 구성됩니다" (no FileRead)',
  "",
  "Examples of GROUNDED (even without tools):",
  '  - "React uses a Virtual DOM for efficient rendering" (general knowledge)',
  '  - "I\'m not sure, let me check the file" (honest uncertainty)',
  '  - "The project appears to be a web app" (vague, no specific claims)',
  "",
  "Rules:",
  "- If the response says it read/checked a file but no tool was used,",
  "  that is FABRICATED — the claim of having read is itself a fabrication.",
  "- General knowledge answers are always GROUNDED.",
  "- When uncertain, default to GROUNDED.",
  "- Output only the one word, uppercase, no punctuation.",
].join("\n");

/** Judge ungrounded claims — no tools were used but the response may
 *  claim specific file/document contents. */
export async function judgeUngroundedClaims(
  llm: LLMClient,
  assistantText: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
  model?: string,
): Promise<GroundingVerdict> {
  const deadline = Date.now() + timeoutMs;
  const prompt = [
    "The assistant produced the following response WITHOUT using any",
    "file-reading tools (FileRead, Grep, Glob, Bash) this turn.",
    "",
    "<draft_response>",
    assistantText.slice(0, 6_000),
    "</draft_response>",
    "",
    "Does this response claim specific details from files, documents,",
    "configs, or scripts that it could not know without reading them?",
    "",
    "Verdict (one word):",
  ].join("\n");

  let output = "";
  try {
    const stream = llm.stream({
      model: model ?? JUDGE_MODEL_FALLBACK,
      system: UNGROUNDED_JUDGE_SYSTEM,
      messages: [{ role: "user", content: prompt }],
      max_tokens: 8,
      temperature: 0,
    });
    for await (const evt of stream) {
      if (Date.now() > deadline) break;
      if (evt.kind === "text_delta") output += evt.delta;
      if (evt.kind === "message_end" || evt.kind === "error") break;
    }
  } catch {
    return "GROUNDED";
  }

  return parseGroundingVerdict(output);
}

export interface FactGroundingAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface FactGroundingVerifierOptions {
  agent?: FactGroundingAgent;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_FACT_GROUNDING;
  // Default OFF — false positive rate too high in production (2026-04-21).
  // Haiku judge miscalibrates when workspace has conflicting sources
  // (.env vs DAILY_RUNBOOK vs script). Re-enable after judge prompt tuning.
  if (raw === undefined || raw === null) return false;
  const v = raw.trim().toLowerCase();
  return v === "on" || v === "true" || v === "1";
}

export function makeFactGroundingVerifierHook(
  opts: FactGroundingVerifierOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:fact-grounding-verifier",
    point: "beforeCommit",
    priority: 82,
    blocking: true,
    failOpen: true,
    timeoutMs: DEFAULT_TIMEOUT_MS + 1_000,
    handler: async (
      { assistantText, toolCallCount, toolReadHappened, retryCount },
      ctx: HookContext,
    ) => {
      try {
        if (!isEnabled()) return { action: "continue" };

        if (!assistantText || assistantText.trim().length === 0) {
          return { action: "continue" };
        }

        // Two modes:
        // A) Read-type tools fired → ground response against tool_results
        // B) No read tools this turn → check if response claims
        //    file/document contents without having verified
        //
        // Key: use toolReadHappened (not toolCallCount) so that
        // non-read tools (NotifyUser, CronCreate, etc.) don't
        // accidentally route to Mode A which would fail open when
        // transcript is empty.

        let verdict: GroundingVerdict;

        if (!toolReadHappened) {
          // Mode B: no read tools — ask Haiku if the response makes
          // specific claims about file contents or workspace state
          // without any tool verification this turn.
          verdict = await judgeUngroundedClaims(
            ctx.llm,
            assistantText,
            DEFAULT_TIMEOUT_MS,
            ctx.agentModel,
          );
          // Only block on FABRICATED in mode B — DISTORTED doesn't
          // apply when there's nothing to distort.
          if (verdict === "DISTORTED") verdict = "GROUNDED";
        } else {
          // Mode A: read tools were used — ground against results
          let entries: ReadonlyArray<TranscriptEntry> | null = null;
          if (opts.agent) {
            try {
              entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
            } catch (err) {
              ctx.log(
                "warn",
                "[fact-grounding-verifier] transcript read failed; failing open",
                { error: err instanceof Error ? err.message : String(err) },
              );
              return { action: "continue" };
            }
          }
          const source =
            entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);

          const summary = buildToolResultsSummary(source, ctx.turnId);
          if (!summary || summary.trim().length === 0) {
            return { action: "continue" };
          }

          verdict = await judgeGrounding(
            ctx.llm,
            summary,
            assistantText,
            DEFAULT_TIMEOUT_MS,
            ctx.agentModel,
          );
        }

        ctx.emit({
          type: "rule_check",
          ruleId: "fact-grounding-verifier",
          verdict: verdict === "GROUNDED" ? "ok" : "violation",
          detail: `verdict=${verdict} retryCount=${retryCount}`,
        });

        if (verdict === "GROUNDED") {
          return { action: "continue" };
        }

        if (retryCount >= MAX_RETRIES) {
          ctx.log(
            "warn",
            "[fact-grounding-verifier] retry budget exhausted; failing open",
            { verdict, retryCount },
          );
          return { action: "continue" };
        }

        ctx.log(
          "warn",
          "[fact-grounding-verifier] blocking commit: tool output mismatch",
          { verdict, retryCount },
        );
        return {
          action: "block",
          reason: [
            `[RETRY:FACT_GROUNDING:${verdict}] Your response contains information`,
            "that does not match the tool results from this turn.",
            "",
            "Before finalising this answer:",
            "1) Re-read the tool output carefully.",
            "2) Correct any details that were changed, misquoted, or invented.",
            "3) Only include facts that are directly supported by tool results.",
            "4) If you need additional information, use the appropriate tool to retrieve it.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log(
          "warn",
          "[fact-grounding-verifier] unexpected error; failing open",
          { error: err instanceof Error ? err.message : String(err) },
        );
        return { action: "continue" };
      }
    },
  };
}
