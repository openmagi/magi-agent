"use client";

import { useState, type KeyboardEvent, type MouseEvent } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import type {
  ControlRequestDecision,
  ControlRequestRecord,
  ControlRequestResponse,
  PatchPreview,
  PatchPreviewFile,
} from "@/chat-core";

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

type ProductPlaneControlKind =
  | "auto_permission_self_review"
  | "hard_guard_block"
  | "uncertain_fail_passthrough"
  | "admin_override_required"
  | "approval_required"
  | "denied_hard_invariant";

type ProductPlaneExecutionState =
  | "auto_executed"
  | "blocked"
  | "pending_approval"
  | "denied"
  | "not_executed";

type ProductPlaneOverrideScope = "admin" | "operator" | "none";

type ProductPlaneReceiptState =
  | "delivered"
  | "failed"
  | "pending"
  | "rendered"
  | "missing_receipt";

interface ProductPlaneControlProjection {
  kind: ProductPlaneControlKind;
  executionState?: ProductPlaneExecutionState;
  reviewId?: string;
  guardrailId?: string;
  invariantId?: string;
  approvalId?: string;
  overrideScope?: ProductPlaneOverrideScope;
  actionDigest?: string;
  policySnapshotDigest?: string;
  receiptId?: string;
  receiptState?: ProductPlaneReceiptState;
  reasonCodes: string[];
  locksApproval: boolean;
}

const PRODUCT_PLANE_KINDS = new Set<ProductPlaneControlKind>([
  "auto_permission_self_review",
  "hard_guard_block",
  "uncertain_fail_passthrough",
  "admin_override_required",
  "approval_required",
  "denied_hard_invariant",
]);

const PRODUCT_PLANE_EXECUTION_STATES = new Set<ProductPlaneExecutionState>([
  "auto_executed",
  "blocked",
  "pending_approval",
  "denied",
  "not_executed",
]);

const PRODUCT_PLANE_OVERRIDE_SCOPES = new Set<ProductPlaneOverrideScope>([
  "admin",
  "operator",
  "none",
]);

const PRODUCT_PLANE_RECEIPT_STATES = new Set<ProductPlaneReceiptState>([
  "delivered",
  "failed",
  "pending",
  "rendered",
  "missing_receipt",
]);

const SAFE_PUBLIC_ID_RE = /^[A-Za-z0-9][A-Za-z0-9:_-]{0,95}$/;
const SAFE_DIGEST_RE = /^sha256:[a-f0-9]{16,128}$/;
const UNSAFE_PUBLIC_VALUE_RE =
  /(authorization|cookie|token|session|prompt|output|transcript|tool|result|private|password|bearer)/i;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function enumValue<T extends string>(value: unknown, allowed: Set<T>): T | undefined {
  return typeof value === "string" && allowed.has(value as T) ? (value as T) : undefined;
}

function safePublicId(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!SAFE_PUBLIC_ID_RE.test(trimmed)) return undefined;
  if (UNSAFE_PUBLIC_VALUE_RE.test(trimmed)) return undefined;
  return trimmed;
}

function safeDigest(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return SAFE_DIGEST_RE.test(trimmed) ? trimmed : undefined;
}

function safeReasonCodes(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const codes: string[] = [];
  for (const item of value) {
    const code = safePublicId(item);
    if (!code || seen.has(code)) continue;
    seen.add(code);
    codes.push(code);
    if (codes.length >= 6) break;
  }
  return codes;
}

function hasProductPlaneControlMetadata(value: unknown): boolean {
  return (
    isRecord(value) &&
    (Object.prototype.hasOwnProperty.call(value, "productPlaneControl") ||
      Object.prototype.hasOwnProperty.call(value, "product_plane_control"))
  );
}

function productPlaneControlFromInput(
  value: unknown,
  approvalReceiptPreview?: ControlRequestRecord["approvalReceiptPreview"],
): ProductPlaneControlProjection | null {
  if (!isRecord(value)) return null;
  const candidate = isRecord(value.productPlaneControl)
    ? value.productPlaneControl
    : isRecord(value.product_plane_control)
      ? value.product_plane_control
      : null;
  if (!candidate) return null;
  const kind = enumValue(candidate.kind, PRODUCT_PLANE_KINDS);
  if (!kind) return null;
  const actionDigest =
    safeDigest(candidate.actionDigest) ??
    safeDigest(approvalReceiptPreview?.actionDigest);
  const policySnapshotDigest =
    safeDigest(candidate.policySnapshotDigest) ??
    safeDigest(approvalReceiptPreview?.policySnapshotDigest);
  const locksApproval =
    kind === "hard_guard_block" || kind === "denied_hard_invariant";

  return {
    kind,
    executionState: enumValue(
      candidate.executionState,
      PRODUCT_PLANE_EXECUTION_STATES,
    ),
    reviewId: safePublicId(candidate.reviewId),
    guardrailId: safePublicId(candidate.guardrailId),
    invariantId: safePublicId(candidate.invariantId),
    approvalId: safePublicId(candidate.approvalId),
    overrideScope: enumValue(candidate.overrideScope, PRODUCT_PLANE_OVERRIDE_SCOPES),
    actionDigest,
    policySnapshotDigest,
    receiptId: safePublicId(candidate.receiptId),
    receiptState: enumValue(candidate.receiptState, PRODUCT_PLANE_RECEIPT_STATES),
    reasonCodes: safeReasonCodes(candidate.reasonCodes),
    locksApproval,
  };
}

function titleCaseToken(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function controlKindLabel(kind: ProductPlaneControlKind): string {
  switch (kind) {
    case "auto_permission_self_review":
      return "Auto Permission Self-Review";
    case "hard_guard_block":
      return "Hard Guard Block";
    case "uncertain_fail_passthrough":
      return "Uncertain Fail-Passthrough";
    case "admin_override_required":
      return "Admin Override Required";
    case "approval_required":
      return "Approval Required";
    case "denied_hard_invariant":
      return "Denied Hard Invariant";
  }
}

function controlExecutionLabel(state?: ProductPlaneExecutionState): string {
  if (!state) return "Not Specified";
  switch (state) {
    case "auto_executed":
      return "Executed Automatically";
    case "blocked":
      return "Blocked";
    case "pending_approval":
      return "Pending Approval";
    case "denied":
      return "Denied";
    case "not_executed":
      return "Not Executed";
  }
}

function receiptStateLabel(state?: ProductPlaneReceiptState): string {
  if (state === "missing_receipt") return "Receipt Missing";
  return state ? titleCaseToken(state) : "Receipt Pending";
}

function hasReceiptReference(control: ProductPlaneControlProjection): boolean {
  return Boolean(
    control.receiptId || control.actionDigest || control.policySnapshotDigest,
  );
}

function displayReceiptState(
  control: ProductPlaneControlProjection,
): ProductPlaneReceiptState | undefined {
  if (
    (control.receiptState === "delivered" || control.receiptState === "rendered") &&
    !hasReceiptReference(control)
  ) {
    return "missing_receipt";
  }
  return control.receiptState;
}

function productPlaneControlMessage(control: ProductPlaneControlProjection): string {
  const receiptState = displayReceiptState(control);
  if (control.locksApproval) {
    if (control.kind === "denied_hard_invariant") {
      return "Hard invariants cannot be overridden from this UI.";
    }
    return "Execution is blocked by product-plane guardrails.";
  }
  if (control.executionState === "auto_executed") {
    if (receiptState === "delivered" || receiptState === "rendered") {
      return "Executed automatically with receipt-backed state.";
    }
    return "Executed automatically; receipt pending or missing.";
  }
  if (control.kind === "uncertain_fail_passthrough") {
    return "Needs operator review before execution.";
  }
  if (control.kind === "admin_override_required") {
    return "Admin override is required by configured policy.";
  }
  return "Approval required before execution.";
}

function ProductPlaneControlSummary({
  control,
}: {
  control: ProductPlaneControlProjection;
}) {
  const receiptState = displayReceiptState(control);
  const rows = [
    { label: "Control", value: controlKindLabel(control.kind) },
    { label: "Execution", value: controlExecutionLabel(control.executionState) },
    { label: "Review", value: control.reviewId },
    { label: "Guardrail", value: control.guardrailId },
    { label: "Invariant", value: control.invariantId },
    { label: "Approval", value: control.approvalId },
    { label: "Override scope", value: control.overrideScope },
    { label: "Action", value: shortDigest(control.actionDigest), mono: true },
    { label: "Policy", value: shortDigest(control.policySnapshotDigest), mono: true },
    {
      label: "Receipt",
      value: control.receiptId
        ? `${receiptStateLabel(receiptState)} · ${control.receiptId}`
        : receiptStateLabel(receiptState),
    },
  ].filter(
    (row): row is { label: string; value: string; mono?: boolean } =>
      typeof row.value === "string" && row.value.length > 0,
  );

  return (
    <section
      aria-label="Product-plane control context"
      className="mt-3 rounded-md border border-black/[0.06] bg-black/[0.02] px-3 py-2.5 text-xs text-secondary/75"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[11px] font-medium uppercase tracking-wide text-secondary/50">
            Product-plane control
          </div>
          <div className="mt-0.5 truncate text-sm font-medium text-foreground">
            {controlKindLabel(control.kind)}
          </div>
        </div>
        <span className="rounded-md border border-black/[0.06] bg-white px-2 py-1 text-[11px] font-medium text-secondary/75">
          {controlExecutionLabel(control.executionState)}
        </span>
      </div>
      <p className="mt-2 text-xs text-secondary/70">
        {productPlaneControlMessage(control)}
      </p>
      <dl className="mt-2 grid gap-1.5">
        {rows.map((row) => (
          <div
            key={row.label}
            className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2"
          >
            <dt className="font-medium text-secondary/60">{row.label}</dt>
            <dd
              className={`min-w-0 truncate text-secondary/80 ${
                row.mono ? "font-mono" : ""
              }`}
              translate={row.mono ? "no" : undefined}
            >
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
      {control.reasonCodes.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {control.reasonCodes.map((code) => (
            <span
              key={code}
              className="max-w-full truncate rounded-md border border-black/[0.06] bg-white px-1.5 py-0.5 font-mono text-[11px] text-secondary/70"
              translate="no"
            >
              {code}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function UnsupportedProductPlaneControlSummary() {
  return (
    <section
      aria-label="Unsupported product-plane control context"
      className="mt-3 rounded-md border border-amber-500/20 bg-amber-50 px-3 py-2.5 text-xs text-amber-800"
    >
      <div className="text-[11px] font-medium uppercase tracking-wide text-amber-700/70">
        Product-plane control
      </div>
      <div className="mt-0.5 text-sm font-medium text-amber-950">
        Unsupported Product-Plane Control
      </div>
      <p className="mt-2 text-xs text-amber-900/75">
        No action is claimed from this projection. The public contract does not
        support this control state yet.
      </p>
    </section>
  );
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

function shortDigest(value?: string): string | undefined {
  if (!value) return undefined;
  return value.length > 24 ? `${value.slice(0, 13)}…${value.slice(-6)}` : value;
}

function ApprovalReceiptPreview({
  request,
}: {
  request: ControlRequestRecord;
}) {
  const preview = request.approvalReceiptPreview;
  if (!preview) return null;
  const rows = [
    { label: "Approval scope", value: safePublicId(preview.approvalScope) },
    { label: "Approver group", value: safePublicId(preview.approverGroup) },
    { label: "Action", value: shortDigest(safeDigest(preview.actionDigest)) },
    {
      label: "Policy",
      value: shortDigest(safeDigest(preview.policySnapshotDigest)),
    },
  ].filter((row): row is { label: string; value: string } => Boolean(row.value));
  if (rows.length === 0) return null;

  return (
    <dl className="mt-3 grid gap-1.5 rounded-md border border-black/[0.06] bg-white/70 px-2 py-1.5 text-[11px] text-secondary/65">
      {rows.map((row) => (
        <div key={row.label} className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2">
          <dt className="font-medium text-secondary/70">{row.label}</dt>
          <dd className="min-w-0 truncate font-mono text-secondary/75" translate="no">
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
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
                {busy === "command" ? "Loading…" : info.label}
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
  const productPlaneControl = productPlaneControlFromInput(
    request.proposedInput,
    request.approvalReceiptPreview,
  );
  const hasProductPlaneControlProjection = hasProductPlaneControlMetadata(
    request.proposedInput,
  );
  const unsupportedProductPlaneControl =
    hasProductPlaneControlProjection && !productPlaneControl;
  const suppressRawProductPlaneInput = Boolean(
    productPlaneControl || unsupportedProductPlaneControl,
  );
  const [feedback, setFeedback] = useState("");
  const [inputText, setInputText] = useState(() =>
    patchApproval || suppressRawProductPlaneInput
      ? ""
      : inputPreview(request.proposedInput) ?? "",
  );
  const [inputError, setInputError] = useState("");
  const [busy, setBusy] = useState<ControlRequestDecision | null>(null);
  const preview =
    patchApproval || suppressRawProductPlaneInput
      ? null
      : inputPreview(request.proposedInput);
  const pending = request.state === "pending";
  const isQuestion = request.kind === "user_question";
  const isToolPermission = request.kind === "tool_permission";
  const socialInfo = socialRequestInfo(request);
  const autoExecutedProductPlaneControl =
    productPlaneControl?.kind === "auto_permission_self_review" &&
    productPlaneControl.executionState === "auto_executed";
  const hideApprove = Boolean(
    productPlaneControl?.locksApproval ||
      unsupportedProductPlaneControl ||
      autoExecutedProductPlaneControl,
  );
  const showEditableToolInput = Boolean(
    isToolPermission && preview && !patchApproval && !suppressRawProductPlaneInput,
  );

  if (pending && socialInfo) {
    return (
      <SocialBrowserRequestCard
        request={request}
        info={socialInfo}
        onRespond={onRespond}
      />
    );
  }

  if (pending && isQuestion) {
    return null;
  }

  async function submit(decision: ControlRequestDecision) {
    let updatedInput: unknown;
    setInputError("");
    if (
      decision === "approved" &&
      isToolPermission &&
      !patchApproval &&
      inputText.trim()
    ) {
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
      });
    } finally {
      setBusy(null);
    }
  }

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
        {productPlaneControl && (
          <ProductPlaneControlSummary control={productPlaneControl} />
        )}
        {unsupportedProductPlaneControl && <UnsupportedProductPlaneControlSummary />}
        <ApprovalReceiptPreview request={request} />

        {pending && (
          <>
            {showEditableToolInput && (
              <>
                <textarea
                  value={inputText}
                  onChange={(event) => setInputText(event.target.value)}
                  aria-label="Proposed tool input"
                  className="mt-3 min-h-32 w-full resize-y rounded-md border border-black/10 bg-white px-3 py-2 font-mono text-xs focus:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:outline-none"
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
              aria-label="Control request feedback"
              className="mt-3 min-h-16 w-full resize-y rounded-md border border-black/10 bg-white px-3 py-2 text-sm focus:border-primary focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:outline-none"
              placeholder="Feedback…"
            />
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void submit("denied")}
                className="rounded-md border border-black/10 px-3 py-2 text-sm font-medium text-secondary/80 hover:bg-black/[0.03] focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:outline-none disabled:opacity-40"
              >
                Deny
              </button>
              {!hideApprove && (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void submit("approved")}
                  className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-white hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:outline-none disabled:opacity-40"
                >
                  Approve
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
