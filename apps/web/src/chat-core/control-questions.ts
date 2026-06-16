import type { ControlRequestRecord } from "./types";

function choicesOf(value: unknown): Array<{ id: string; label: string }> {
  if (!value || typeof value !== "object") return [];
  const choices = (value as { choices?: unknown }).choices;
  if (!Array.isArray(choices)) return [];
  return choices
    .map((choice) => {
      if (!choice || typeof choice !== "object") return null;
      const record = choice as { id?: unknown; label?: unknown };
      if (typeof record.id !== "string") return null;
      return {
        id: record.id,
        label: typeof record.label === "string" ? record.label : record.id,
      };
    })
    .filter((choice): choice is { id: string; label: string } => choice !== null);
}

function isSocialBrowserQuestion(request: ControlRequestRecord): boolean {
  if (request.kind !== "user_question") return false;
  return choicesOf(request.proposedInput).some((choice) =>
    choice.id === "social_browser_connect_instagram" ||
    choice.id === "social_browser_connect_x"
  );
}

export function isNaturalAnswerControlQuestion(
  request: ControlRequestRecord,
): boolean {
  return (
    request.kind === "user_question" &&
    request.state === "pending" &&
    !isSocialBrowserQuestion(request)
  );
}

export function firstNaturalAnswerControlQuestion(
  requests: readonly ControlRequestRecord[] | undefined,
): ControlRequestRecord | null {
  return requests?.find(isNaturalAnswerControlQuestion) ?? null;
}

export function isControlRequestCardRequest(
  request: ControlRequestRecord,
): boolean {
  if (request.kind !== "user_question") return request.state === "pending";
  return request.state === "pending" && isSocialBrowserQuestion(request);
}

export function controlQuestionText(request: ControlRequestRecord): string {
  const choices = choicesOf(request.proposedInput);
  if (choices.length === 0) return request.prompt;
  return [
    request.prompt,
    "",
    ...choices.map((choice) => `- ${choice.label}`),
  ].join("\n");
}
