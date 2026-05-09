"use client";

import { useState } from "react";
import type {
  ControlRequestDecision,
  ControlRequestRecord,
  ControlRequestResponse,
} from "@/lib/chat/types";

interface ControlRequestCardProps {
  request: ControlRequestRecord;
  onRespond: (
    request: ControlRequestRecord,
    response: ControlRequestResponse,
  ) => Promise<void> | void;
}

function inputPreview(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  try {
    return JSON.stringify(value, null, 2).slice(0, 1200);
  } catch {
    return String(value).slice(0, 1200);
  }
}

function choicesOf(value: unknown): Array<{ id: string; label: string }> {
  if (!value || typeof value !== "object") return [];
  const choices = (value as { choices?: unknown }).choices;
  if (!Array.isArray(choices)) return [];
  return choices
    .map((choice) => {
      if (!choice || typeof choice !== "object") return null;
      const obj = choice as { id?: unknown; label?: unknown };
      if (typeof obj.id !== "string") return null;
      return {
        id: obj.id,
        label: typeof obj.label === "string" ? obj.label : obj.id,
      };
    })
    .filter((choice): choice is { id: string; label: string } => choice !== null);
}

export function ControlRequestCard({ request, onRespond }: ControlRequestCardProps) {
  const [feedback, setFeedback] = useState("");
  const [answer, setAnswer] = useState("");
  const [selectedChoice, setSelectedChoice] = useState("");
  const [inputText, setInputText] = useState(() => inputPreview(request.proposedInput) ?? "");
  const [inputError, setInputError] = useState("");
  const [busy, setBusy] = useState<ControlRequestDecision | null>(null);
  const preview = inputPreview(request.proposedInput);
  const pending = request.state === "pending";
  const isQuestion = request.kind === "user_question";
  const isToolPermission = request.kind === "tool_permission";
  const choices = choicesOf(request.proposedInput);

  const submit = async (decision: ControlRequestDecision) => {
    let updatedInput: unknown;
    setInputError("");
    if (decision === "approved" && isToolPermission && inputText.trim()) {
      try {
        updatedInput = JSON.parse(inputText);
      } catch (err) {
        setInputError(err instanceof Error ? err.message : "Invalid JSON");
        return;
      }
    }
    setBusy(decision);
    try {
      await onRespond(request, {
        decision,
        ...(feedback.trim() ? { feedback: feedback.trim() } : {}),
        ...(updatedInput !== undefined ? { updatedInput } : {}),
        ...(decision === "answered" && (selectedChoice || answer.trim())
          ? { answer: selectedChoice || answer.trim() }
          : {}),
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="my-3 flex justify-start">
      <div className="w-full max-w-2xl rounded-lg border border-black/10 bg-white px-4 py-3 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-xs font-medium uppercase text-secondary/50">
              {request.kind.replace("_", " ")}
            </div>
            <div className="mt-1 text-sm font-medium text-foreground">
              {request.prompt}
            </div>
          </div>
          <span className="rounded-md bg-black/[0.04] px-2 py-1 text-xs text-secondary/70">
            {request.state}
          </span>
        </div>

        {preview && !pending && (
          <pre className="mt-3 max-h-48 overflow-auto rounded-md bg-black/[0.035] p-3 text-xs text-secondary/80">
            {preview}
          </pre>
        )}

        {pending && (
          <>
            {isQuestion && (
              <>
                {choices.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {choices.map((choice) => (
                      <button
                        key={choice.id}
                        type="button"
                        onClick={() => setSelectedChoice(choice.id)}
                        className={`rounded-md border px-3 py-2 text-sm font-medium ${
                          selectedChoice === choice.id
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-black/10 text-secondary/80"
                        }`}
                      >
                        {choice.label}
                      </button>
                    ))}
                  </div>
                )}
                <textarea
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  className="mt-3 min-h-20 w-full resize-y rounded-md border border-black/10 bg-white px-3 py-2 text-sm outline-none focus:border-primary"
                  placeholder="Answer"
                />
              </>
            )}
            {isToolPermission && preview && (
              <>
                <textarea
                  value={inputText}
                  onChange={(event) => setInputText(event.target.value)}
                  className="mt-3 min-h-32 w-full resize-y rounded-md border border-black/10 bg-white px-3 py-2 font-mono text-xs outline-none focus:border-primary"
                  spellCheck={false}
                />
                {inputError && (
                  <div className="mt-2 text-xs text-red-600">{inputError}</div>
                )}
              </>
            )}
            <textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              className="mt-3 min-h-16 w-full resize-y rounded-md border border-black/10 bg-white px-3 py-2 text-sm outline-none focus:border-primary"
              placeholder="Feedback"
            />
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              {isQuestion ? (
                <button
                  type="button"
                  disabled={busy !== null || (!selectedChoice && !answer.trim())}
                  onClick={() => void submit("answered")}
                  className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
                >
                  Answer
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void submit("denied")}
                    className="rounded-md border border-black/10 px-3 py-2 text-sm font-medium text-secondary/80 disabled:opacity-40"
                  >
                    Deny
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void submit("approved")}
                    className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
                  >
                    Approve
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
