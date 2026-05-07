/**
 * Built-in session commitment tracker (port of
 * infra/docker/api-proxy/session-commitment-tracker.js).
 * Design reference: §6 invariant C (route contract).
 *
 * Scans the conversation history for [META: route=...] tags. If a
 * prior turn committed to a non-direct route (subagent / pipeline)
 * and the current turn shows signs of silent fallback to direct,
 * this hook injects a non-negotiable warning into the system prompt
 * of the upcoming LLM call.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { LLMMessage } from "../../transport/LLMClient.js";
import { normalizeRouteValue } from "../../turn/routeMeta.js";

const META_RE = /\[META:\s*([^\]]+?)\]/i;
const ROUTE_RE = /route\s*=\s*([^,\]]+)/i;
const INTENT_RE = /intent\s*=\s*([^,\]]+)/i;
const FALLBACK_PHRASE_RE =
  /(?:서브에이전트(?:가)?\s*(?:안|실패|못)|직접\s*처리(?:하|해)|직접\s*답(?:변|하)|직접\s*확인|subagent\s*failed|falling back|I['’]ll (?:just )?(?:do|handle) (?:it|this) directly)/i;

interface Commitment {
  route: string;
  intent: string | null;
}

function textOfMessage(m: LLMMessage): string {
  if (typeof m.content === "string") return m.content;
  if (Array.isArray(m.content)) {
    return m.content
      .filter((b) => b && typeof b === "object" && (b as { type?: string }).type === "text")
      .map((b) => (b as { text: string }).text ?? "")
      .join("\n");
  }
  return "";
}

function parseMeta(text: string): Commitment | null {
  const m = META_RE.exec(text);
  if (!m) return null;
  const blob = m[1] ?? "";
  const routeRaw = ROUTE_RE.exec(blob)?.[1] ?? null;
  const route = normalizeRouteValue(routeRaw) ?? routeRaw?.trim().toLowerCase() ?? null;
  if (!route) return null;
  const intent = INTENT_RE.exec(blob)?.[1]?.trim() ?? null;
  return { route, intent };
}

function findOutstandingCommitment(messages: LLMMessage[], lookback = 6): Commitment | null {
  const assistants: LLMMessage[] = [];
  for (let i = messages.length - 1; i >= 0 && assistants.length < lookback; i--) {
    const m = messages[i];
    if (m && m.role === "assistant") assistants.push(m);
  }
  for (const a of assistants) {
    const meta = parseMeta(textOfMessage(a));
    if (!meta) continue;
    if (meta.route === "subagent" || meta.route === "subagent->gate" || meta.route === "pipeline") return meta;
    if (meta.route === "direct") return null; // latest META wins
  }
  return null;
}

function detectFallbackPhrase(messages: LLMMessage[]): boolean {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m || m.role !== "assistant") continue;
    if (FALLBACK_PHRASE_RE.test(textOfMessage(m))) return true;
    break; // only the most recent assistant turn
  }
  return false;
}

function buildHint(outstanding: Commitment | null, fallbackDetected: boolean): string | null {
  if (!outstanding && !fallbackDetected) return null;
  const route = outstanding?.route ?? "subagent";
  const domain = outstanding?.intent ?? "the prior task";

  return `<aef_route_commitment priority="critical">
On a prior turn you committed to \`route=${route}\` for ${domain}.
${
  fallbackDetected
    ? 'Your most recent response showed phrasing suggesting a fallback to direct execution (e.g. "서브에이전트가 안 되니 직접 처리"). This pattern is BLOCKED.'
    : "That commitment is still outstanding."
}

**Rules (hard, non-negotiable):**

1. You may NOT silently switch to \`route=direct\` because the ${route}
   infrastructure failed. Infrastructure failure ≠ permission to answer
   from memory.

2. If the ${route} call genuinely failed, your response MUST:
   a. Say so explicitly ("${route} 실패 — ...").
   b. Report the failure reason as observed.
   c. Either retry (if transient) OR ask the user to re-consent to a
      direct fallback. Do NOT assume consent.

3. If you proceed anyway (user re-consented, or task truly doesn't
   need the committed route), emit a NEW [META: ...] line with the
   updated route and state in plain text why the change is legitimate.

4. Do not 3rd-person-analyze your own prior output as if it came from
   another bot. Own your session history.
</aef_route_commitment>`;
}

export const sessionCommitmentTrackerHook: RegisteredHook<"beforeLLMCall"> = {
  name: "builtin:session-commitment-tracker",
  point: "beforeLLMCall",
  priority: 30,
  blocking: true,
  timeoutMs: 200,
  handler: async ({ messages, tools, system, iteration }, ctx: HookContext) => {
    if (iteration > 0) return { action: "continue" };
    const outstanding = findOutstandingCommitment(messages);
    const fallback = detectFallbackPhrase(messages);
    const hint = buildHint(outstanding, fallback);
    if (!hint) return { action: "continue" };

    ctx.emit({
      type: "rule_check",
      ruleId: "session-commitment-tracker",
      verdict: "pending",
      detail: `route=${outstanding?.route ?? "unknown"} fallback=${fallback}`,
    });

    const nextSystem = system ? `${hint}\n\n${system}` : hint;
    return {
      action: "replace",
      value: { messages, tools, system: nextSystem, iteration },
    };
  },
};
