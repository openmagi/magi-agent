/**
 * ToolSelector — pick the tool set exposed to the LLM for one turn.
 *
 * Extracted from Turn.buildToolDefs (R3 refactor, 2026-04-19). Owns:
 *   • T2-08 plan-mode read-only filter (driven by session permissionMode)
 *   • Skill intent classification + filterToolsByIntent
 *   • Hard cap at MAX_TOOLS_PER_TURN (§9.8 P3)
 *   • intent tool_start / tool_end SSE emission for observability
 */

import type { Session } from "../Session.js";
import type { SseWriter } from "../transport/SseWriter.js";
import type { LLMToolDef } from "../transport/LLMClient.js";
import type { Tool } from "../Tool.js";
import { filterToolsByIntent } from "../rules/IntentClassifier.js";
import type { ToolRegistry } from "../tools/ToolRegistry.js";

/**
 * Hard cap on tools exposed per turn (§9.8 P3).
 * 2026-04-20 0.17.1: 15 → 50 for Claude Code parity. Bots with 100+
 * skills previously had relevant skills truncated by the 15-cap after
 * intent classification picked too few tags. 50 covers every realistic
 * intent overlap while staying below the model's tool-def token budget.
 */
export const MAX_TOOLS_PER_TURN = 50;

/** Tool names allowed while planMode=true. Writes remain runtime-gated. */
export const PLAN_MODE_ALLOWED_TOOLS: ReadonlySet<string> = new Set([
  "FileRead",
  "Glob",
  "Grep",
  "PatchApply",
  "TaskBoard",
  "ExitPlanMode",
  "AskUserQuestion",
  "SwitchToActMode",
]);

export interface ToolSelectorDeps {
  readonly session: Session;
  readonly sse: SseWriter;
  readonly turnId: string;
  readonly userText: string;
  /** True when the session (or Turn mirror) is in plan mode. */
  readonly planMode: boolean;
  /** Tool names already discovered via tool_reference in message history.
   *  Discovered deferred tools are sent with full schema (no defer_loading). */
  readonly discoveredToolNames?: ReadonlySet<string>;
}

export function isStrictPlanModeEnabled(): boolean {
  const v = (process.env.MAGI_STRICT_PLAN_MODE ?? "").trim().toLowerCase();
  return v === "1" || v === "true" || v === "on";
}

export async function buildToolDefs(deps: ToolSelectorDeps): Promise<LLMToolDef[]> {
  const registry = deps.session.agent.tools as ToolRegistry;
  let all: Tool[];

  if (deps.planMode && isStrictPlanModeEnabled() && typeof registry.getAvailableTools === "function") {
    registry.setMode("plan");
    all = registry.getAvailableTools();
  } else if (deps.planMode) {
    all = registry.list().filter((t) => PLAN_MODE_ALLOWED_TOOLS.has(t.name));
  } else {
    if (typeof registry.setMode === "function") registry.setMode("act");
    all = registry.list();
  }

  const hasSkills = all.some((t) => t.kind === "skill");

  let selected: Tool[];
  if (!hasSkills) {
    selected = all.slice(0, MAX_TOOLS_PER_TURN);
  } else {
    // Collect unique tags across loaded skills for the classifier.
    const tagSet = new Set<string>();
    for (const t of all) if (t.kind === "skill") for (const tag of t.tags ?? []) tagSet.add(tag);
    const availableTags = [...tagSet];

    const intentTags = await deps.session.agent.intent.classify(
      deps.userText,
      availableTags,
    );
    deps.sse.agent({
      type: "tool_start",
      id: `intent-${deps.turnId}`,
      name: `intent:${intentTags.join(",") || "general"}`,
    });
    deps.sse.agent({
      type: "tool_end",
      id: `intent-${deps.turnId}`,
      status: "ok",
      durationMs: 0,
      output_preview: intentTags.join(","),
    });

    selected = filterToolsByIntent(all, intentTags, MAX_TOOLS_PER_TURN);
  }

  return selected.map((t) => {
    const isDeferred = t.shouldDefer === true;
    const isDiscovered = deps.discoveredToolNames?.has(t.name) ?? false;

    if (isDeferred && !isDiscovered) {
      return {
        name: t.name,
        description: t.description,
        input_schema: t.inputSchema,
        defer_loading: true as const,
      };
    }
    return {
      name: t.name,
      description: t.description,
      input_schema: t.inputSchema,
    };
  });
}
