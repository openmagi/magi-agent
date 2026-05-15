"use client";

import { useState, type KeyboardEvent, type MouseEvent } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import type {
  ControlRequestDecision,
  ControlRequestRecord,
  ControlRequestResponse,
  PatchPreview,
  PatchPreviewFile,
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

function patchApprovalInput(
  value: unknown,
): { patchPreview?: PatchPreview; previewError?: string } | null {
  if (!value || typeof value !== "object") return null;
  const record = value as {
    toolName?: unknown;
    patchPreview?: unknown;
    previewError?: unknown;
  };
  if (record.toolName !== "PatchApply") return null;
  return {
    ...(isPatchPreview(record.patchPreview)
      ? { patchPreview: record.patchPreview }
      : {}),
    ...(typeof record.previewError === "string"
      ? { previewError: record.previewError }
      : {}),
  };
}

function isPatchPreview(value: unknown): value is PatchPreview {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<PatchPreview>;
  return (
    typeof record.dryRun === "boolean" &&
    Array.isArray(record.changedFiles) &&
    Array.isArray(record.createdFiles) &&
    Array.isArray(record.deletedFiles) &&
    Array.isArray(record.files)
  );
}

function PatchApprovalSummary({
  patchPreview,
  previewError,
}: {
  patchPreview?: PatchPreview;
  previewError?: string;
}) {
  const files = patchPreview?.files ?? [];
  const totalAdded = files.reduce((sum, file) => sum + file.addedLines, 0);
  const totalRemoved = files.reduce((sum, file) => sum + file.removedLines, 0);
  const fileCount = files.length || patchPreview?.changedFiles.length || 0;
  return (
    <div className="mt-3 rounded-md bg-black/[0.035] p-3 text-xs text-secondary/80">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-foreground">Patch preview</span>
        {patchPreview ? (
          <span>
            {fileCount} files · +{totalAdded} -{totalRemoved}
          </span>
        ) : (
          <span>{previewError || "preview unavailable"}</span>
        )}
      </div>
      {files.length > 0 && (
        <div className="mt-2 grid gap-1.5">
          {files.slice(0, 12).map((file) => (
            <PatchFileRow key={file.path} file={file} />
          ))}
          {files.length > 12 && (
            <div className="text-secondary/60">+{files.length - 12} more files</div>
          )}
        </div>
      )}
    </div>
  );
}

function PatchFileRow({ file }: { file: PatchPreviewFile }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="min-w-0 truncate font-mono text-[11px] text-secondary">
        {file.path}
      </span>
      <span className="shrink-0 text-secondary/70">
        {file.operation} · +{file.addedLines} -{file.removedLines}
      </span>
    </div>
  );
}

type SocialProvider = "instagram" | "x";

interface SocialRequestInfo {
  provider: SocialProvider;
  connectChoiceId: string;
  label: string;
}

interface SocialScreenshot {
  contentType?: string;
  imageBase64?: string;
  url?: string;
}

const REMOTE_KEYS = new Set([
  "Backspace",
  "Delete",
  "Enter",
  "Escape",
  "Tab",
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  "PageUp",
  "PageDown",
]);

function socialRequestInfo(request: ControlRequestRecord): SocialRequestInfo | null {
  if (request.kind !== "user_question") return null;
  const choices = choicesOf(request.proposedInput);
  for (const choice of choices) {
    if (choice.id === "social_browser_connect_instagram") {
      return { provider: "instagram", connectChoiceId: choice.id, label: "Instagram" };
    }
    if (choice.id === "social_browser_connect_x") {
      return { provider: "x", connectChoiceId: choice.id, label: "X" };
    }
  }
  return null;
}

function SocialBrowserRequestCard({
  request,
  info,
  onRespond,
}: {
  request: ControlRequestRecord;
  info: SocialRequestInfo;
  onRespond: ControlRequestCardProps["onRespond"];
}) {
  const authFetch = useAuthFetch();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [screenshot, setScreenshot] = useState<SocialScreenshot | null>(null);
  const [busy, setBusy] = useState<"start" | "command" | "continue" | "cancel" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const imageSrc =
    screenshot?.imageBase64 && screenshot.contentType
      ? `data:${screenshot.contentType};base64,${screenshot.imageBase64}`
      : null;

  async function startSession() {
    setBusy("start");
    setError(null);
    try {
      const res = await authFetch("/api/integrations/social-browser/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: info.provider }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Could not open the social browser.");
        return;
      }
      setSessionId(data.session?.sessionId ?? null);
      setScreenshot(data.screenshot ?? null);
    } catch {
      setError("Could not open the social browser.");
    } finally {
      setBusy(null);
    }
  }

  async function sendCommand(command: Record<string, unknown>) {
    if (!sessionId) return;
    setBusy("command");
    setError(null);
    try {
      const res = await authFetch(
        `/api/integrations/social-browser/session/${sessionId}/command`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(command),
        },
      );
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Social browser command failed.");
        return;
      }
      setScreenshot({
        contentType: data.contentType,
        imageBase64: data.imageBase64,
        url: data.url,
      });
    } catch {
      setError("Social browser command failed.");
    } finally {
      setBusy(null);
    }
  }

  function handleScreenshotClick(event: MouseEvent<HTMLImageElement>) {
    if (!sessionId || busy) return;
    const target = event.currentTarget;
    target.parentElement?.focus();
    const rect = target.getBoundingClientRect();
    const scaleX = target.naturalWidth / rect.width;
    const scaleY = target.naturalHeight / rect.height;
    void sendCommand({
      action: "click",
      x: Math.round((event.clientX - rect.left) * scaleX),
      y: Math.round((event.clientY - rect.top) * scaleY),
    });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!sessionId || busy || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key.length === 1) {
      event.preventDefault();
      void sendCommand({ action: "type", text: event.key });
      return;
    }
    if (REMOTE_KEYS.has(event.key)) {
      event.preventDefault();
      void sendCommand({ action: "key", key: event.key });
    }
  }

  async function respond(answer: string, busyState: "continue" | "cancel") {
    setBusy(busyState);
    try {
      await onRespond(request, { decision: "answered", answer });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="my-3 flex justify-start">
      <div className="w-full max-w-2xl rounded-lg border border-black/10 bg-white px-4 py-3 shadow-sm">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="text-xs font-medium uppercase text-secondary/50">
              Social browser
            </div>
            <div className="mt-1 text-sm font-medium text-foreground">
              Connect {info.label}
            </div>
            <p className="mt-1 text-xs text-secondary">
              Passwords stay in the browser session and are not sent to the bot.
            </p>
          </div>
          <span className="rounded-md bg-black/[0.04] px-2 py-1 text-xs text-secondary/70">
            {request.state}
          </span>
        </div>

        {error && (
          <div className="mt-3 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-600">
            {error}
          </div>
        )}

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void startSession()}
            disabled={busy !== null}
            className="rounded-md border border-primary/20 px-3 py-2 text-sm font-medium text-primary disabled:opacity-40"
          >
            {sessionId ? `Restart ${info.label}` : `Open ${info.label}`}
          </button>
          {sessionId && (
            <button
              type="button"
              onClick={() => void sendCommand({ action: "screenshot" })}
              disabled={busy !== null}
              className="rounded-md border border-black/10 px-3 py-2 text-sm font-medium text-secondary/80 disabled:opacity-40"
            >
              Refresh
            </button>
          )}
        </div>

        {sessionId && (
          <div
            tabIndex={0}
            onKeyDown={handleKeyDown}
            className="mt-3 overflow-hidden rounded-lg border border-black/[0.08] bg-white outline-none focus:ring-2 focus:ring-primary/20"
          >
            <div className="border-b border-black/[0.06] px-2.5 py-1.5 text-[11px] text-secondary">
              {screenshot?.url || info.label}
            </div>
            {imageSrc ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={imageSrc}
                alt={`${info.label} browser preview`}
                onClick={handleScreenshotClick}
                className="block aspect-video w-full cursor-crosshair object-contain"
              />
            ) : (
              <div className="flex aspect-video items-center justify-center text-[11px] text-secondary">
                {busy === "command" ? "..." : info.label}
              </div>
            )}
          </div>
        )}

        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void respond("social_browser_cancel", "cancel")}
            className="rounded-md border border-black/10 px-3 py-2 text-sm font-medium text-secondary/80 disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy !== null || !sessionId}
            onClick={() => void respond(info.connectChoiceId, "continue")}
            className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            Continue after login
          </button>
        </div>
      </div>
    </div>
  );
}

export function ControlRequestCard({ request, onRespond }: ControlRequestCardProps) {
  const patchApproval = patchApprovalInput(request.proposedInput);
  const [feedback, setFeedback] = useState("");
  const [answer, setAnswer] = useState("");
  const [selectedChoice, setSelectedChoice] = useState("");
  const [inputText, setInputText] = useState(() =>
    patchApproval ? "" : inputPreview(request.proposedInput) ?? "",
  );
  const [inputError, setInputError] = useState("");
  const [busy, setBusy] = useState<ControlRequestDecision | null>(null);
  const preview = patchApproval ? null : inputPreview(request.proposedInput);
  const pending = request.state === "pending";
  const isQuestion = request.kind === "user_question";
  const isToolPermission = request.kind === "tool_permission";
  const choices = choicesOf(request.proposedInput);
  const socialInfo = socialRequestInfo(request);

  if (pending && socialInfo) {
    return (
      <SocialBrowserRequestCard
        request={request}
        info={socialInfo}
        onRespond={onRespond}
      />
    );
  }

  const submit = async (decision: ControlRequestDecision) => {
    let updatedInput: unknown;
    setInputError("");
    if (decision === "approved" && isToolPermission && !patchApproval && inputText.trim()) {
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
        {patchApproval && (
          <PatchApprovalSummary
            patchPreview={patchApproval.patchPreview}
            previewError={patchApproval.previewError}
          />
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
            {isToolPermission && preview && !patchApproval && (
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
