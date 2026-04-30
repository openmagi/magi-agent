/**
 * Slash-command registry — built-in `/compact`, `/reset`, `/status` and
 * any future additions. Ported from legacy gateway so bots migrated to
 * clawy-core-agent keep the same `/foo` UX.
 *
 * Interception point: {@link Session.runTurn} calls
 * {@link matchSlashCommand} at the top of the mutex-protected turn
 * flow. If the incoming user message matches a registered command,
 * the handler runs and the Turn LLM path is skipped entirely (no
 * Turn object is constructed, no tokens are spent).
 *
 * Design rules (v1):
 *   - Matching is case-sensitive + lowercase only (`/compact` yes,
 *     `/COMPACT` no).
 *   - Leading + trailing whitespace on the user message is trimmed
 *     before matching.
 *   - Match succeeds only when the message is exactly the command
 *     token OR `<command> <args…>`. `/compacting` does not match
 *     `/compact`.
 *   - Aliases live on the same SlashCommand record and dispatch to
 *     the same handler.
 *   - Unknown slash commands (starting with `/` but not registered)
 *     fall through — the Turn runs normally. This preserves forward
 *     compatibility with skills that may use `/foo` as a literal
 *     prompt fragment.
 */

import type { Session } from "../Session.js";
import type { SseWriter } from "../transport/SseWriter.js";

export interface SlashCommandContext {
  session: Session;
  sse: SseWriter;
}

export interface SlashCommand {
  /** Canonical name including the leading slash, e.g. `/compact`. */
  name: string;
  /** Optional aliases (e.g. `/compress` → `/compact`). Same rules. */
  aliases?: string[];
  /** One-line summary — surfaced by `/status` and future `/help`. */
  description?: string;
  /**
   * Handler. `args` is the verbatim substring after the command token
   * (leading space stripped). For `/reset yes` args is `"yes"`.
   */
  handler: (args: string, ctx: SlashCommandContext) => Promise<void>;
}

export class SlashCommandRegistry {
  private readonly byName = new Map<string, SlashCommand>();

  /**
   * Register a command. Throws on duplicate registration (either via
   * `name` or any `alias`) so silent shadowing bugs surface at boot
   * rather than at dispatch time.
   */
  register(cmd: SlashCommand): void {
    if (this.byName.has(cmd.name)) {
      throw new Error(`slash command already registered: ${cmd.name}`);
    }
    this.byName.set(cmd.name, cmd);
    for (const alias of cmd.aliases ?? []) {
      if (this.byName.has(alias)) {
        throw new Error(`slash command alias already registered: ${alias}`);
      }
      this.byName.set(alias, cmd);
    }
  }

  /** Return the command registered under `name`, or null. */
  resolve(name: string): SlashCommand | null {
    return this.byName.get(name) ?? null;
  }

  /**
   * Unique list of registered commands (deduped across aliases).
   * Handy for `/status` and future `/help` output.
   */
  list(): SlashCommand[] {
    const seen = new Set<SlashCommand>();
    for (const cmd of this.byName.values()) seen.add(cmd);
    return [...seen];
  }
}

export interface SlashMatch {
  command: SlashCommand;
  args: string;
}

/**
 * Try to match a raw user message against the registry. Returns the
 * matched command + trailing args, or null to indicate "not a slash
 * command, fall through to the LLM turn".
 *
 * Whitespace: the input is trimmed before matching, but the `args`
 * returned preserves original interior spacing (only the separator
 * space between token and args is dropped).
 */
export function matchSlashCommand(
  text: string,
  registry: SlashCommandRegistry,
): SlashMatch | null {
  const trimmed = text.trim();
  if (trimmed.length === 0) return null;
  if (trimmed[0] !== "/") return null;

  // Split token vs args. We accept either bare (`/compact`) or
  // whitespace-separated (`/reset yes`). Tabs + multi-space collapsed
  // on the first separator only.
  const spaceIdx = trimmed.search(/\s/);
  const token = spaceIdx === -1 ? trimmed : trimmed.slice(0, spaceIdx);
  const args = spaceIdx === -1 ? "" : trimmed.slice(spaceIdx + 1).trim();

  // v1 constraint: case-sensitive lowercase only. `/COMPACT` is NOT
  // the same as `/compact`. This keeps things simple; a future v2 can
  // add a normaliser.
  if (token !== token.toLowerCase()) return null;

  const cmd = registry.resolve(token);
  if (!cmd) return null;
  return { command: cmd, args };
}
