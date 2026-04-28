/**
 * Superpowers slash commands — `/plan`, `/onboarding`, and
 * `/superpowers:*` namespace (one per bundled skill).
 *
 * Design reference:
 *   docs/plans/2026-04-20-superpowers-plugin-design.md
 *
 * Source skills live under
 *   infra/docker/clawy-core-agent/skills/superpowers/<name>/SKILL.md
 *
 * The skills themselves are loaded as Phase 2b prompt-only tools by the
 * SkillLoader during Agent.start(). The slash commands here are thin
 * convenience entry points: when a user types `/plan` or
 * `/superpowers:brainstorming`, we emit a synthetic assistant text that
 * surfaces the skill body (or a pointer) so the conversation can
 * proceed with the skill context "loaded". This bypasses the LLM path
 * entirely — slash commands are non-billed, non-LLM (same contract as
 * `/compact /reset /status`).
 *
 * Naming: full `/superpowers:*` only, no aliases (per Kevin 2026-04-20
 * decision). `/plan` and `/onboarding` are independent top-level slash
 * commands (not under the `/superpowers:` namespace).
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { SlashCommand, SlashCommandContext } from "./registry.js";
import type { SseWriter } from "../transport/SseWriter.js";

/** Catalogue of bundled superpowers skills — one entry per directory in
 *  `skills/superpowers/`. Order drives `/status` listing and
 *  autocomplete ordering in the web/mobile clients. */
export const SUPERPOWERS_SKILLS = [
  "brainstorming",
  "dispatching-parallel-agents",
  "executing-plans",
  "finishing-a-development-branch",
  "receiving-code-review",
  "requesting-code-review",
  "subagent-driven-development",
  "systematic-debugging",
  "test-driven-development",
  "using-git-worktrees",
  "using-superpowers",
  "verification-before-completion",
  "writing-plans",
  "writing-skills",
] as const;

export type SuperpowersSkill = (typeof SUPERPOWERS_SKILLS)[number];

/** Write a synthetic assistant text on the `event: agent` SSE channel.
 *  Mirrors the built-in `/compact /reset /status` single-channel emit —
 *  see `slash/commands.ts` + `turn/LLMStreamReader.ts` for the dual-emit
 *  regression context. */
function emitText(sse: SseWriter, text: string): void {
  sse.agent({ type: "text_delta", delta: text });
}

/**
 * Read the SKILL.md body for a bundled skill. Returns null when the
 * directory is missing or unreadable — the slash handler falls back to
 * a short pointer text so a broken install never wedges the user's
 * turn.
 */
export async function readSkillBody(
  skillsRoot: string,
  skillName: string,
): Promise<string | null> {
  const md = path.join(skillsRoot, skillName, "SKILL.md");
  try {
    return await fs.readFile(md, "utf8");
  } catch {
    return null;
  }
}

/**
 * Build the `/plan` command. Independent top-level slash (NOT
 * `/superpowers:plan`). Dispatches the writing-plans skill body as the
 * synthetic assistant text.
 */
export function makePlanCommand(skillsRoot: string): SlashCommand {
  return {
    name: "/plan",
    description:
      "Load the writing-plans superpower and kick off a numbered implementation plan.",
    async handler(_args: string, ctx: SlashCommandContext): Promise<void> {
      const body = await readSkillBody(skillsRoot, "writing-plans");
      if (body) {
        emitText(
          ctx.sse,
          `[Skill activated: superpowers:writing-plans]\n\n${body}`,
        );
      } else {
        emitText(
          ctx.sse,
          "[Skill activated: superpowers:writing-plans]\n\nUse this skill to turn a spec into a numbered implementation plan.",
        );
      }
    },
  };
}

/**
 * Build the `/onboarding` command. Independent top-level slash. Emits
 * the using-superpowers skill body as the intro scaffold. Per the
 * design doc, the "2분 온보딩" nudge is delivered by the
 * `onboardingNeededCheck` beforeTurnStart hook — this slash is the
 * manual/explicit entry point.
 */
export function makeOnboardingCommand(skillsRoot: string): SlashCommand {
  return {
    name: "/onboarding",
    description:
      "Run the 2-minute onboarding walkthrough to populate identity / soul / user context.",
    async handler(_args: string, ctx: SlashCommandContext): Promise<void> {
      const body = await readSkillBody(skillsRoot, "using-superpowers");
      const intro =
        "✅ 온보딩 시작합니다. 2분이면 됩니다. 몇 가지 질문에 답해주시면, 앞으로 훨씬 잘 도와드릴 수 있어요.\n\n";
      if (body) {
        emitText(
          ctx.sse,
          `${intro}[Skill activated: superpowers:using-superpowers]\n\n${body}`,
        );
      } else {
        emitText(ctx.sse, `${intro}[Skill activated: superpowers:using-superpowers]`);
      }
    },
  };
}

/**
 * Build a single `/superpowers:<name>` slash command for the given
 * bundled skill. Handler reads SKILL.md and emits it as synthetic
 * assistant text. Fails open — missing SKILL.md surfaces a short
 * pointer text instead of wedging the turn.
 */
export function makeSuperpowersSkillCommand(
  skillsRoot: string,
  skillName: SuperpowersSkill,
): SlashCommand {
  return {
    name: `/superpowers:${skillName}`,
    description: `Load the superpowers:${skillName} skill.`,
    async handler(_args: string, ctx: SlashCommandContext): Promise<void> {
      const body = await readSkillBody(skillsRoot, skillName);
      if (body) {
        emitText(
          ctx.sse,
          `[Skill activated: superpowers:${skillName}]\n\n${body}`,
        );
      } else {
        emitText(
          ctx.sse,
          `[Skill activated: superpowers:${skillName}] (body not bundled — please reinstall).`,
        );
      }
    },
  };
}

/**
 * Build every `/superpowers:<name>` slash command. Returns the full list
 * in catalogue order so Agent.ctor can register them with a single
 * loop.
 */
export function makeAllSuperpowersSkillCommands(
  skillsRoot: string,
): SlashCommand[] {
  return SUPERPOWERS_SKILLS.map((name) =>
    makeSuperpowersSkillCommand(skillsRoot, name),
  );
}
