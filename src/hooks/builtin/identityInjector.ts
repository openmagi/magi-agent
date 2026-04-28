/**
 * Built-in identity-injector hook — T3-17 (Phase 3).
 *
 * Design reference:
 * - `docs/plans/2026-04-19-core-agent-phase-3-plan.md` §5 / T3-17
 * - `docs/plans/2026-04-19-clawy-core-agent-design.md` §7.12.c
 *   (identity fencing format)
 *
 * OpenClaw's SOUL.md was a bot-authored prompt convention — hackable
 * and self-mutable. This hook implements the user-authored equivalent
 * for Clawy: the bot operator defines role / hard rules / methodology
 * in three workspace files and we prepend them to the system prompt on
 * every first iteration of a turn.
 *
 * Compaction-proof: because we re-inject on every turn's first
 * iteration, compaction can't erase the user's contract with the bot.
 *
 * Bot-tamper-proof: the three files live inside the default
 * `sealed_files` set (T3-12), so the bot cannot silently rewrite its
 * own rules.
 *
 * Files read from `workspace/`:
 *   - `identity.md` — role / who the bot is
 *   - `rules.md` — hard rules / code of conduct (MUST-follow)
 *   - `soul.md` — methodology / values (lowercase; optional)
 *
 * A missing file is skipped silently. If all three are missing the
 * hook is a no-op. At least one file must exist for injection.
 *
 * Total content cap: {@link MAX_CHARS}. When the concatenated source
 * exceeds the cap, the LARGEST of the three sections is truncated
 * (suffix `... [truncated]`) until the total fits. Smaller sections
 * are left intact so short rules/identity never get clipped.
 *
 * Toggle:
 *  - `CORE_AGENT_IDENTITY_INJECTION=off` (env) disables globally.
 *  - `workspace/agent.config.yaml: identity_injection: off` per-bot.
 *  - `disable_builtin_hooks: [builtin:identity-injector]` per-bot.
 */

import fs from "node:fs/promises";
import crypto from "node:crypto";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import type { RegisteredHook, HookContext } from "../types.js";

/** Total character budget for the three sections' raw content. */
export const MAX_CHARS = 5_000;

/** Section keys in fixed output order. */
type SectionKey = "identity" | "rules" | "soul";

interface SectionSpec {
  key: SectionKey;
  filename: string;
  heading: string;
}

const SECTIONS: readonly SectionSpec[] = [
  { key: "identity", filename: "identity.md", heading: "# Role" },
  { key: "rules", filename: "rules.md", heading: "# Rules (MUST follow)" },
  { key: "soul", filename: "soul.md", heading: "# Methodology" },
];

const TRUNCATION_SUFFIX = "... [truncated]";

function isEnabledByEnv(): boolean {
  const raw = process.env.CORE_AGENT_IDENTITY_INJECTION;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  if (v === "" || v === "on" || v === "true" || v === "1") return true;
  return false;
}

/**
 * Read `workspace/agent.config.yaml` and check for `identity_injection`.
 * Returns `true` if missing / unreadable (default on). Returns `false`
 * only if the key is explicitly off/false/0/disabled/no.
 */
async function isEnabledByWorkspaceConfig(
  workspaceRoot: string | undefined,
): Promise<boolean> {
  if (!workspaceRoot) return true;
  const configPath = path.join(workspaceRoot, "agent.config.yaml");
  let raw: string;
  try {
    raw = await fs.readFile(configPath, "utf8");
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return true;
    return true;
  }
  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch {
    return true;
  }
  if (!parsed || typeof parsed !== "object") return true;
  const val = (parsed as Record<string, unknown>)["identity_injection"];
  if (val === undefined || val === null) return true;
  if (typeof val === "boolean") return val;
  if (typeof val === "string") {
    const v = val.trim().toLowerCase();
    if (v === "off" || v === "false" || v === "0" || v === "disabled" || v === "no") {
      return false;
    }
  }
  return true;
}

async function readSectionFile(
  workspaceRoot: string,
  filename: string,
): Promise<string | null> {
  const full = path.join(workspaceRoot, filename);
  try {
    const raw = await fs.readFile(full, "utf8");
    const trimmed = raw.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    // ENOENT or any other read error — skip silently.
    return null;
  }
}

export interface LoadedSections {
  identity: string | null;
  rules: string | null;
  soul: string | null;
}

export async function loadSections(workspaceRoot: string): Promise<LoadedSections> {
  const [identity, rules, soul] = await Promise.all([
    readSectionFile(workspaceRoot, "identity.md"),
    readSectionFile(workspaceRoot, "rules.md"),
    readSectionFile(workspaceRoot, "soul.md"),
  ]);
  return { identity, rules, soul };
}

/**
 * Enforce the total-chars cap by iteratively truncating the largest
 * present section until the total fits. Each truncation replaces the
 * tail of the section with TRUNCATION_SUFFIX. If the largest section
 * cannot be truncated smaller than the suffix, we give up on that
 * section and mark it truncated wholesale.
 *
 * Pure-function; does not mutate its input.
 */
export function enforceCap(
  loaded: LoadedSections,
  maxChars: number = MAX_CHARS,
): LoadedSections {
  const out: LoadedSections = {
    identity: loaded.identity,
    rules: loaded.rules,
    soul: loaded.soul,
  };
  const keys: SectionKey[] = ["identity", "rules", "soul"];
  const totalChars = (): number =>
    keys.reduce((acc, k) => acc + (out[k] ? (out[k] as string).length : 0), 0);

  // Hard safety cap — don't loop forever on pathological inputs.
  for (let i = 0; i < 32 && totalChars() > maxChars; i++) {
    // Pick the largest section.
    let largestKey: SectionKey | null = null;
    let largestLen = -1;
    for (const k of keys) {
      const v = out[k];
      if (v && v.length > largestLen) {
        largestLen = v.length;
        largestKey = k;
      }
    }
    if (!largestKey) break;
    const currentLargest = out[largestKey] as string;
    const excess = totalChars() - maxChars;
    // Target length for this section to bring total into budget.
    const target = Math.max(0, currentLargest.length - excess - TRUNCATION_SUFFIX.length);
    if (target <= 0) {
      // Cannot truncate meaningfully — mark wholesale.
      out[largestKey] = TRUNCATION_SUFFIX;
    } else {
      out[largestKey] = currentLargest.slice(0, target).trimEnd() + TRUNCATION_SUFFIX;
    }
  }
  return out;
}

export interface BuiltIdentityBlock {
  fence: string;
  revision: string;
  sections: SectionKey[];
  bytes: number;
}

/**
 * Build the `<agent-identity>` fenced block. Returns null when no
 * section is present. `revision` is the first 8 hex chars of the
 * sha256 over the raw concatenated source (pre-fence) — lets the UI /
 * audit correlate identity edits.
 */
export function buildIdentityFence(loaded: LoadedSections): BuiltIdentityBlock | null {
  const capped = enforceCap(loaded);
  const included: { key: SectionKey; heading: string; body: string }[] = [];
  for (const spec of SECTIONS) {
    const body = capped[spec.key];
    if (body && body.length > 0) {
      included.push({ key: spec.key, heading: spec.heading, body });
    }
  }
  if (included.length === 0) return null;

  // Revision hash: sha256 over `<key>\n<body>\n\n` joined.
  const hashInput = included.map((s) => `${s.key}\n${s.body}\n\n`).join("");
  const revision = crypto
    .createHash("sha256")
    .update(hashInput, "utf8")
    .digest("hex")
    .slice(0, 8);

  const bodyParts = included.map((s) => `${s.heading}\n${s.body}`);
  const header = `<agent-identity source="user" revision="${revision}">`;
  const footer = `</agent-identity>`;
  const fence = `${header}\n${bodyParts.join("\n\n")}\n${footer}`;

  return {
    fence,
    revision,
    sections: included.map((s) => s.key),
    bytes: Buffer.byteLength(fence, "utf8"),
  };
}

export interface IdentityInjectorOptions {
  workspaceRoot?: string;
}

export function makeIdentityInjectorHook(
  opts: IdentityInjectorOptions = {},
): RegisteredHook<"beforeLLMCall"> {
  return {
    name: "builtin:identity-injector",
    point: "beforeLLMCall",
    priority: 1,
    blocking: true,
    timeoutMs: 2_000,
    handler: async ({ messages, tools, system, iteration }, ctx: HookContext) => {
      // Only inject on the first iteration of a turn — follow-up
      // iterations already carry the block in `system`.
      if (iteration > 0) return { action: "continue" };

      // Env toggle (global).
      if (!isEnabledByEnv()) return { action: "continue" };

      // No workspace → nothing to read.
      if (!opts.workspaceRoot) return { action: "continue" };

      // Workspace config override.
      const fileEnabled = await isEnabledByWorkspaceConfig(opts.workspaceRoot);
      if (!fileEnabled) return { action: "continue" };

      const loaded = await loadSections(opts.workspaceRoot);
      if (!loaded.identity && !loaded.rules && !loaded.soul) {
        return { action: "continue" };
      }

      const built = buildIdentityFence(loaded);
      if (!built) return { action: "continue" };

      ctx.emit({
        type: "rule_check",
        ruleId: "identity-injector",
        verdict: "ok",
        detail: `identity_injected revision=${built.revision} bytes=${built.bytes} sections=${built.sections.join(",")}`,
      });

      ctx.log("info", "[identityInjector] identity_injected", {
        revision: built.revision,
        bytes: built.bytes,
        sections: built.sections,
      });

      const nextSystem = system ? `${built.fence}\n\n${system}` : built.fence;
      return {
        action: "replace",
        value: { messages, tools, system: nextSystem, iteration },
      };
    },
  };
}
