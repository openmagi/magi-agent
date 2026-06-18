"use client";

import { useEffect, useRef, useState } from "react";
import { fetchAttachmentBlob, getAttachmentUrl } from "@/chat-core/attachments";

type ProductPlaneArtifactReceiptStatus =
  | "delivered"
  | "failed"
  | "missing_receipt"
  | "pending"
  | "rendered";

interface ProductPlaneArtifactView {
  artifactId?: string;
  artifactDigest?: string;
  renderStatus?: ProductPlaneArtifactReceiptStatus;
  deliveryStatus?: ProductPlaneArtifactReceiptStatus;
  renderReceiptId?: string;
  deliveryReceiptId?: string;
  renderDigest?: string;
  deliveryDigest?: string;
  warningCodes: string[];
  unsupportedDelivery: boolean;
}

/** Minimal info needed to open an HTML artifact. */
export interface ArtifactRef {
  /** Attachment UUID on chat-proxy. */
  id: string;
  /** Display filename (used as title fallback). */
  filename: string;
  /** Bot owning the attachment (for auth-scoped fetch). */
  botId: string;
  /** Optional sanitized product-plane receipt projection. */
  productPlaneArtifact?: unknown;
}

const SAFE_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
const SAFE_PUBLIC_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,95}$/;
const SAFE_REASON_CODE_RE = /^[a-zA-Z][a-zA-Z0-9_:-]{0,79}$/;
const UNSAFE_PRODUCT_PLANE_VALUE_RE =
  /(raw[_-]?(?:prompt|output|model|tool|event|adk|transcript)|hidden[_-]?reason|authorization|auth[_-]?header|cookie|session[_-]?key|connector[_-]?token|service[_-]?secret|private[_-]?metadata|tool[_-]?args?|tool[_-]?results?)/i;
const SECRET_KEY_NAME_RE =
  /(auth(?:orization)?(?:[_:-]?token|token)?|bearer|oauth|refresh[_:-]?token|opaque[_:-]?token|api[_:-]?key|session(?:[_:-]?token|token|[_:-]?key)|connector(?:[_:-]?token|token)|service(?:[_:-]?secret|secret)|access[_:-]?key|private[_:-]?key|slack[_:-]?token|xox[baprs]|credential|secret)/i;
const SECRET_SHAPE_RE =
  /(?:^|[^a-zA-Z0-9])(?:gh[pousr]_[a-zA-Z0-9_]{20,}|sk-[a-zA-Z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[a-zA-Z0-9-]{8,})(?:$|[^a-zA-Z0-9])|(?:^|[^a-zA-Z0-9_-])(?:[a-zA-Z0-9_-]{10,}\.){2}[a-zA-Z0-9_-]{8,}(?:$|[^a-zA-Z0-9_-])|(?:^|[^a-zA-Z0-9_-])eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]{6,}(?:$|[^a-zA-Z0-9_-])/;
const PRIVATE_PATH_RE =
  /(?:^|[\s"'`])(?:~\/|\.{2}\/|\/(?:Users|home|var|etc|private|tmp|mnt|Volumes)\/|[A-Za-z]:\\|(?:[a-zA-Z0-9_.-]+\/){2,}[a-zA-Z0-9_.-]+\.(?:db|sqlite|sqlite3|json|log|html?))/;
const RECEIPT_STATUSES = new Set<ProductPlaneArtifactReceiptStatus>([
  "delivered",
  "failed",
  "missing_receipt",
  "pending",
  "rendered",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeDigest(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return SAFE_DIGEST_RE.test(trimmed) ? trimmed : undefined;
}

function hasUnsafeProductPlaneText(value: string): boolean {
  return (
    UNSAFE_PRODUCT_PLANE_VALUE_RE.test(value) ||
    SECRET_KEY_NAME_RE.test(value) ||
    SECRET_SHAPE_RE.test(value) ||
    PRIVATE_PATH_RE.test(value)
  );
}

function safePublicId(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 96) return undefined;
  if (!SAFE_PUBLIC_ID_RE.test(trimmed) || hasUnsafeProductPlaneText(trimmed)) {
    return undefined;
  }
  return trimmed;
}

function safeReasonCode(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!SAFE_REASON_CODE_RE.test(trimmed) || hasUnsafeProductPlaneText(trimmed)) {
    return undefined;
  }
  return trimmed;
}

function safeStatus(value: unknown): ProductPlaneArtifactReceiptStatus | undefined {
  if (typeof value !== "string") return undefined;
  return RECEIPT_STATUSES.has(value as ProductPlaneArtifactReceiptStatus)
    ? (value as ProductPlaneArtifactReceiptStatus)
    : undefined;
}

function uniqueWarnings(warningCodes: string[]): string[] {
  return Array.from(new Set(warningCodes)).slice(0, 6);
}

function parseProductPlaneArtifact(value: unknown): ProductPlaneArtifactView | null {
  if (!isRecord(value)) return null;

  const artifactId = safePublicId(value.artifactId);
  const artifactDigest = safeDigest(value.artifactDigest);
  const renderReceiptId = safePublicId(value.renderReceiptId);
  const deliveryReceiptId = safePublicId(value.deliveryReceiptId);
  const renderDigest = safeDigest(value.renderDigest);
  const deliveryDigest = safeDigest(value.deliveryDigest);
  const warningCodes = Array.isArray(value.warningCodes)
    ? value.warningCodes.map(safeReasonCode).filter((code): code is string => Boolean(code))
    : [];
  const unsupportedDelivery = value.unsupportedDelivery === true;
  const parsedRenderStatus = safeStatus(value.renderStatus);
  const parsedDeliveryStatus = safeStatus(value.deliveryStatus);

  const renderReceiptBacked = Boolean(renderReceiptId || renderDigest);
  const deliveryReceiptBacked = Boolean(deliveryReceiptId || deliveryDigest);

  if (
    !artifactId &&
    !artifactDigest &&
    !parsedRenderStatus &&
    !parsedDeliveryStatus &&
    !renderReceiptId &&
    !deliveryReceiptId &&
    !renderDigest &&
    !deliveryDigest &&
    warningCodes.length === 0 &&
    !unsupportedDelivery
  ) {
    return null;
  }

  let renderStatus = parsedRenderStatus ?? "pending";
  let deliveryStatus = parsedDeliveryStatus ?? "pending";

  if ((renderStatus === "rendered" || renderStatus === "delivered") && !renderReceiptBacked) {
    renderStatus = "missing_receipt";
    warningCodes.push("missing_render_receipt");
  }

  if (deliveryStatus === "delivered" && !deliveryReceiptBacked) {
    deliveryStatus = "missing_receipt";
    warningCodes.push("missing_delivery_receipt");
  }

  return {
    ...(artifactId ? { artifactId } : {}),
    ...(artifactDigest ? { artifactDigest } : {}),
    ...(renderStatus ? { renderStatus } : {}),
    ...(deliveryStatus ? { deliveryStatus } : {}),
    ...(renderReceiptId ? { renderReceiptId } : {}),
    ...(deliveryReceiptId ? { deliveryReceiptId } : {}),
    ...(renderDigest ? { renderDigest } : {}),
    ...(deliveryDigest ? { deliveryDigest } : {}),
    warningCodes: uniqueWarnings(warningCodes),
    unsupportedDelivery,
  };
}

function shortDigest(digest: string | undefined): string | undefined {
  if (!digest) return undefined;
  return `${digest.slice(0, 13)}…${digest.slice(-6)}`;
}

function renderStatusLabel(artifact: ProductPlaneArtifactView): string {
  if (artifact.renderStatus === "failed") return "Render Failed";
  if (artifact.renderStatus === "pending") return "Render Pending";
  if (artifact.renderStatus === "missing_receipt") return "Render Receipt Missing";
  if (artifact.renderStatus === "rendered" || artifact.renderStatus === "delivered") {
    return artifact.renderReceiptId || artifact.renderDigest
      ? "Render Verified"
      : "Render Receipt Missing";
  }
  return "Render Pending";
}

function deliveryStatusLabel(artifact: ProductPlaneArtifactView): string {
  if (artifact.deliveryStatus === "failed") return "Delivery Failed";
  if (artifact.deliveryStatus === "pending") return "Delivery Pending";
  if (artifact.deliveryStatus === "missing_receipt") return "Delivery Receipt Missing";
  if (artifact.deliveryStatus === "rendered") return "Delivery Receipt Pending";
  if (artifact.deliveryStatus === "delivered") {
    return artifact.deliveryReceiptId || artifact.deliveryDigest
      ? "Delivered"
      : "Delivery Receipt Missing";
  }
  return "Delivery Pending";
}

function toneForStatus(label: string): "good" | "neutral" | "warning" | "danger" {
  if (label.includes("Failed")) return "danger";
  if (label.includes("Missing") || label.includes("Unsupported")) return "warning";
  if (label === "Delivered" || label.includes("Verified")) return "good";
  return "neutral";
}

function toneClass(tone: "good" | "neutral" | "warning" | "danger"): string {
  switch (tone) {
    case "good":
      return "border-emerald-500/20 bg-emerald-500/[0.08] text-emerald-700";
    case "warning":
      return "border-amber-500/20 bg-amber-500/[0.08] text-amber-800";
    case "danger":
      return "border-red-500/20 bg-red-500/[0.08] text-red-700";
    case "neutral":
      return "border-black/[0.08] bg-black/[0.03] text-secondary/70";
  }
}

function ProductPlaneStatusPill({ label }: { label: string }) {
  return (
    <span
      className={`inline-flex max-w-full items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-tight ${toneClass(toneForStatus(label))}`}
    >
      <span className="truncate">{label}</span>
    </span>
  );
}

function ProductPlaneArtifactMeta({
  label,
  value,
}: {
  label: string;
  value: string | undefined;
}) {
  if (!value) return null;
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-[10px] leading-tight">
      <span className="shrink-0 text-secondary/45">{label}</span>
      <span className="min-w-0 break-words font-mono text-secondary/70" translate="no">
        {value}
      </span>
    </div>
  );
}

function ProductPlaneArtifactSummary({
  artifact,
  surface,
}: {
  artifact: ArtifactRef;
  surface: "card" | "panel";
}) {
  const productPlaneArtifact = parseProductPlaneArtifact(artifact.productPlaneArtifact);
  if (!productPlaneArtifact) return null;

  const renderLabel = renderStatusLabel(productPlaneArtifact);
  const deliveryLabel = deliveryStatusLabel(productPlaneArtifact);
  const digestLabel = shortDigest(productPlaneArtifact.artifactDigest);
  const renderDigestLabel = shortDigest(productPlaneArtifact.renderDigest);
  const deliveryDigestLabel = shortDigest(productPlaneArtifact.deliveryDigest);
  const compact = surface === "card";

  return (
    <div
      role="group"
      className={compact ? "mt-1.5 space-y-1" : "mt-1.5 max-w-full space-y-1"}
      data-product-plane-artifact-status="true"
      aria-label="Product-plane artifact receipts"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        <ProductPlaneStatusPill label={renderLabel} />
        <ProductPlaneStatusPill label={deliveryLabel} />
        {productPlaneArtifact.unsupportedDelivery && (
          <ProductPlaneStatusPill label="Delivery Unsupported" />
        )}
      </div>
      <div className={compact ? "space-y-0.5" : "grid gap-x-3 gap-y-0.5 sm:grid-cols-2"}>
        <ProductPlaneArtifactMeta label="Artifact Digest" value={digestLabel} />
        <ProductPlaneArtifactMeta label="Artifact ID" value={productPlaneArtifact.artifactId} />
        <ProductPlaneArtifactMeta label="Render Receipt" value={productPlaneArtifact.renderReceiptId} />
        <ProductPlaneArtifactMeta label="Render Digest" value={renderDigestLabel} />
        <ProductPlaneArtifactMeta label="Delivery Receipt" value={productPlaneArtifact.deliveryReceiptId} />
        <ProductPlaneArtifactMeta label="Delivery Digest" value={deliveryDigestLabel} />
        {productPlaneArtifact.warningCodes.length > 0 && (
          <ProductPlaneArtifactMeta
            label="Warnings"
            value={productPlaneArtifact.warningCodes.join(", ")}
          />
        )}
      </div>
    </div>
  );
}

/**
 * Inline card shown inside the message bubble for .html artifacts.
 * Clicking "Open" opens the ArtifactPanel (handled by the page-level host).
 */
export function ArtifactCard({ artifact, onOpen }: { artifact: ArtifactRef; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="group flex w-full items-start gap-3 rounded-xl border border-black/[0.08] bg-white px-3 py-2.5 text-left transition-[border-color,box-shadow,background-color] hover:border-[#7C3AED] hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#7C3AED] cursor-pointer"
    >
      <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-[#7C3AED] to-[#A78BFA] flex items-center justify-center shrink-0">
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="16 18 22 12 16 6" />
          <polyline points="8 6 2 12 8 18" />
        </svg>
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-foreground truncate">{artifact.filename}</div>
        <div className="text-[11px] text-secondary/60">Interactive HTML · click to open</div>
        <ProductPlaneArtifactSummary artifact={artifact} surface="card" />
      </div>
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="mt-1 shrink-0 text-secondary/40 transition-colors group-hover:text-[#7C3AED]">
        <path d="M7 17L17 7" />
        <path d="M7 7h10v10" />
      </svg>
    </button>
  );
}

/**
 * Side panel (desktop ≥md) or fullscreen modal (mobile) that renders an HTML
 * artifact in a sandboxed iframe.
 *
 * Security: uses `srcdoc` with `sandbox="allow-scripts"` and no
 * `allow-same-origin` — iframe becomes a null-origin context with no access
 * to parent cookies, localStorage, or DOM.
 */
export function ArtifactPanel({ artifact, onClose }: { artifact: ArtifactRef | null; onClose: () => void }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!artifact) {
      setHtml(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    setHtml(null);
    const ac = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ac;

    (async () => {
      try {
        const url = getAttachmentUrl(artifact.botId, artifact.id);
        const blob = await fetchAttachmentBlob(url);
        if (ac.signal.aborted) return;
        const text = await blob.text();
        if (ac.signal.aborted) return;
        setHtml(text);
      } catch (e) {
        if (ac.signal.aborted) return;
        setError(e instanceof Error ? e.message : "Failed to load artifact");
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    })();

    return () => ac.abort();
  }, [artifact]);

  // Close on Escape
  useEffect(() => {
    if (!artifact) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [artifact, onClose]);

  if (!artifact) return null;

  const downloadUrl = getAttachmentUrl(artifact.botId, artifact.id);

  return (
    <>
      {/* Mobile/desktop overlay. On desktop this is a right-side drawer; on
          mobile it is a fullscreen modal. */}
      <div
        className="fixed inset-0 z-40 bg-black/30 md:bg-black/20 animate-in fade-in duration-150"
        onClick={onClose}
      />
      <aside
        className="fixed z-50 inset-0 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] md:pt-0 md:pb-0 md:inset-y-0 md:right-0 md:left-auto md:w-[min(680px,55vw)] bg-white md:border-l border-black/[0.08] shadow-2xl flex flex-col animate-in slide-in-from-right duration-200"
        role="dialog"
        aria-label="HTML artifact"
      >
        <header className="flex items-center gap-3 px-4 py-3 border-b border-black/[0.06] shrink-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#7C3AED] to-[#A78BFA] flex items-center justify-center shrink-0">
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="16 18 22 12 16 6" />
              <polyline points="8 6 2 12 8 18" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-foreground truncate">{artifact.filename}</div>
            <div className="text-[11px] text-secondary/60">Sandboxed HTML preview</div>
            <ProductPlaneArtifactSummary artifact={artifact} surface="panel" />
          </div>
          <a
            href={downloadUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="p-2 rounded-lg hover:bg-black/[0.04] transition-colors text-secondary/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#7C3AED]"
            title="Open raw (new tab, auth via browser)"
            aria-label="Open raw"
          >
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </a>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-black/[0.04] transition-colors text-secondary/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#7C3AED]"
            aria-label="Close"
          >
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>
        <div className="flex-1 min-h-0 bg-[#FAFAFA] relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-xs text-secondary/60">Loading artifact…</div>
            </div>
          )}
          {error && (
            <div className="absolute inset-0 flex items-center justify-center p-4">
              <div className="text-xs text-red-600 bg-red-500/[0.06] rounded-xl px-3 py-2 text-center max-w-md">
                {error}
              </div>
            </div>
          )}
          {html !== null && !error && (
            <iframe
              // null-origin sandbox: no cookie / DOM / storage access to parent.
              // `allow-scripts` enables interactive artifacts; we intentionally
              // do NOT grant `allow-same-origin`, `allow-forms`, `allow-popups`,
              // `allow-top-navigation`.
              sandbox="allow-scripts"
              srcDoc={html}
              className="w-full h-full border-0"
              title={artifact.filename}
            />
          )}
        </div>
      </aside>
    </>
  );
}
