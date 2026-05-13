/**
 * Turn-scope types. Extracted from Turn.ts (R3 refactor) so Turn.ts
 * stays at coordinator scope.
 */

import type { TokenUsage } from "../util/types.js";
import type { RouteDecision } from "../routing/types.js";

export type TurnRoute = "direct" | "subagent" | "pipeline";

export type TurnStatus =
  | "pending"
  | "planning"
  | "executing"
  | "verifying"
  | "committing"
  | "compacting"
  | "committed"
  | "aborted";

export type TurnStopReason =
  | "end_turn"
  | "max_tokens_recovered"
  | "unknown_tool_loop"
  | "iteration_limit"
  | "permission_denied"
  | "permission_timeout"
  | "budget_exceeded"
  | "compaction_impossible"
  | "empty_response_retry_exhausted"
  | "structured_output_retry_exhausted"
  | "aborted";

export interface TurnMeta {
  turnId: string;
  sessionKey: string;
  startedAt: number;
  endedAt?: number;
  declaredRoute: TurnRoute;
  status: TurnStatus;
  usage: TokenUsage;
  stopReason?: TurnStopReason;
  configuredModel?: string;
  effectiveModel?: string;
  routeDecision?: RouteDecision;
}

export interface PlanResult {
  plan: string;
  estimatedSteps: number;
  needsApproval: boolean;
}

export interface VerificationReport {
  ok: boolean;
  violations: string[];
}

export interface TurnResult {
  meta: TurnMeta;
  assistantText: string;
  stopReason: TurnStopReason;
}
