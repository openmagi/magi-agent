import type { UserMessage } from "../util/types.js";

export interface GoalLoopState {
  missionId: string;
  objective: string;
  turnsUsed: number;
  maxTurns: number;
  paused: boolean;
  cancelled: boolean;
}

export interface GoalRequestInput {
  text: string;
  goalMode?: boolean;
}

export interface GoalRequest {
  objective: string;
  text: string;
}

export interface GoalContinuationInput {
  objective: string;
  missionId: string;
  missionRunId?: string;
  turnsUsed: number;
  maxTurns: number;
  previousAssistantText: string;
  reason: string;
}

export function canContinueGoal(state: GoalLoopState): boolean {
  if (state.paused || state.cancelled) return false;
  return state.turnsUsed < state.maxTurns;
}

export function goalRequestFromMessage(input: GoalRequestInput): GoalRequest | null {
  const text = input.text.trim();
  const slash = text.match(/^\/goal(?:\s+([\s\S]+))?$/i);
  if (slash) {
    const objective = slash[1]?.trim() ?? "";
    return objective ? { objective, text: objective } : null;
  }
  if (input.goalMode === true && text.length > 0) {
    return { objective: text, text };
  }
  return null;
}

export function goalLoopMaxTurns(
  env: { CORE_AGENT_GOAL_MAX_TURNS?: string } = process.env,
): number {
  const raw = env.CORE_AGENT_GOAL_MAX_TURNS;
  const parsed = raw ? Number.parseInt(raw, 10) : NaN;
  if (!Number.isFinite(parsed)) return 5;
  return Math.max(1, Math.min(parsed, 20));
}

export function buildGoalContinuationMessage(
  input: GoalContinuationInput,
): UserMessage {
  const previous = truncateForGoal(input.previousAssistantText, 2000);
  const reason = truncateForGoal(input.reason, 500);
  return {
    text: [
      "Continue working toward this goal.",
      `Goal: ${input.objective}`,
      `Reason to continue: ${reason || "The goal is not complete yet."}`,
      `Progress so far: ${previous || "(no visible assistant text)"}`,
      `Turn budget: ${input.turnsUsed}/${input.maxTurns} used.`,
      "Do the next concrete step now. If the goal is complete, say so clearly.",
    ].join("\n\n"),
    receivedAt: Date.now(),
    metadata: {
      goalMode: true,
      goalContinuation: true,
      goalObjective: input.objective,
      missionKind: "goal",
      missionTitle: input.objective,
      missionId: input.missionId,
      ...(input.missionRunId ? { missionRunId: input.missionRunId } : {}),
      goalTurnsUsed: input.turnsUsed,
      goalMaxTurns: input.maxTurns,
      systemPromptAddendum: [
        "This is an autonomous goal continuation.",
        "Continue the user's goal without asking for confirmation unless external approval or missing information is required.",
        "Use tools when needed and finish with a concise progress/result update.",
      ].join("\n"),
    },
  };
}

function truncateForGoal(value: string, max: number): string {
  const trimmed = value.trim();
  return trimmed.length <= max ? trimmed : `${trimmed.slice(0, max - 3).trimEnd()}...`;
}
