import type { UserMessage } from "../util/types.js";
import type { LLMClient } from "../transport/LLMClient.js";

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

export interface GoalSpec {
  title: string;
  objective: string;
  completionCriteria: string[];
}

export interface GoalContinuationInput {
  objective: string;
  title?: string;
  completionCriteria?: string[];
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

function fallbackGoalTitle(raw: string): string {
  const normalized = raw.replace(/\s+/g, " ").trim();
  if (!normalized) return "Goal mission";
  const sentenceEnd = normalized.search(/[.!?\n。！？]/);
  const firstSentence = sentenceEnd > 0 ? normalized.slice(0, sentenceEnd + 1) : normalized;
  return truncateForGoal(firstSentence, 80);
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => truncateForGoal(item, 160))
    .filter(Boolean)
    .slice(0, 6);
}

export function parseGoalSpecResult(text: string, rawRequest: string): GoalSpec {
  const fallback: GoalSpec = {
    title: fallbackGoalTitle(rawRequest),
    objective: truncateForGoal(rawRequest, 500),
    completionCriteria: ["Deliver a clear completion update for this goal."],
  };
  try {
    const parsed = JSON.parse(text) as {
      title?: unknown;
      objective?: unknown;
      completionCriteria?: unknown;
    };
    const title = typeof parsed.title === "string"
      ? truncateForGoal(parsed.title, 80)
      : "";
    const objective = typeof parsed.objective === "string"
      ? truncateForGoal(parsed.objective, 500)
      : "";
    const completionCriteria = stringArray(parsed.completionCriteria);
    return {
      title: title || fallback.title,
      objective: objective || fallback.objective,
      completionCriteria: completionCriteria.length > 0
        ? completionCriteria
        : fallback.completionCriteria,
    };
  } catch {
    return fallback;
  }
}

export async function distillGoalSpec(input: {
  llm: Pick<LLMClient, "stream">;
  model: string;
  rawRequest: string;
  timeoutMs?: number;
}): Promise<GoalSpec> {
  const timeoutMs = input.timeoutMs ?? 5_000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("goal_spec_timeout")), timeoutMs);
  let text = "";
  try {
    for await (const event of input.llm.stream({
      model: input.model,
      system: [
        "You are a goal mission distiller.",
        "Turn a verbose user request into compact public goal metadata.",
        "Return only JSON with this shape:",
        '{"title":"short title","objective":"one sentence objective","completionCriteria":["concrete done condition"]}',
        "Rules:",
        "- title must be <= 80 characters and must not copy the raw request.",
        "- objective must be one concise sentence.",
        "- completionCriteria must be concrete, user-visible conditions for judging the goal done.",
        "- Preserve the user's language.",
      ].join("\n"),
      messages: [
        {
          role: "user",
          content: `Raw request:\n${input.rawRequest}`,
        },
      ],
      max_tokens: 512,
      temperature: 0,
      thinking: { type: "disabled" },
      signal: controller.signal,
    })) {
      if (event.kind === "text_delta") text += event.delta;
      if (event.kind === "error") return parseGoalSpecResult("", input.rawRequest);
    }
    return parseGoalSpecResult(text, input.rawRequest);
  } catch {
    return parseGoalSpecResult("", input.rawRequest);
  } finally {
    clearTimeout(timer);
  }
}

export function goalLoopMaxTurns(
  env: { CORE_AGENT_GOAL_MAX_TURNS?: string } = process.env,
): number {
  const raw = env.CORE_AGENT_GOAL_MAX_TURNS;
  const parsed = raw ? Number.parseInt(raw, 10) : NaN;
  if (!Number.isFinite(parsed)) return 30;
  return Math.max(1, Math.min(parsed, 50));
}

export function buildGoalContinuationMessage(
  input: GoalContinuationInput,
): UserMessage {
  const previous = truncateForGoal(input.previousAssistantText, 2000);
  const reason = truncateForGoal(input.reason, 500);
  const criteria = (input.completionCriteria?.length
    ? input.completionCriteria
    : ["Deliver a clear completion update for this goal."])
    .map((item) => `- ${truncateForGoal(item, 160)}`)
    .join("\n");
  return {
    text: [
      "Continue working toward this goal.",
      `Goal: ${input.objective}`,
      `Completion criteria:\n${criteria}`,
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
      goalCompletionCriteria: input.completionCriteria?.length
        ? input.completionCriteria
        : ["Deliver a clear completion update for this goal."],
      missionKind: "goal",
      missionTitle: input.title ?? input.objective,
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
  const trimmed = value.replace(/\s+/g, " ").trim();
  return trimmed.length <= max ? trimmed : `${trimmed.slice(0, max - 3).trimEnd()}...`;
}
