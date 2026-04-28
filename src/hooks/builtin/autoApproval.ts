/**
 * Built-in auto-approval hook — T2-08 (`permissionMode = "auto"`).
 *
 * Design reference:
 * - `docs/plans/2026-04-19-core-agent-phase-3-plan.md` §4 / T2-08.
 * - Audit 03 P1 + DEBT-PLAN-PERMS-01.
 *
 * Behaviour:
 *   - In `"default"` mode, tools declared `dangerous: true` ask for
 *     user consent. Non-dangerous tools continue to downstream hooks.
 *   - When the session is in `auto`:
 *       * Tools declared `dangerous: true` return
 *         `{ action: "permission_decision", decision: "ask" }` so the
 *         human is still prompted via the askUser delegate wired by
 *         Turn.ts at the beforeToolUse site (T2-07).
 *       * Everything else returns
 *         `{ action: "permission_decision", decision: "approve" }`.
 *
 * Priority: 30 — the HookRegistry runs pre-hooks in ascending priority
 * order (priority 1 is identity-injector, 5 is memory-injector, 30 is
 * early but AFTER those). Runs BEFORE dangerous_patterns (future
 * T2-09, target priority 40) and selfClaimVerifier (which lives on
 * beforeCommit, priority 80). This ordering means auto-approve is the
 * first voice on tool consent when a session opts in.
 *
 * The hook has no knowledge of the individual tool registry — it uses
 * a small delegate `resolveTool` supplied at construction so we can
 * unit-test without spinning up an Agent.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { Tool } from "../../Tool.js";
import type { Session, PermissionMode } from "../../Session.js";

/**
 * Shape the hook needs from the hosting Agent. Kept as a narrow
 * interface so tests can stub it without constructing the full Agent.
 */
export interface AutoApprovalAgent {
  /**
   * Active permission mode for the session the hook is running in.
   * The hook is declared once globally (not per-session) so the lookup
   * goes through the turn's sessionKey via a registry supplied by the
   * caller.
   */
  getSessionPermissionMode(sessionKey: string): PermissionMode | null;
  /** Resolve a tool by name from the Agent's tool registry. */
  resolveTool(name: string): Tool | null;
}

export interface AutoApprovalOptions {
  agent: AutoApprovalAgent;
}

export function makeAutoApprovalHook(
  opts: AutoApprovalOptions,
): RegisteredHook<"beforeToolUse"> {
  return {
    name: "builtin:auto-approval",
    point: "beforeToolUse",
    // Ascending priority → runs early. 30 is after identity/memory
    // injection (1/5) but before dangerous_patterns (T2-09, target
    // 40) and any user-authored permission hooks (default 100).
    priority: 30,
    blocking: true,
    timeoutMs: 500,
    handler: async ({ toolName }, ctx: HookContext) => {
      const mode = opts.agent.getSessionPermissionMode(ctx.sessionKey);
      // Plan-mode tool filtering and bypass mode are handled at the
      // dispatcher. This hook owns default/auto consent policy only.
      if (mode !== "auto" && mode !== "default") {
        return { action: "continue" };
      }

      const tool = opts.agent.resolveTool(toolName);
      // Unknown tool — refuse to approve. Turn.ts will surface an
      // `unknown tool` error before this runs in the normal code path
      // but belt-and-suspenders: never silently approve something we
      // can't classify.
      if (!tool) {
        if (mode === "default") {
          return { action: "continue" };
        }
        return {
          action: "permission_decision",
          decision: "ask",
          reason: `auto-approval: unknown tool ${toolName}`,
        };
      }

      if (tool.dangerous === true) {
        return {
          action: "permission_decision",
          decision: "ask",
          reason: `auto-approval: ${toolName} is dangerous — confirm before running`,
        };
      }

      if (mode === "default") {
        return { action: "continue" };
      }

      return {
        action: "permission_decision",
        decision: "approve",
        reason: `auto-approval: ${toolName} non-dangerous`,
      };
    },
  };
}

/**
 * Convenience: build the delegate from a live Agent instance. Kept
 * here (not in Agent.ts) so the hook module owns its own wiring and
 * the Agent file stays focused on orchestration.
 */
export function agentToAutoApprovalDelegate(agent: {
  listSessions(): Session[];
  tools: { resolve(name: string): Tool | null };
}): AutoApprovalAgent {
  return {
    getSessionPermissionMode(sessionKey) {
      const s = agent.listSessions().find((x) => x.meta.sessionKey === sessionKey);
      return s ? s.getPermissionMode() : null;
    },
    resolveTool(name) {
      return agent.tools.resolve(name);
    },
  };
}
