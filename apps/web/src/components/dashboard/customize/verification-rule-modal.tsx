"use client";

import { useEffect, useState } from "react";
import { Lock, Trash2 } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import type { CustomizeCatalog, CustomRule, HarnessPresetItem } from "@/lib/customize-api";

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
  /** USER-RULES.md body + save handler. */
  userRules: string;
  rulesSaving: boolean;
  onSaveRules: (text: string) => void;
  error: string | null;
}

// WHEN-group (domain) order + labels — the modal groups by *when a gate fires*
// rather than by semantic category (spec §7). Preview presets are pulled into
// their own collapsed section regardless of domain.
const DOMAIN_ORDER = ["always-on", "coding", "research", "delivery"] as const;

const DOMAIN_LABELS: Record<string, string> = {
  "always-on": "Always-on (security)",
  coding: "Coding tasks",
  research: "Research tasks",
  delivery: "Delivery / General",
};

function Toggle({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean;
  disabled: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40 ${
        checked ? "bg-primary" : "bg-black/15"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

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

function PresetRow({
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
    <div className="flex items-center justify-between gap-4 rounded-xl border border-black/[0.06] bg-white px-4 py-3">
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
        <Toggle
          checked={checked}
          disabled={pending}
          onChange={(next) => onToggle(preset.id, next)}
          label={`Toggle preset ${preset.title}`}
        />
      ) : null}
    </div>
  );
}

// Structured custom-rule builder (P1: deterministic_ref only). The user picks a
// producer-backed WHAT-menu check + a scope; firesAt/tier are fixed (pre-final,
// deterministic) and action is block. Saved rules render as toggle/delete rows.
const SCOPES = ["always", "coding", "research", "delivery", "memory", "task"] as const;

function CustomRulesSection({
  menu,
  rules,
  busy,
  onAdd,
  onToggle,
  onDelete,
}: {
  menu: CustomizeCatalog["verification"]["customRuleMenu"];
  rules: CustomRule[];
  busy: boolean;
  onAdd: (rule: CustomRule) => void;
  onToggle: (rule: CustomRule, enabled: boolean) => void;
  onDelete: (id: string) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<"deterministic_ref" | "tool_perm" | "llm_criterion" | "after_tool">("deterministic_ref");
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

  const describe = (rule: CustomRule): string => {
    const p = (rule.what?.payload ?? {}) as Record<string, unknown>;
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
    kind === "deterministic_ref"
      ? !!ref
      : kind === "llm_criterion"
        ? !!criterion.trim()
        : kind === "after_tool"
          ? !!toolMatch.trim() && (!!contentPattern.trim() || !!criterion.trim())
          : !!matchValue.trim();

  const buildRule = (): CustomRule => {
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

  const selectCls = "mt-1 w-full rounded-lg border border-black/[0.12] bg-white px-2 py-1.5 text-sm";
  const selectTriggerCls = "mt-1 rounded-lg px-2 py-1.5 text-sm font-normal";

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
              className="flex items-center justify-between gap-3 rounded-xl border border-black/[0.06] bg-white px-4 py-2.5"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{describe(rule)}</p>
                <p className="mt-0.5 text-[11px] text-secondary/80">
                  {rule.scope} · {rule.firesAt} · {rule.action}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Toggle
                  checked={rule.enabled}
                  disabled={busy}
                  onChange={(next) => onToggle(rule, next)}
                  label={`Toggle custom rule ${rule.id}`}
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
              onChange={(v) => setKind(v as typeof kind)}
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
              ]}
            />
          </label>

          {kind === "deterministic_ref" ? (
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
              onClick={() => setAdding(false)}
              className="rounded-lg px-3 py-1.5 text-sm text-secondary hover:text-foreground"
            >
              Cancel
            </button>
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
                setAdding(false);
              }}
              className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            >
              Add rule
            </button>
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

export function VerificationRuleModal({
  open,
  onClose,
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
  error,
}: VerificationRuleModalProps): React.ReactElement | null {
  const [rulesDraft, setRulesDraft] = useState(userRules);
  // Re-seed the draft whenever the modal (re)opens with fresh backend state.
  useEffect(() => {
    if (open) setRulesDraft(userRules);
  }, [open, userRules]);

  if (!open) return null;

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

          {/* Preview (not yet wired) — collapsed, non-toggle */}
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

          {/* Structured custom rules (deterministic) */}
          <CustomRulesSection
            menu={catalog.customRuleMenu}
            rules={customRules}
            busy={customRuleBusy}
            onAdd={onAddCustomRule}
            onToggle={onToggleCustomRule}
            onDelete={onDeleteCustomRule}
          />

          {/* Freeform prompt guidance (USER-RULES.md) */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
              Freeform guidance
            </h3>
            <p className="mb-2 text-xs leading-relaxed text-secondary">
              Free-text instructions injected into your agent&apos;s system prompt every turn.
            </p>
            <textarea
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
      </div>
    </Modal>
  );
}
