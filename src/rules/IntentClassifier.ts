/**
 * IntentClassifier — cheap Haiku classifier that maps a user message
 * to a set of intent tags. Design reference: §9.8 P2.
 *
 * The classifier is ONLY invoked when the bot has at least one skill
 * with declared tags — if every tool is core, there's nothing to
 * filter and we skip the latency cost.
 */

import type { LLMClient } from "../transport/LLMClient.js";

export interface ClassifyOptions {
  /** Per-call timeout. Default 3000ms. */
  timeoutMs?: number;
}

export class IntentClassifier {
  /**
   * Cache keyed by `<tagList>|<userMessage>` so repeat turns with
   * the same text (retry loops, multi-channel echo) don't re-classify.
   */
  private readonly cache = new Map<string, { tags: string[]; at: number }>();
  private static readonly CACHE_TTL_MS = 60_000;

  constructor(private readonly llm: LLMClient) {}

  /**
   * Classify `message` into one or more of `availableTags`. Returns
   * `["general"]` when the model can't decide or nothing matches —
   * callers treat `"general"` as "include all skills".
   */
  async classify(
    message: string,
    availableTags: string[],
    opts: ClassifyOptions = {},
  ): Promise<string[]> {
    if (availableTags.length === 0) return ["general"];

    const cacheKey = `${availableTags.sort().join(",")}|${message}`;
    const hit = this.cache.get(cacheKey);
    if (hit && Date.now() - hit.at < IntentClassifier.CACHE_TTL_MS) {
      return hit.tags;
    }

    const timeoutMs = opts.timeoutMs ?? 3_000;
    const system = [
      "You classify a user message into intent tags.",
      `Available tags: ${availableTags.join(", ")}`,
      `Plus the fallback tag: general`,
      "",
      "Rules:",
      "- Respond with comma-separated tags only, no explanation, no quotes.",
      "- Use 1-3 tags max. Prefer fewer.",
      "- Use `general` ONLY when no listed tag clearly applies.",
      "- Never invent tags not in the list.",
      "",
      'Examples:\nuser: "부동산 경매 찾아줘" → legal, realestate',
      'user: "동전 던져줘" → random, utility',
      'user: "안녕" → general',
    ].join("\n");

    const deadline = Date.now() + timeoutMs;
    let output = "";

    try {
      const stream = this.llm.stream({
        model: "claude-haiku-4-5",
        system,
        messages: [{ role: "user", content: message.slice(0, 2000) }],
        max_tokens: 40,
        temperature: 0,
      });
      for await (const evt of stream) {
        if (Date.now() > deadline) break;
        if (evt.kind === "text_delta") output += evt.delta;
        if (evt.kind === "message_end" || evt.kind === "error") break;
      }
    } catch {
      // Classifier failure → fallback to general. Turn continues.
      return ["general"];
    }

    const tags = parseTags(output, availableTags);
    const result = tags.length > 0 ? tags : ["general"];
    this.cache.set(cacheKey, { tags: result, at: Date.now() });
    return result;
  }
}

function parseTags(raw: string, allowed: string[]): string[] {
  const allowSet = new Set([...allowed.map((t) => t.toLowerCase()), "general"]);
  const tokens = raw
    .toLowerCase()
    .replace(/[`"'*]/g, "")
    .split(/[,\n;]+/)
    .map((t) => t.trim())
    .filter((t) => t.length > 0 && allowSet.has(t));
  // Dedup preserving order.
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of tokens) {
    if (!seen.has(t)) {
      seen.add(t);
      out.push(t);
    }
  }
  return out;
}

/**
 * Filter + rank tools per §9.8 P2/P3:
 *   1. Core tools always included.
 *   2. Skill tools: keep those whose tags intersect the classified
 *      intent. "general" matches all skills.
 *   3. Cap total at `maxTotal`. Skills trimmed first by lexical order
 *      (Phase 2b MVP — Phase 2c+ will rank by compliance-log success).
 */
export interface FilterInput {
  coreTools: Array<{ name: string }>;
  skillTools: Array<{ name: string; tags?: string[] }>;
  intentTags: string[];
  maxTotal?: number;
}

export function filterToolsByIntent<
  T extends { name: string; tags?: string[]; kind?: "core" | "skill" },
>(tools: T[], intentTags: string[], maxTotal = 15): T[] {
  const core = tools.filter((t) => t.kind !== "skill");
  const skills = tools.filter((t) => t.kind === "skill");

  const selectedSkills =
    intentTags.includes("general") || intentTags.length === 0
      ? skills
      : skills.filter((s) => {
          const tags = (s.tags ?? []).map((t) => t.toLowerCase());
          return intentTags.some((it) => tags.includes(it.toLowerCase()));
        });

  const budget = Math.max(0, maxTotal - core.length);
  // Deterministic ordering so same inputs → same output in tests.
  const ranked = [...selectedSkills].sort((a, b) => a.name.localeCompare(b.name));
  return [...core, ...ranked.slice(0, budget)];
}
