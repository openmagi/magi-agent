/**
 * CacheOptimizedPrompt — splits a fully-assembled system prompt into
 * static / semi-static / dynamic blocks so the Anthropic prompt cache
 * can reuse the stable prefix across turns.
 *
 * Block layout (order matters for cache hit):
 *   1. STATIC  — SOUL.md, agent-identity, self-model, output rules,
 *                evidence/discipline policy. Unchanged across turns
 *                within a session; cache_control: ephemeral.
 *   2. SEMI-STATIC — tool definitions (via hook), workspace snapshot,
 *                repo map. Stable within a 5-min TTL window;
 *                cache_control: ephemeral.
 *   3. DYNAMIC — session/turn header, memory injection, runtime model
 *                identity, hidden control feedback, addenda. Changes
 *                every turn; no cache_control.
 */

export interface CacheOptimizedPrompt {
  staticSystem: string;
  semiStaticSystem: string;
  dynamicSystem: string;
}

export interface SystemBlock {
  type: "text";
  text: string;
  cache_control?: { type: "ephemeral" };
}

export function toCombinedString(prompt: CacheOptimizedPrompt): string {
  return [prompt.staticSystem, prompt.semiStaticSystem, prompt.dynamicSystem]
    .filter((s) => s.length > 0)
    .join("\n\n");
}

export function toSystemBlocks(prompt: CacheOptimizedPrompt): SystemBlock[] {
  const blocks: SystemBlock[] = [];
  if (prompt.staticSystem.length > 0) {
    blocks.push({
      type: "text",
      text: prompt.staticSystem,
      cache_control: { type: "ephemeral" },
    });
  }
  if (prompt.semiStaticSystem.length > 0) {
    blocks.push({
      type: "text",
      text: prompt.semiStaticSystem,
      cache_control: { type: "ephemeral" },
    });
  }
  if (prompt.dynamicSystem.length > 0) {
    blocks.push({ type: "text", text: prompt.dynamicSystem });
  }
  return blocks;
}

// --- Fence markers used to classify prompt regions ---

const STATIC_FENCES = [
  "<agent-identity",
  "<agent_self_model>",
  "<output-rules>",
  "<runtime-evidence-policy>",
  "<execution-discipline-policy>",
  "<subagent_execution_baseline>",
  "<memory_mode hidden=",
];

const SEMI_STATIC_FENCES = [
  "<workspace_snapshot",
  "<repo_map>",
  "<coding_context",
];

const DYNAMIC_FENCES = [
  "[Session:",
  "[Turn:",
  "[Time:",
  "[Channel:",
  "<memory-context",
  "<memory-root",
  "<runtime_model_identity",
  "<runtime_control_feedback",
  "<reliability-policy",
];

function classifyLine(line: string): "static" | "semiStatic" | "dynamic" {
  for (const fence of DYNAMIC_FENCES) {
    if (line.startsWith(fence)) return "dynamic";
  }
  for (const fence of SEMI_STATIC_FENCES) {
    if (line.startsWith(fence)) return "semiStatic";
  }
  for (const fence of STATIC_FENCES) {
    if (line.startsWith(fence)) return "static";
  }
  return "dynamic";
}

/**
 * Split a fully assembled system prompt (post-hooks) into cache-
 * optimized blocks. Lines between recognized fences inherit the
 * classification of the most recent fence. Unrecognized lines before
 * any fence fall into `dynamic` (session header is usually first).
 *
 * The result is three concatenated strings; callers convert to
 * Anthropic system blocks via {@link toSystemBlocks}.
 */
export function splitForCacheOptimization(
  system: string,
): CacheOptimizedPrompt {
  const staticLines: string[] = [];
  const semiStaticLines: string[] = [];
  const dynamicLines: string[] = [];

  let currentBucket: "static" | "semiStatic" | "dynamic" = "dynamic";
  let inFencedBlock = false;
  let fenceDepth = 0;

  for (const line of system.split("\n")) {
    const trimmed = line.trimStart();

    if (!inFencedBlock) {
      const classification = classifyLine(trimmed);
      if (classification !== "dynamic" || isFenceOpen(trimmed)) {
        currentBucket = classification;
        if (isFenceOpen(trimmed)) {
          inFencedBlock = true;
          fenceDepth = 1;
        }
      }
    } else {
      if (isFenceOpen(trimmed)) fenceDepth += 1;
      if (isFenceClose(trimmed)) {
        fenceDepth -= 1;
        if (fenceDepth <= 0) {
          inFencedBlock = false;
          pushLine(currentBucket, line, staticLines, semiStaticLines, dynamicLines);
          currentBucket = "dynamic";
          continue;
        }
      }
    }

    pushLine(currentBucket, line, staticLines, semiStaticLines, dynamicLines);
  }

  return {
    staticSystem: staticLines.join("\n").trim(),
    semiStaticSystem: semiStaticLines.join("\n").trim(),
    dynamicSystem: dynamicLines.join("\n").trim(),
  };
}

function pushLine(
  bucket: "static" | "semiStatic" | "dynamic",
  line: string,
  staticLines: string[],
  semiStaticLines: string[],
  dynamicLines: string[],
): void {
  switch (bucket) {
    case "static":
      staticLines.push(line);
      break;
    case "semiStatic":
      semiStaticLines.push(line);
      break;
    case "dynamic":
      dynamicLines.push(line);
      break;
  }
}

function isFenceOpen(line: string): boolean {
  return /^<[a-z_-]+[\s>]/.test(line) && !line.startsWith("</");
}

function isFenceClose(line: string): boolean {
  return /^<\/[a-z_-]+>/.test(line);
}
