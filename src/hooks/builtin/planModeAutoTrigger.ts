/**
 * planModeAutoTrigger hook — detects "implementation-intent" phrases on
 * the first iteration of a turn and nudges the bot toward `/plan`
 * (superpowers:writing-plans) before it starts coding.
 *
 * Design reference:
 *   docs/plans/2026-04-20-superpowers-plugin-design.md (design decision #1:
 *   "Auto-trigger `/plan` mode — enabled globally.")
 *
 * Behaviour:
 *   - beforeLLMCall, priority 8 (runs after memory-injector / mid-turn
 *     at 3-5 so the nudge lands late in the system-prompt stack where
 *     the LLM is most likely to honour it).
 *   - First iteration only (iteration === 0).
 *   - Inspects the latest user-role message text for
 *     implementation-intent patterns (see IMPLEMENTATION_INTENT_RE).
 *   - If the session is already in `plan` permissionMode OR the bot has
 *     been opted out via env gate, skip silently.
 *   - When triggered, appends a short `<plan_mode_nudge>` system-block
 *     suffix to `args.system` (not a full rewrite — stays additive so
 *     any upstream hook's system edits are preserved).
 *   - Fail-open: any regex/classifier error → `continue`, no mutation.
 *
 * Env gate: `CORE_AGENT_PLAN_AUTOTRIGGER` (default "on", per-bot
 * opt-out via `off` / `false` / `0`).
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { PermissionMode } from "../../Session.js";
import { latestUserText } from "./classifyTurnMode.js";
import { getOrClassifyRequestMeta } from "./turnMetaClassifier.js";

export interface PlanModeAutoTriggerAgent {
  /** Returns the current permissionMode for `sessionKey`, or null when
   *  the session has been evicted between scheduling + dispatch. */
  getSessionPermissionMode(sessionKey: string): PermissionMode | null;
}

export interface PlanModeAutoTriggerOpts {
  readonly agent: PlanModeAutoTriggerAgent;
}

/**
 * Implementation-intent regex — conservative. Matches when the user
 * appears to be asking the bot to author production code (write a
 * feature / implement an endpoint / refactor an existing module) rather
 * than merely asking a question, fetching data, or chit-chatting.
 *
 * The regex composes three clauses:
 *   1. Bare imperatives: build|implement|add|create|write|refactor.
 *   2. "write/add/create … feature|api|endpoint|hook|service" pairs,
 *      so "write a doc" doesn't trigger but "write an endpoint" does.
 *   3. Case-insensitive.
 *
 * Kept intentionally small — the nudge is cheap, so false positives
 * are preferable to missing a real coding request. The bot can always
 * decline the nudge and proceed without `/plan`.
 */
const IMPLEMENTATION_CLASSIFIER_PROMPT = `Does this message ask to BUILD or IMPLEMENT something non-trivial (code feature, API, service, component)?

YES examples: "implement the API endpoint", "구현해줘", "함수 만들어", "add a new route handler", "refactor the auth module", "서비스 빌드해줘"
NO examples: "explain this code", "코드 분석해줘", "what does this do", "search for X", "write a document", "요약해줘", simple questions, file operations

Reply ONLY: YES or NO`;

const DOCUMENT_OR_FILE_OUTPUT_RE =
  /(?:docx|pdf|md|markdown|hwpx|hwp|pptx|xlsx|csv)|\b(?:document|file|report)\b|(?:마크다운|문서|파일|보고서|리포트|첨부|전달)/i;
const DOCUMENT_OR_FILE_ACTION_RE =
  /(?:만들|작성|생성|변환|내보내|내뱉|보내|전달|첨부|deliver|attach|export|convert|render|write|generate|create)/i;
const CODE_IMPLEMENTATION_TARGET_RE =
  /\b(?:api|endpoint|route|handler|hook|service|component|module|function|class|middleware|schema|migration|database|frontend|backend)\b|(?:구현|코드|엔드포인트|라우트|핸들러|서비스|컴포넌트|모듈|함수|마이그레이션|프론트|백엔드)/i;

function isDocumentOrFileOperation(text: string): boolean {
  return (
    DOCUMENT_OR_FILE_OUTPUT_RE.test(text) &&
    DOCUMENT_OR_FILE_ACTION_RE.test(text) &&
    !CODE_IMPLEMENTATION_TARGET_RE.test(text)
  );
}

export async function matchesImplementationIntent(text: string, ctx?: HookContext): Promise<boolean> {
  if (!text) return false;
  if (!ctx?.llm) return false;

  try {
    let result = "";
    for await (const event of ctx.llm.stream({
      model: "claude-haiku-4-5",
      system: IMPLEMENTATION_CLASSIFIER_PROMPT,
      messages: [{ role: "user", content: [{ type: "text", text: text.slice(0, 300) }] }],
      max_tokens: 10,
    })) {
      if (event.kind === "text_delta") result += event.delta;
    }
    return result.trim().toUpperCase().startsWith("YES");
  } catch {
    return false;
  }
}

/** Parse the env gate. Default "on" unless explicitly disabled. */
export function isAutoTriggerEnabled(env: string | undefined): boolean {
  const v = (env ?? "on").trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

const PLAN_NUDGE_BLOCK = `

<plan_mode_nudge>
It looks like this request involves building / implementing something
non-trivial. Consider invoking \`/plan\` (or \`/superpowers:writing-plans\`)
to draft a numbered implementation plan before you touch code.
You do not have to — but if the scope is ≥ 3 steps, planning first
tends to save rework.
</plan_mode_nudge>
`.trim();

export function makePlanModeAutoTriggerHook(
  opts: PlanModeAutoTriggerOpts,
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:plan-mode-auto-trigger",
    point: "beforeLLMCall",
    // Priority 8: after classify-turn-mode (3) + memory/mid-turn (3-5)
    // but before most late system-prompt appenders so the nudge has a
    // stable relative position in the final prompt.
    priority: 8,
    blocking: false,
    timeoutMs: 5_000,
    handler: async (args, ctx: HookContext) => {
      try {
        // Env gate — operators / per-bot config may disable.
        if (!isAutoTriggerEnabled(process.env.CORE_AGENT_PLAN_AUTOTRIGGER)) {
          return { action: "continue" };
        }
        // Only nudge on the first iteration of a turn — subsequent
        // tool-loop iterations already have tool context and don't
        // need a generic pre-code pep talk.
        if (args.iteration > 0) return { action: "continue" };

        // Skip if the session is already in plan mode — the user has
        // the signal already.
        const mode = opts.agent.getSessionPermissionMode(ctx.sessionKey);
        if (mode === "plan") return { action: "continue" };

        const text = latestUserText(args.messages);
        if (!text) return { action: "continue" };
        if (isDocumentOrFileOperation(text)) return { action: "continue" };
        const classified = await getOrClassifyRequestMeta(ctx, { userMessage: text });
        if (classified.documentOrFileOperation) return { action: "continue" };
        if (!classified.implementationIntent) return { action: "continue" };

        ctx.log("info", "[plan-mode-auto-trigger] nudging toward /plan", {
          turnId: ctx.turnId,
        });

        const nextSystem = args.system
          ? `${args.system}\n\n${PLAN_NUDGE_BLOCK}`
          : PLAN_NUDGE_BLOCK;

        return {
          action: "replace",
          value: {
            ...args,
            system: nextSystem,
          },
        };
      } catch (err) {
        ctx.log("warn", "[plan-mode-auto-trigger] fail-open", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}
