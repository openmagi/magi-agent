"use client";

import { useEffect, useState } from "react";
import { Lock, Trash2 } from "lucide-react";
import { SHACL_EXAMPLE_TEMPLATE } from "./shacl-example-template";
import { CustomChecksSection } from "./custom-checks-section";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/_ds";
import type {
  ConversationTurn,
  CustomizeCatalog,
  CustomRule,
  HarnessPresetItem,
  ShaclCompileResponse,
  ShaclPreviewCase,
  ShaclReview,
} from "@/lib/customize-api";

interface VerificationRuleModalProps {
  open: boolean;
  onClose: () => void;
  catalog: CustomizeCatalog["verification"];
  /** Explicit per-preset overrides; effective state = presetOverrides[id] ?? preset.defaultEnabled. */
  presetOverrides: Record<string, boolean>;
  /** Preset ids with an in-flight PATCH. */
  pendingPresets: Set<string>;
  onTogglePreset: (id: string, enabled: boolean) => void;
  /** Structured custom rules (deterministic in P1). */
  customRules: CustomRule[];
  onAddCustomRule: (rule: CustomRule) => void;
  onToggleCustomRule: (rule: CustomRule, enabled: boolean) => void;
  onDeleteCustomRule: (id: string) => void;
  customRuleBusy: boolean;
  /**
   * Sends a natural-language constraint to the runtime for SHACL compilation
   * and preview. Injected by the parent (customize-tab.tsx) so the modal
   * doesn't call APIs directly.
   */
  onCompileShacl: (
    nlText: string,
    sampleRecords?: unknown[],
    priorTurns?: ConversationTurn[],
  ) => Promise<ShaclCompileResponse>;
  /** USER-RULES.md body + save handler. */
  userRules: string;
  rulesSaving: boolean;
  onSaveRules: (text: string) => void;
  error: string | null;
}

// WHEN-group (domain) order + labels — the modal groups by *when a gate fires*
// rather than by semantic category (spec §7). Preview presets are pulled into
// their own collapsed section regardless of domain.
export const DOMAIN_ORDER = ["always-on", "coding", "research", "delivery"] as const;

export const DOMAIN_LABELS: Record<string, string> = {
  "always-on": "Always-on (security)",
  coding: "Coding tasks",
  research: "Research tasks",
  delivery: "Delivery / General",
};

// ---------------------------------------------------------------------------
// Guide panel static data — v1 static fallback
// ---------------------------------------------------------------------------

const GUIDE_CATEGORIES = [
  { name: "Numeric range", example: 'amount must be ≤ 3000' },
  { name: "Allowed values", example: 'category must be one of {travel, meal}' },
  { name: "Pattern match", example: 'filename must match *.pdf' },
  { name: "Required field", example: 'every approval record must have signedBy' },
  { name: "Cardinality", example: 'at most one approval per request' },
] as const;

// Source: _BUILTIN_FIELD_HINTS in customize/shacl_compiler.py. Do NOT add fields not present there.
const STARTER_PROMPTS = [
  "TestRun must have exitCode equal to 0.",
  "EditMatch must have confidence at least 0.8.",
  "DocumentCoverage coverageRatio must be at least 0.9.",
  "Reject SourceInspection records where inspected is false.",
] as const;

// Source: _BUILTIN_FIELD_HINTS in customize/shacl_compiler.py. Do NOT add fields not present there.
// Only types with non-empty hints are listed here. Types with [] (e.g. Calculation, GitDiff) have NO known fields.
const STATIC_EVIDENCE_FIELDS = [
  "TestRun: command, exitCode",
  "EditMatch: tier, tierIndex, confidence, ambiguous, fileDigest, spanDigest",
  "DocumentCoverage: totalUnits, coveredUnits, coverageRatio, threshold, status, sourceDigest, docDigest",
  "SourceInspection: sourceId, sourceIds, sourceKind, inspected",
  "CodeDiagnostics: checker, errorCount, fileDigest, diagnosticsDigest",
  "CommitCheckpoint: checkpointDigest, pathRef",
] as const;

function Pill({ text, tone }: { text: string; tone: "neutral" | "live" | "lock" | "preview" }) {
  const cls = {
    neutral: "bg-black/[0.05] text-secondary",
    live: "bg-emerald-500/10 text-emerald-600",
    lock: "bg-emerald-500/10 text-emerald-600",
    preview: "bg-amber-500/10 text-amber-600",
  }[tone];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}>
      {tone === "lock" ? <Lock className="h-3 w-3" /> : null}
      {text}
    </span>
  );
}

// Tier · opt-method · wiring-state badges (spec §7: e.g. "det · opt-out · live").
function Badges({ preset }: { preset: HarnessPresetItem }) {
  if (preset.enforcement === "always-on") {
    return <Pill text="Always on" tone="lock" />;
  }
  if (preset.enforcement === "preview") {
    return <Pill text="Preview" tone="preview" />;
  }
  if (preset.enforcement === "capability") {
    return <Pill text="Capability" tone="neutral" />;
  }
  // enforcing
  return (
    <div className="flex items-center gap-1.5">
      {preset.tier === "deterministic" ? <Pill text="det" tone="neutral" /> : null}
      {preset.optMethod ? <Pill text={preset.optMethod} tone="neutral" /> : null}
      <Pill text="live" tone="live" />
    </div>
  );
}

export function PresetRow({
  preset,
  checked,
  pending,
  onToggle,
}: {
  preset: HarnessPresetItem;
  checked: boolean;
  pending: boolean;
  onToggle: (id: string, enabled: boolean) => void;
}) {
  const togglable = preset.enforcement === "enforcing";
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-semibold text-foreground">{preset.title}</p>
          <Badges preset={preset} />
        </div>
        {preset.description ? (
          <p className="mt-1 text-[11px] leading-relaxed text-secondary/80">{preset.description}</p>
        ) : null}
      </div>
      {togglable ? (
        <Switch
          checked={checked}
          disabled={pending}
          onToggle={async (next) => onToggle(preset.id, next)}
          labelOn={`Disable preset ${preset.title}`}
          labelOff={`Enable preset ${preset.title}`}
        />
      ) : null}
    </div>
  );
}

// Structured custom-rule builder (P1: deterministic_ref only). The user picks a
// producer-backed WHAT-menu check + a scope; firesAt/tier are fixed (pre-final,
// deterministic) and action is block. Saved rules render as toggle/delete rows.
const SCOPES = ["always", "coding", "research", "delivery", "memory", "task"] as const;

export { describeDraft, type CustomRuleKind } from "./describe-draft";

import { describeDraft, type CustomRuleKind } from "./describe-draft";


export function CustomRulesSection({
  menu,
  rules,
  busy,
  onAdd,
  onToggle,
  onDelete,
  onCompileShacl,
  initialKind,
  autoOpen = false,
}: {
  menu: CustomizeCatalog["verification"]["customRuleMenu"];
  rules: CustomRule[];
  busy: boolean;
  onAdd: (rule: CustomRule) => void;
  onToggle: (rule: CustomRule, enabled: boolean) => void;
  onDelete: (id: string) => void;
  onCompileShacl: (
    nlText: string,
    sampleRecords?: unknown[],
    priorTurns?: ConversationTurn[],
  ) => Promise<ShaclCompileResponse>;
  /** Phase-2 pre-fill: seed the kind picker so the user lands on the right
   *  shape (e.g. AddRuleModal's "Restrict tool" choice → tool_perm). */
  initialKind?: CustomRuleKind;
  /** Phase-2: open the add-form immediately on mount so the user does not
   *  have to click "+ Add custom rule" after picking from AddRuleModal. */
  autoOpen?: boolean;
}) {
  const [adding, setAdding] = useState<boolean>(autoOpen);
  const [kind, setKind] = useState<CustomRuleKind>(initialKind ?? "deterministic_ref");
  // Re-seed when the parent passes a new initial kind (the AddRuleModal
  // routes user back here with a different choice). Equality-safe to avoid
  // stomping a partially-filled draft on unrelated re-renders.
  useEffect(() => {
    if (initialKind) {
      setKind(initialKind);
      setAdding(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialKind]);

  // SHACL-specific state
  const [shaclMode, setShaclMode] = useState<"nl" | "raw">("nl");
  const [nlText, setNlText] = useState("");
  const [rawTtl, setRawTtl] = useState("");
  const [sampleRecordsText, setSampleRecordsText] = useState("");
  const [sampleRecordsError, setSampleRecordsError] = useState<string | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [compileError, setCompileError] = useState<string | null>(null);
  const [shaclPreview, setShaclPreview] = useState<ShaclCompileResponse | null>(null);
  const [shaclError, setShaclError] = useState<string | null>(null);

  // Conversational compile state (Sub-task 5.3b)
  const [conversation, setConversation] = useState<ConversationTurn[]>([]);
  const [clarifyingQuestions, setClarifyingQuestions] = useState<string[] | null>(null);
  const [pendingAnswer, setPendingAnswer] = useState("");
  // exhausted=true when the user has consumed all allowed clarification rounds (≥3 user turns in priorTurns).
  const [exhausted, setExhausted] = useState(false);

  // Guide panel collapse state (Sub-task 5.3c)
  // Default: expanded. Auto-collapse when user types in nlText.
  const [guideExpanded, setGuideExpanded] = useState(true);

  const [ref, setRef] = useState(menu[0]?.ref ?? "");
  const [scope, setScope] = useState<string>("coding");
  const [matchType, setMatchType] = useState<"tool" | "domain" | "domainAllowlist">("tool");
  const [matchValue, setMatchValue] = useState("");
  const [decision, setDecision] = useState<"deny" | "ask">("deny");
  const [criterion, setCriterion] = useState("");
  // P4 after-tool ingestion gate fields.
  const [toolMatch, setToolMatch] = useState("");
  const [contentPattern, setContentPattern] = useState("");
  const [contentIsRegex, setContentIsRegex] = useState(false);
  const [contentNegate, setContentNegate] = useState(false);

  const menuLabel = (r: string) => menu.find((m) => m.ref === r)?.label ?? r;

  /** Reset all conversational state — call on cancel, mode switch, kind change, save. */
  const resetConversation = () => {
    setConversation([]);
    setClarifyingQuestions(null);
    setPendingAnswer("");
    setExhausted(false);
  };

  /** Reset all SHACL state back to clean. */
  const resetShaclState = () => {
    setShaclPreview(null);
    setShaclError(null);
    setCompileError(null);
    setNlText("");
    setRawTtl("");
    setSampleRecordsText("");
    setSampleRecordsError(null);
    setShaclMode("nl");
    resetConversation();
    setGuideExpanded(true);
  };

  const describe = (rule: CustomRule): string => {
    const p = (rule.what?.payload ?? {}) as Record<string, unknown>;
    if (rule.what?.kind === "shacl_constraint") {
      const ttl = typeof p.shapeTtl === "string" ? p.shapeTtl : "";
      const snippet = ttl.slice(0, 40).replace(/\s+/g, " ");
      return `SHACL constraint${snippet ? `: ${snippet}…` : ""}`;
    }
    if (rule.what?.kind === "tool_perm") {
      const m = (p.match ?? {}) as Record<string, unknown>;
      const verb = p.decision === "ask" ? "Require approval for" : "Deny";
      if (typeof m.tool === "string") return `${verb} tool "${m.tool}"`;
      if (typeof m.domain === "string") return `${verb} fetches to ${m.domain}`;
      if (Array.isArray(m.domainAllowlist)) return `${verb} fetches outside [${m.domainAllowlist.join(", ")}]`;
      return verb;
    }
    if (rule.what?.kind === "llm_criterion") {
      if (rule.firesAt === "after_tool_use") {
        const tools = Array.isArray(p.toolMatch) ? (p.toolMatch as string[]).join(", ") : "";
        const cm = (p.contentMatch ?? {}) as Record<string, unknown>;
        const detail =
          typeof cm.pattern === "string" ? `pattern "${String(cm.pattern)}"` : `"${String(p.criterion ?? "")}"`;
        return `After-tool gate on [${tools}]: ${detail}`;
      }
      return `LLM check: "${String(p.criterion ?? "")}"`;
    }
    return menuLabel(String(p.ref ?? ""));
  };

  const canAdd =
    kind === "shacl_constraint"
      ? shaclMode === "nl"
        ? !!shaclPreview?.ok && !shaclError
        : !!rawTtl.trim()
      : kind === "deterministic_ref"
        ? !!ref
        : kind === "llm_criterion"
          ? !!criterion.trim()
          : kind === "after_tool"
            ? !!toolMatch.trim() && (!!contentPattern.trim() || !!criterion.trim())
            : !!matchValue.trim();

  const buildRule = (): CustomRule => {
    if (kind === "shacl_constraint") {
      const shapeTtl = shaclMode === "raw" ? rawTtl.trim() : (shaclPreview?.shapeTtl ?? "");
      return {
        scope,
        enabled: true,
        what: { kind: "shacl_constraint", payload: { shapeTtl } },
        firesAt: "pre_final",
        action: "block",
      };
    }
    if (kind === "tool_perm") {
      const action = decision === "deny" ? "block" : "ask_approval";
      let match: Record<string, unknown>;
      if (matchType === "tool") match = { tool: matchValue.trim() };
      else if (matchType === "domain") match = { domain: matchValue.trim() };
      else match = { domainAllowlist: matchValue.split(",").map((s) => s.trim()).filter(Boolean) };
      return {
        scope,
        enabled: true,
        what: { kind: "tool_perm", payload: { match, decision } },
        firesAt: "before_tool_use",
        action,
      };
    }
    if (kind === "llm_criterion") {
      return {
        scope,
        enabled: true,
        what: { kind: "llm_criterion", payload: { criterion: criterion.trim() } },
        firesAt: "pre_final",
        action: "block",
      };
    }
    if (kind === "after_tool") {
      const payload: Record<string, unknown> = {
        toolMatch: toolMatch.split(",").map((s) => s.trim()).filter(Boolean),
      };
      if (contentPattern.trim()) {
        payload.contentMatch = { pattern: contentPattern.trim(), isRegex: contentIsRegex, negate: contentNegate };
      }
      if (criterion.trim()) payload.criterion = criterion.trim();
      return {
        scope,
        enabled: true,
        what: { kind: "llm_criterion", payload },
        firesAt: "after_tool_use",
        action: "override",
      };
    }
    return {
      scope,
      enabled: true,
      what: { kind: "deterministic_ref", payload: { ref } },
      firesAt: "pre_final",
      action: "block",
    };
  };

  // Phase-2 live preview: build the plain-English "This rule will: ..." line
  // from the CURRENT draft (not the persisted list). Returns null when the
  // form is too empty to describe — the caller hides the preview in that
  // case so it does not flash misleading text.
  const draftPreview = describeDraft({
    kind,
    scope,
    ref,
    refLabel: ref ? menuLabel(ref) : "",
    matchType,
    matchValue,
    decision,
    criterion,
    toolMatch,
    contentPattern,
    contentIsRegex,
    contentNegate,
    shaclMode,
    shaclPreviewOk: !!shaclPreview?.ok,
    rawTtlHasContent: !!rawTtl.trim(),
  });

  const selectCls = "mt-1 w-full rounded-lg border border-black/[0.12] bg-white px-2 py-1.5 text-sm";
  const selectTriggerCls = "mt-1 rounded-lg px-2 py-1.5 text-sm font-normal";

  // Count user turns to enforce the round cap.
  // The backend rejects when validated_user_turn_count >= 3 (i.e. priorTurns already has ≥3 user turns).
  // We set exhausted=true when the user's answer would make us hit that cap, or when backend returns the error.
  const userTurnCount = conversation.filter((t) => t.role === "user").length;
  // exhausted state is managed explicitly (see setExhausted calls); this is kept for test assertions.
  const roundsExhausted = exhausted;

  return (
    <section>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
        Custom Rules
      </h3>
      <p className="mb-2 text-xs leading-relaxed text-secondary">
        Build a real gate: a deterministic evidence check (blocks the final answer),
        a tool-permission rule (deny / require approval for a tool or source domain),
        or a tool-result ingestion gate (strip an after-tool result by pattern or LLM
        check). No prompt injection.
      </p>

      {rules.length > 0 ? (
        <div className="mb-2 space-y-2">
          {rules.map((rule) => (
            <div
              key={rule.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-4 py-2.5"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-medium text-foreground">{describe(rule)}</p>
                  {rule.what?.kind === "shacl_constraint" ? (
                    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-600">
                      Deterministic · SHACL · live
                    </span>
                  ) : null}
                </div>
                <p className="mt-0.5 text-[11px] text-secondary/80">
                  {rule.scope} · {rule.firesAt} · {rule.action}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={rule.enabled}
                  disabled={busy}
                  onToggle={async (next) => onToggle(rule, next)}
                  labelOn={`Disable custom rule ${rule.id}`}
                  labelOff={`Enable custom rule ${rule.id}`}
                />
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => rule.id && onDelete(rule.id)}
                  className="p-1 text-secondary transition-colors hover:text-red-600 disabled:opacity-40"
                  aria-label={`Delete custom rule ${rule.id}`}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {adding ? (
        <div className="space-y-2 rounded-xl border border-black/[0.08] bg-gray-50/60 p-3">
          <label className="block text-[11px] font-medium text-secondary">
            Rule type
            <Select
              value={kind}
              onChange={(v) => {
                setKind(v as typeof kind);
                // Reset all SHACL sub-state on kind change via the canonical helper.
                resetShaclState();
              }}
              className={selectTriggerCls}
              options={[
                {
                  value: "deterministic_ref",
                  label: `Deterministic evidence check${menu.length === 0 ? " (none available)" : ""}`,
                  disabled: menu.length === 0,
                },
                { value: "tool_perm", label: "Tool permission (deny / approval)" },
                { value: "llm_criterion", label: "LLM criterion check (final answer)" },
                { value: "after_tool", label: "Tool-result ingestion gate (after-tool)" },
                { value: "shacl_constraint", label: "Deterministic constraint (SHACL)" },
              ]}
            />
          </label>

          {kind === "shacl_constraint" ? (
            <div className="space-y-2">
              {/* Input mode toggle: Natural language | Raw .ttl */}
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-medium text-secondary">Input mode:</span>
                <button
                  type="button"
                  onClick={() => {
                    setShaclMode("nl");
                    setShaclPreview(null);
                    setShaclError(null);
                    resetConversation();
                    setGuideExpanded(true);
                  }}
                  className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${shaclMode === "nl" ? "bg-primary text-white" : "text-secondary hover:text-foreground"}`}
                >
                  Natural language
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShaclMode("raw");
                    setShaclPreview(null);
                    setShaclError(null);
                    resetConversation();
                  }}
                  className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors ${shaclMode === "raw" ? "bg-primary text-white" : "text-secondary hover:text-foreground"}`}
                >
                  Raw .ttl
                </button>
              </div>

              {shaclMode === "nl" ? (
                <>
                  {/* Sub-task 5.3c — Guide panel (default expanded, auto-collapse on input) */}
                  <div className="rounded-lg border border-black/[0.07] bg-[var(--glass-regular-bg)] backdrop-blur-xl p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-semibold text-foreground">
                        What kind of rules can I write?
                      </span>
                      <button
                        type="button"
                        aria-expanded={guideExpanded}
                        aria-controls="shacl-guide-content"
                        onClick={() => setGuideExpanded((prev) => !prev)}
                        className="text-[10px] text-secondary hover:text-foreground"
                      >
                        {guideExpanded ? "Hide" : "Show examples again"}
                      </button>
                    </div>
                    <p className="mt-1 text-[10px] text-secondary/70">
                      Clicking Compile asks an AI to translate your description into SHACL. You&apos;ll review the result and explicitly activate it — nothing is saved before approval.
                    </p>

                    {guideExpanded ? (
                      <div id="shacl-guide-content" className="mt-2 space-y-2">
                        {/* Category list */}
                        <div className="space-y-1">
                          {GUIDE_CATEGORIES.map((cat) => (
                            <div key={cat.name} className="flex items-baseline gap-2">
                              <span className="shrink-0 text-[11px] font-semibold text-foreground">
                                {cat.name}
                              </span>
                              <span className="text-[10px] text-secondary">— {cat.example}</span>
                            </div>
                          ))}
                        </div>
                        <p className="text-[10px] italic text-secondary/70">
                          Not for open-ended judgments like &apos;is the answer fair/polite/correct&apos;.
                        </p>

                        {/* Starter prompts */}
                        <div>
                          <p className="mb-1 text-[10px] font-semibold tracking-wide text-secondary/70">
                            <span className="uppercase">Starter prompts</span>{" "}
                            <span>— click to fill</span>
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {STARTER_PROMPTS.map((prompt) => (
                              <button
                                key={prompt}
                                type="button"
                                onClick={() => {
                                  setNlText(prompt);
                                  setShaclPreview(null);
                                  setShaclError(null);
                                  resetConversation();
                                  // Auto-collapse when a starter prompt is selected
                                  setGuideExpanded(false);
                                }}
                                className="rounded-md border border-black/[0.08] bg-gray-50 px-2 py-1 text-[10px] text-secondary hover:border-primary/20 hover:bg-primary/[0.04] hover:text-foreground"
                              >
                                {prompt}
                              </button>
                            ))}
                          </div>
                        </div>

                        {/* Available evidence field chips */}
                        <div>
                          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-secondary/70">
                            Available evidence fields
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {STATIC_EVIDENCE_FIELDS.map((field) => (
                              <span
                                key={field}
                                className="rounded-md bg-black/[0.04] px-2 py-0.5 font-mono text-[10px] text-secondary"
                              >
                                {field}
                              </span>
                            ))}
                          </div>
                        </div>
                      </div>
                    ) : null}
                  </div>

                  <label className="block text-[11px] font-medium text-secondary">
                    Describe your constraint in plain English
                    <textarea
                      aria-label="Natural-language constraint input"
                      value={nlText}
                      onChange={(e) => {
                        setNlText(e.target.value);
                        // Reset preview on text change (but NOT the conversation — the user
                        // may be refining their text mid-clarification flow).
                        setShaclPreview(null);
                        setShaclError(null);
                        // Auto-collapse guide when user starts typing
                        if (e.target.value.trim().length > 0) {
                          setGuideExpanded(false);
                        }
                      }}
                      rows={3}
                      placeholder="e.g. TestRun must have exitCode equal to 0."
                      className={`${selectCls} resize-y`}
                    />
                  </label>

                  {/* F1: Sample records textarea — optional, enables deterministic preview */}
                  <label className="block text-[11px] font-medium text-secondary">
                    Sample records (JSON, optional)
                    <textarea
                      aria-label="Sample records JSON input"
                      value={sampleRecordsText}
                      onChange={(e) => {
                        setSampleRecordsText(e.target.value);
                        setSampleRecordsError(null);
                      }}
                      rows={3}
                      placeholder='[{"field_cost": 1000}, {"field_cost": null}]'
                      className={`${selectCls} resize-y font-mono text-[11px]`}
                    />
                  </label>
                  {sampleRecordsError ? (
                    <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] text-amber-700">
                      {sampleRecordsError}
                    </div>
                  ) : null}

                  <button
                    type="button"
                    disabled={!nlText.trim() || compiling || !!clarifyingQuestions || exhausted}
                    onClick={async () => {
                      // F1: parse sample records before compile
                      let parsedSamples: unknown[] | undefined;
                      if (sampleRecordsText.trim()) {
                        try {
                          const parsed: unknown = JSON.parse(sampleRecordsText);
                          if (!Array.isArray(parsed)) {
                            setSampleRecordsError("Must be a JSON array (e.g. [{...}, {...}])");
                            return;
                          }
                          parsedSamples = parsed;
                        } catch {
                          setSampleRecordsError("Invalid JSON — fix or leave blank.");
                          return;
                        }
                      }
                      // F4: try/finally so compiling is always cleared
                      setCompiling(true);
                      setShaclPreview(null);
                      setShaclError(null);
                      setCompileError(null);
                      try {
                        const result = await onCompileShacl(nlText, parsedSamples, conversation.length > 0 ? conversation : undefined);
                        if (result.clarifyingQuestions && result.clarifyingQuestions.length > 0) {
                          // Conversational: compiler needs clarification
                          const newConversation: ConversationTurn[] = [
                            ...conversation,
                            { role: "user", content: nlText },
                            {
                              role: "assistant",
                              content: JSON.stringify({ questions: result.clarifyingQuestions }),
                            },
                          ];
                          setConversation(newConversation);
                          setClarifyingQuestions(result.clarifyingQuestions);
                        } else if (result.ok) {
                          setShaclPreview(result);
                          setClarifyingQuestions(null);
                        } else {
                          setShaclError(result.error ?? "Compile failed");
                        }
                      } catch (err: unknown) {
                        setCompileError(err instanceof Error ? err.message : "An error occurred during compilation.");
                      } finally {
                        setCompiling(false);
                      }
                    }}
                    className="rounded-lg bg-black/[0.07] px-3 py-1.5 text-[11px] font-semibold text-foreground disabled:opacity-40 hover:bg-black/[0.12]"
                  >
                    {compiling ? "Compiling…" : "Compile"}
                  </button>

                  {/* Compile status area — aria-live so screen readers announce errors and questions */}
                  <div role="status" aria-live="polite" aria-atomic="false">
                    {/* F4: catch-level compile error (thrown exception, not ok:false) */}
                    {compileError ? (
                      <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-[11px] text-red-600">
                        {compileError}
                      </div>
                    ) : null}

                    {/* Compile error (ok:false from backend) */}
                    {shaclError ? (
                      <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-[11px] text-red-600">
                        {shaclError}
                      </div>
                    ) : null}
                  </div>

                  {/* Sub-task 5.3b — Chat-style conversation history */}
                  {conversation.length > 0 ? (
                    <div className="rounded-lg border border-black/[0.06] bg-gray-50/80 px-3 py-2">
                      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-secondary/70">
                        Conversation history
                      </p>
                      <div className="space-y-0.5 font-mono text-[10px] text-secondary">
                        {conversation.map((turn, i) => (
                          <div key={i}>
                            <span className="font-semibold">{turn.role === "user" ? "You" : "AI"}:</span>{" "}
                            {turn.role === "assistant"
                              ? (() => {
                                  try {
                                    const parsed = JSON.parse(turn.content) as { questions?: string[] };
                                    return (parsed.questions ?? []).join(" / ");
                                  } catch {
                                    return turn.content;
                                  }
                                })()
                              : turn.content}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {/* Sub-task 5.3b — Clarifying questions card + exhausted state */}
                  {(clarifyingQuestions && !shaclPreview?.ok) || exhausted ? (
                    <div aria-live="polite" className="rounded-xl border border-blue-500/20 bg-blue-50/40 p-3">
                      {clarifyingQuestions ? (
                        <>
                          <p className="mb-2 text-[11px] font-semibold text-foreground">
                            The compiler needs clarification:
                          </p>
                          <ul id="shacl-clarifying-questions" className="mb-3 list-inside list-disc space-y-1 text-[11px] text-secondary">
                            {clarifyingQuestions.map((q, i) => (
                              <li key={i}>{q}</li>
                            ))}
                          </ul>
                        </>
                      ) : null}

                      {exhausted ? (
                        <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] text-amber-700">
                          Compile attempts exhausted — switch to raw mode or rephrase your constraint.
                          <span className="mt-1 block">Tip: switch to Raw .ttl mode to write SHACL directly.</span>
                        </div>
                      ) : (
                        <>
                          <label className="block text-[11px] font-medium text-secondary">
                            Your answer
                            <textarea
                              value={pendingAnswer}
                              onChange={(e) => setPendingAnswer(e.target.value)}
                              rows={2}
                              placeholder="Type your answer here…"
                              className={`${selectCls} resize-y`}
                              aria-label="Answer to clarifying question"
                              aria-describedby="shacl-clarifying-questions"
                            />
                          </label>
                          <button
                            type="button"
                            disabled={!pendingAnswer.trim() || compiling || userTurnCount >= 3}
                            onClick={async () => {
                              // If this answer would put us at the round cap (3 user turns in priorTurns),
                              // flip exhausted immediately and do not call the backend again.
                              const updatedConversation: ConversationTurn[] = [
                                ...conversation,
                                { role: "user", content: pendingAnswer },
                              ];
                              // Count user turns in the conversation AFTER appending this answer.
                              const newUserTurnCount = updatedConversation.filter((t) => t.role === "user").length;
                              setConversation(updatedConversation);
                              setPendingAnswer("");

                              if (newUserTurnCount >= 3) {
                                // This answer fills the last allowed slot — mark exhausted.
                                setExhausted(true);
                                setClarifyingQuestions(null);
                                return;
                              }

                              // F4: try/finally so compiling is always cleared
                              setCompiling(true);
                              setShaclPreview(null);
                              setShaclError(null);
                              setCompileError(null);
                              try {
                                const result = await onCompileShacl(nlText, undefined, updatedConversation);
                                if (result.clarifyingQuestions && result.clarifyingQuestions.length > 0) {
                                  // Another round of questions
                                  const nextConversation: ConversationTurn[] = [
                                    ...updatedConversation,
                                    {
                                      role: "assistant",
                                      content: JSON.stringify({ questions: result.clarifyingQuestions }),
                                    },
                                  ];
                                  setConversation(nextConversation);
                                  setClarifyingQuestions(result.clarifyingQuestions);
                                } else if (result.ok) {
                                  setShaclPreview(result);
                                  setClarifyingQuestions(null);
                                } else {
                                  // Check for backend round-cap error message
                                  const errMsg = result.error ?? "Compile failed";
                                  if (errMsg.includes("too many conversation rounds")) {
                                    setExhausted(true);
                                    setClarifyingQuestions(null);
                                  } else {
                                    setShaclError(errMsg);
                                  }
                                }
                              } catch (err: unknown) {
                                setCompileError(err instanceof Error ? err.message : "An error occurred during compilation.");
                              } finally {
                                setCompiling(false);
                              }
                            }}
                            className="mt-2 rounded-lg bg-black/[0.07] px-3 py-1.5 text-[11px] font-semibold text-foreground disabled:opacity-40 hover:bg-black/[0.12]"
                          >
                            {compiling ? "Compiling…" : "Answer"}
                          </button>
                        </>
                      )}
                    </div>
                  ) : null}

                  {/* Preview panel (ok:true) */}
                  {shaclPreview?.ok ? (
                    <div className="space-y-2 rounded-xl border border-emerald-500/20 bg-emerald-50/40 p-3">
                      {/* F2: Reviewer verdict — warn explicitly when absent */}
                      {shaclPreview.review ? (
                        <div className="text-[11px]">
                          <span className="font-semibold text-foreground">Reviewer verdict: </span>
                          <span className="text-emerald-700">{shaclPreview.review.verdict}</span>
                          <span className="ml-2 text-secondary">
                            (Confidence {Math.round((shaclPreview.review.confidence ?? 0) * 100)}%)
                          </span>
                        </div>
                      ) : (
                        <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] text-amber-700">
                          ⚠ Reviewer check unavailable — verify the SHACL manually
                        </div>
                      )}

                      {/* Reverse explanation */}
                      {shaclPreview.explanation ? (
                        <p className="text-[11px] leading-relaxed text-secondary">
                          <span className="font-medium text-foreground">Reverse explanation: </span>
                          {shaclPreview.explanation}
                        </p>
                      ) : null}

                      {/* F1: Sample PASS/FAIL list — with honest empty-state */}
                      {shaclPreview.previewCases && shaclPreview.previewCases.length > 0 ? (
                        <div>
                          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-secondary/70">
                            Sample results
                          </p>
                          <div className="space-y-1">
                            {shaclPreview.previewCases.map((c: ShaclPreviewCase, i: number) => (
                              <div key={i} className="flex items-center gap-2 text-[11px]">
                                {/* F3: null conforms → N/A in neutral color, not red FAIL */}
                                <span
                                  className={
                                    c.conforms === null
                                      ? "text-secondary"
                                      : c.conforms
                                        ? "text-emerald-600"
                                        : "text-red-500"
                                  }
                                >
                                  {c.conforms === null ? "N/A" : c.conforms ? "PASS" : "FAIL"}
                                </span>
                                <span className="text-secondary">{c.status}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <p className="text-[11px] text-secondary/70">
                          Add sample records to see deterministic PASS/FAIL preview.
                        </p>
                      )}

                      {/* Collapsed: generated SHACL */}
                      <details className="mt-1">
                        <summary className="cursor-pointer text-[10px] font-medium text-secondary hover:text-foreground">
                          View generated SHACL
                        </summary>
                        <pre className="mt-1 overflow-x-auto rounded bg-black/[0.04] p-2 text-[10px] text-foreground">
                          {shaclPreview.shapeTtl}
                        </pre>
                      </details>

                      {/* Approve / retry buttons */}
                      <div className="mt-2 flex gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            onAdd(buildRule());
                            // Reset SHACL state after approval
                            resetShaclState();
                            setAdding(false);
                          }}
                          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-emerald-700"
                        >
                          ✓ Looks right — activate
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setShaclPreview(null);
                            setShaclError(null);
                            resetConversation();
                          }}
                          className="rounded-lg px-3 py-1.5 text-[11px] text-secondary hover:text-foreground"
                        >
                          ✗ Retry
                        </button>
                      </div>
                    </div>
                  ) : null}
                </>
              ) : (
                /* raw .ttl mode */
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-medium text-secondary">SHACL .ttl direct input</span>
                    <button
                      type="button"
                      onClick={() => setRawTtl(SHACL_EXAMPLE_TEMPLATE)}
                      className="rounded px-2 py-0.5 text-[10px] font-medium text-secondary hover:bg-black/[0.06] hover:text-foreground"
                    >
                      Load example
                    </button>
                  </div>
                  <textarea
                    aria-label="SHACL TTL input"
                    value={rawTtl}
                    onChange={(e) => setRawTtl(e.target.value)}
                    rows={6}
                    placeholder="@prefix sh: <http://www.w3.org/ns/shacl#> ."
                    className={`${selectCls} resize-y font-mono text-[11px]`}
                  />
                </div>
              )}
            </div>
          ) : kind === "deterministic_ref" ? (
            <label className="block text-[11px] font-medium text-secondary">
              Require
              <Select
                value={ref}
                onChange={setRef}
                className={selectTriggerCls}
                options={menu.map((m) => ({ value: m.ref, label: m.label }))}
              />
            </label>
          ) : kind === "llm_criterion" ? (
            <label className="block text-[11px] font-medium text-secondary">
              Criterion (LLM judges the final answer; blocks if not met)
              <textarea
                value={criterion}
                onChange={(e) => setCriterion(e.target.value)}
                rows={3}
                placeholder="e.g. Every factual claim is backed by a cited source."
                className={`${selectCls} resize-y`}
              />
              <span className="mt-1 block text-[10px] text-amber-600">
                Requires the egress gate (MAGI_EGRESS_GATE_ENABLED); otherwise saved but inactive.
              </span>
            </label>
          ) : kind === "after_tool" ? (
            <>
              <label className="block text-[11px] font-medium text-secondary">
                Tool(s) to inspect (comma-separated)
                <input
                  value={toolMatch}
                  onChange={(e) => setToolMatch(e.target.value)}
                  placeholder="web_search, web_fetch"
                  className={selectCls}
                />
              </label>
              <label className="block text-[11px] font-medium text-secondary">
                Block when the result matches (deterministic pre-filter)
                <input
                  value={contentPattern}
                  onChange={(e) => setContentPattern(e.target.value)}
                  placeholder="ssn:  or  \d{3}-\d{2}-\d{4}"
                  className={selectCls}
                />
              </label>
              <div className="flex gap-4">
                <label className="flex items-center gap-1.5 text-[11px] font-medium text-secondary">
                  <input type="checkbox" checked={contentIsRegex} onChange={(e) => setContentIsRegex(e.target.checked)} />
                  Regex
                </label>
                <label className="flex items-center gap-1.5 text-[11px] font-medium text-secondary">
                  <input type="checkbox" checked={contentNegate} onChange={(e) => setContentNegate(e.target.checked)} />
                  Block when it does NOT match
                </label>
              </div>
              <label className="block text-[11px] font-medium text-secondary">
                Optional LLM criterion (judged only when the pre-filter matches)
                <textarea
                  value={criterion}
                  onChange={(e) => setCriterion(e.target.value)}
                  rows={2}
                  placeholder="e.g. The result is a 10-K filing."
                  className={`${selectCls} resize-y`}
                />
                <span className="mt-1 block text-[10px] text-amber-600">
                  The LLM sub-mode requires the egress gate (MAGI_EGRESS_GATE_ENABLED); without it only the
                  deterministic pre-filter runs.
                </span>
              </label>
            </>
          ) : (
            <>
              <label className="block text-[11px] font-medium text-secondary">
                Match by
                <Select
                  value={matchType}
                  onChange={(v) => setMatchType(v as typeof matchType)}
                  className={selectTriggerCls}
                  options={[
                    { value: "tool", label: "Tool name" },
                    { value: "domain", label: "Source domain (denylist)" },
                    { value: "domainAllowlist", label: "Source domain allowlist (only these)" },
                  ]}
                />
              </label>
              <label className="block text-[11px] font-medium text-secondary">
                {matchType === "tool" ? "Tool name" : matchType === "domain" ? "Domain to block" : "Allowed domains (comma-separated)"}
                <input
                  value={matchValue}
                  onChange={(e) => setMatchValue(e.target.value)}
                  placeholder={matchType === "domainAllowlist" ? "sec.gov, ecfr.gov" : matchType === "tool" ? "web_fetch" : "evil.com"}
                  className={selectCls}
                />
              </label>
              <label className="block text-[11px] font-medium text-secondary">
                Then
                <Select
                  value={decision}
                  onChange={(v) => setDecision(v as typeof decision)}
                  className={selectTriggerCls}
                  options={[
                    { value: "deny", label: "Deny" },
                    { value: "ask", label: "Require approval" },
                  ]}
                />
              </label>
            </>
          )}

          <label className="block text-[11px] font-medium text-secondary">
            When (scope)
            <Select
              value={scope}
              onChange={setScope}
              className={selectTriggerCls}
              options={SCOPES.map((s) => ({ value: s, label: s }))}
            />
          </label>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setAdding(false);
                resetShaclState();
                setKind("deterministic_ref");
              }}
              className="rounded-lg px-3 py-1.5 text-sm text-secondary hover:text-foreground"
            >
              Cancel
            </button>
            {draftPreview ? (
              <p
                role="note"
                aria-label="Live preview of the rule"
                className="mr-auto rounded-md border border-emerald-500/20 bg-emerald-50/60 px-2.5 py-1 text-[11px] leading-snug text-emerald-900"
              >
                <span className="font-semibold">This rule will:</span> {draftPreview}
              </p>
            ) : null}
            {/* For SHACL nl mode the approve button (above) handles saving — hide generic Add rule. */}
            {kind !== "shacl_constraint" || shaclMode === "raw" ? (
              <button
                type="button"
                disabled={busy || !canAdd}
                onClick={() => {
                  onAdd(buildRule());
                  setMatchValue("");
                  setCriterion("");
                  setToolMatch("");
                  setContentPattern("");
                  setContentIsRegex(false);
                  setContentNegate(false);
                  // resetShaclState covers rawTtl, shaclPreview, shaclError, compileError,
                  // sampleRecords, shaclMode, conversation, and guide state.
                  resetShaclState();
                  setAdding(false);
                }}
                className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
              >
                {kind === "shacl_constraint" ? "Activate" : "Add rule"}
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="w-full rounded-xl border border-dashed border-black/[0.12] px-4 py-2.5 text-sm font-medium text-secondary transition-colors hover:border-primary/30 hover:text-foreground"
        >
          + Add custom rule
        </button>
      )}
    </section>
  );
}

/** Props for the embeddable panel body — same data + handlers as the modal, but
 * no modal chrome (no Modal wrapper, no close button, no `open`/`onClose`).
 * The full-page Customize hub mounts this directly; the legacy modal wraps it.
 */
export type VerificationRulePanelProps = Omit<
  VerificationRuleModalProps,
  "open" | "onClose"
> & {
  /** Re-seed the draft when the wrapping surface re-opens (modal) or when the
   * route mounts (page). Defaults to `true`. */
  seed?: boolean;
};

/** Headless panel body — shared between the legacy modal and the Phase-4 hub. */
export function VerificationRulePanel({
  catalog,
  presetOverrides,
  pendingPresets,
  onTogglePreset,
  customRules,
  onAddCustomRule,
  onToggleCustomRule,
  onDeleteCustomRule,
  customRuleBusy,
  userRules,
  rulesSaving,
  onSaveRules,
  onCompileShacl,
  error,
  seed = true,
}: VerificationRulePanelProps): React.ReactElement {
  const [rulesDraft, setRulesDraft] = useState(userRules);
  useEffect(() => {
    if (seed) setRulesDraft(userRules);
  }, [seed, userRules]);

  // Preview presets are pulled out into their own collapsed section regardless of
  // domain; everything else groups by WHEN (domain).
  const previewPresets = catalog.harnessPresets.filter((p) => p.enforcement === "preview");
  const byDomain = new Map<string, HarnessPresetItem[]>();
  for (const preset of catalog.harnessPresets) {
    if (preset.enforcement === "preview") continue;
    const list = byDomain.get(preset.domain) ?? [];
    list.push(preset);
    byDomain.set(preset.domain, list);
  }
  const orderedDomains = [
    ...DOMAIN_ORDER.filter((d) => byDomain.has(d)),
    ...[...byDomain.keys()].filter((d) => !DOMAIN_ORDER.includes(d as never)),
  ];
  const rulesDirty = rulesDraft !== userRules;

  return (
    <>
      {error ? (
        <div className="mb-4 rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      ) : null}

      <div className="space-y-6">
        {orderedDomains.map((domain) => {
          const presets = byDomain.get(domain) ?? [];
          if (presets.length === 0) return null;
          return (
            <section key={domain}>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
                {DOMAIN_LABELS[domain] ?? domain}
              </h3>
              <div className="space-y-2">
                {presets.map((preset) => (
                  <PresetRow
                    key={preset.id}
                    preset={preset}
                    checked={presetOverrides[preset.id] ?? preset.defaultEnabled}
                    pending={pendingPresets.has(preset.id)}
                    onToggle={onTogglePreset}
                  />
                ))}
              </div>
            </section>
          );
        })}

        {previewPresets.length > 0 ? (
          <details className="rounded-xl border border-black/[0.06] bg-gray-50/60">
            <summary className="cursor-pointer px-4 py-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
              Not yet wired — preview ({previewPresets.length})
            </summary>
            <div className="space-y-2 px-3 pb-3">
              {previewPresets.map((preset) => (
                <PresetRow
                  key={preset.id}
                  preset={preset}
                  checked={false}
                  pending={false}
                  onToggle={onTogglePreset}
                />
              ))}
            </div>
          </details>
        ) : null}

        <CustomRulesSection
          menu={catalog.customRuleMenu}
          rules={customRules}
          busy={customRuleBusy}
          onAdd={onAddCustomRule}
          onToggle={onToggleCustomRule}
          onDelete={onDeleteCustomRule}
          onCompileShacl={onCompileShacl}
        />

        <CustomChecksSection busy={customRuleBusy} />

        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
            Freeform guidance
          </h3>
          <p className="mb-2 text-xs leading-relaxed text-secondary">
            Free-text instructions injected into your agent&apos;s system prompt every turn.
          </p>
          <textarea
            aria-label="Freeform guidance"
            value={rulesDraft}
            onChange={(e) => setRulesDraft(e.target.value)}
            rows={5}
            placeholder="e.g. Always cite sources. Never delete files without confirming."
            className="w-full resize-y rounded-xl border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          />
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              disabled={!rulesDirty || rulesSaving}
              onClick={() => onSaveRules(rulesDraft)}
              className="inline-flex min-h-[36px] items-center rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {rulesSaving ? "Saving…" : rulesDirty ? "Save rules" : "Saved"}
            </button>
          </div>
        </section>
      </div>
    </>
  );
}


export function VerificationRuleModal({
  open,
  onClose,
  ...panelProps
}: VerificationRuleModalProps): React.ReactElement | null {
  if (!open) return null;

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6">
        <div className="mb-1 flex items-start justify-between">
          <h2 className="text-lg font-semibold text-foreground">Verification Rules</h2>
          <button
            type="button"
            onClick={onClose}
            className="-mr-1 -mt-1 p-1 text-secondary transition-colors hover:text-foreground"
            aria-label="Close"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <p className="mb-5 text-xs leading-relaxed text-secondary">
          Toggle the verification gates that constrain your agent&apos;s output. Changes are saved
          immediately. Presets marked <span className="font-medium text-amber-600">Preview</span> are not
          yet wired to a runtime gate; <span className="font-medium text-emerald-600">Always on</span>{" "}
          gates are enforced by the runtime and can&apos;t be turned off here.
        </p>

        <VerificationRulePanel seed={open} {...panelProps} />
      </div>
    </Modal>
  );
}
