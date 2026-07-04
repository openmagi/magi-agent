"use client";

/**
 * Rules table — Phase 1 of the unified Customize redesign.
 *
 * Kevin's 2026-06-20 UX feedback: the previous design split related state
 * across 4 disjoint surfaces (Presets / Custom Rules / Custom Checks /
 * SeamSpec), so users could not see "everything that gates the agent"
 * in one place. Worst: SeamSpec mutations to the builtin preset catalog
 * never visually surfaced back in Presets, making the dataflow look
 * impossible to reason about.
 *
 * This table merges all sources into one scannable list:
 *
 *   ┌───────────┬───────────────────────┬────────┬──────────┬──────┬───────┐
 *   │ origin    │ rule                  │ scope  │ mode     │ state│ action│
 *   ├───────────┼───────────────────────┼────────┼──────────┼──────┼───────┤
 *   │ 🔒 Built-in│ Dangerous Patterns    │ always │ det.     │ ●lock│   —   │
 *   │ 🔒 Built-in│ Coding Verification   │ coding │ det.     │  ✓   │ toggle│
 *   │ 👤 Custom  │ Tests were actually run│delivery│ det.    │  ✓   │ ↑     │
 *   │ 🔍 After-tool│ block: secret-leak  │ always │ regex    │  ✓   │ ↑     │
 *   │ 🪄 SeamSpec│ rewire: coding-verif. │ coding │ det.     │  ✓   │ delete│
 *   └───────────┴───────────────────────┴────────┴──────────┴──────┴───────┘
 *
 * Phase 2 will add plain-English "what this rule does" preview rows and
 * collapse all authoring into a single wizard; this PR keeps existing
 * forms reachable via the AddRuleModal entry-point.
 */

import { ChevronRight, Trash2 } from "lucide-react";
import React, { useMemo, useState } from "react";

import type {
  CustomizeCatalog,
  CustomRule,
  HarnessPresetItem,
  SeamSpecAction,
  SeamSpecDoc,
} from "@/lib/customize-api";

import { TrustBadge, trustClassForPolicy, type TrustClass } from "./trust-badge";


// ---------------------------------------------------------------------------
// Origin + tone constants
// ---------------------------------------------------------------------------


export type RuleOrigin = "builtin" | "custom" | "after-tool" | "seamspec";


const ORIGIN_LABEL: Record<RuleOrigin, string> = {
  builtin: "Built-in",
  custom: "Custom",
  "after-tool": "After-tool",
  seamspec: "SeamSpec",
};


const ORIGIN_TONE: Record<RuleOrigin, string> = {
  builtin: "bg-emerald-500/10 text-emerald-700",
  custom: "bg-blue-500/10 text-blue-700",
  "after-tool": "bg-violet-500/10 text-violet-700",
  seamspec: "bg-amber-500/10 text-amber-700",
};


// Visual grouping by ORIGIN, because that is the axis the user is trying to
// reason about ("what is the system shipping vs what did I add"). Within each
// group we further sort by scope/title for determinism. Built-in security
// rules render first so the always-on guardrails are top-of-page.
const ORIGIN_ORDER: ReadonlyArray<RuleOrigin> = [
  "builtin",
  "custom",
  "after-tool",
  "seamspec",
];


// ---------------------------------------------------------------------------
// Row model
// ---------------------------------------------------------------------------


interface RuleRow {
  /** Stable per-row key (origin + sub-id) used by React. */
  rowKey: string;
  origin: RuleOrigin;
  title: string;
  scope: string;
  mode: string;
  state: "always-on" | "enabled" | "disabled" | "preview";
  /** Honesty taxonomy bucket — drives the per-row TrustBadge in the
   *  Trust column. Derived by ``trustClassForPolicy()`` from the source
   *  shape in each adapter, so the table never invents trust class out
   *  of band. */
  trustClass: TrustClass;
  /** Toggle handler. ``null`` means the row is not togglable (always-on, preview). */
  onToggle: ((next: boolean) => void) | null;
  /** Delete handler. ``null`` means the row is built-in / cannot be deleted. */
  onDelete: (() => void) | null;
  /** Optional one-line description shown beneath the title. */
  hint?: string;
  /** PR-F-UX6: composing primitives for a hybrid (groupId-shared) row.
   *  When present the RuleRowView renders a chevron + nested sub-rows.
   *  Each child is a normal per-rule row so the per-primitive toggle /
   *  delete affordances stay intact. */
  children?: RuleRow[];
}


// ---------------------------------------------------------------------------
// Adapters: each shape → RuleRow
// ---------------------------------------------------------------------------


function builtinToRow(
  preset: HarnessPresetItem,
  presetOverrides: Record<string, boolean>,
  pendingPresets: Set<string>,
  onToggle: (id: string, next: boolean) => void,
): RuleRow {
  const enforcement = preset.enforcement;
  const enabled = presetOverrides[preset.id] ?? preset.defaultEnabled;
  const state: RuleRow["state"] =
    enforcement === "always-on"
      ? "always-on"
      : enforcement === "preview"
        ? "preview"
        : enabled
          ? "enabled"
          : "disabled";
  const togglable = enforcement !== "always-on" && enforcement !== "preview";
  const pending = pendingPresets.has(preset.id);
  return {
    rowKey: `builtin:${preset.id}`,
    origin: "builtin",
    title: preset.title,
    scope: preset.domain,
    mode: preset.tier ?? "—",
    state,
    // Built-in PresetSeams have implicit conditions (the runtime wires
    // controls_refs deterministically); preview presets surface as
    // preview regardless of condition kind. Pass state through so the
    // helper picks the right bucket.
    trustClass: trustClassForPolicy({
      source: "preset_seam",
      state,
      condition: { kind: "none" },
    }),
    onToggle: togglable && !pending ? (next) => onToggle(preset.id, next) : null,
    onDelete: null,
    hint: preset.description,
  };
}


function customRuleToRow(
  rule: CustomRule,
  busy: boolean,
  onToggle: (rule: CustomRule, next: boolean) => void,
  onDelete: (id: string) => void,
): RuleRow {
  const ruleKind = rule.what?.kind ?? "rule";
  const ruleId = rule.id ?? `cr_${Math.random().toString(36).slice(2)}`;
  return {
    rowKey: `custom:${ruleId}`,
    origin: "custom",
    title: rule.id ?? "(unnamed rule)",
    scope: rule.scope ?? "always",
    mode: ruleKind,
    state: rule.enabled ? "enabled" : "disabled",
    // CustomRule's what.kind drives trust class: llm_criterion → advisory,
    // everything else (evidence_ref/shacl_constraint/tool_perm/...) →
    // deterministic. See trust-badge.ts for the full mapping.
    trustClass: trustClassForPolicy({
      source: "custom_rule",
      state: rule.enabled ? "enabled" : "disabled",
      condition: { kind: ruleKind },
    }),
    onToggle: busy ? null : (next) => onToggle(rule, next),
    onDelete: busy || !rule.id ? null : () => onDelete(rule.id!),
    hint: `${rule.firesAt} · ${rule.action}`,
  };
}


/**
 * PR-F-UX6 — render N custom rules sharing a `groupId` as one expandable
 * hybrid row. The summary row reports the kinds composed and exposes a
 * "Trust: hybrid" badge; the expanded view shows each composing primitive
 * as its own per-rule sub-row with the normal toggle/delete affordances.
 */
function customRuleGroupToRow(
  groupId: string,
  rules: CustomRule[],
  busy: boolean,
  onToggle: (rule: CustomRule, next: boolean) => void,
  onDelete: (id: string) => void,
): RuleRow {
  const kinds = Array.from(
    new Set(rules.map((r) => r.what?.kind ?? "rule")),
  );
  const allEnabled = rules.every((r) => r.enabled);
  const anyEnabled = rules.some((r) => r.enabled);
  const state: RuleRow["state"] = allEnabled
    ? "enabled"
    : anyEnabled
      ? "enabled"  // mixed → surface as enabled at the group level; per-primitive toggles surface state truthfully
      : "disabled";
  const scope = rules[0]?.scope ?? "always";
  const firesAt = rules[0]?.firesAt ?? "—";
  return {
    rowKey: `custom-group:${groupId}`,
    origin: "custom",
    title: `${groupId} (hybrid: ${kinds.join(" + ")})`,
    scope,
    mode: kinds.join(" + "),
    state,
    trustClass: "hybrid",
    onToggle: null,
    onDelete: null,
    hint: `${firesAt} · ${rules.length} composing primitives`,
    children: rules.map((rule) =>
      customRuleToRow(rule, busy, onToggle, onDelete),
    ),
  };
}


function seamSpecToRows(spec: SeamSpecDoc): RuleRow[] {
  // One row per action so the user sees what was actually mutated rather
  // than just a doc id. Origin badge stays "SeamSpec" for every row.
  return spec.actions.map((action: SeamSpecAction, idx) => {
    const ops = action.op === "add_seam" ? "adds" : "modifies";
    const wiringHint = action.wiring ? ` (${action.wiring})` : "";
    return {
      rowKey: `seamspec:${spec.id ?? ""}:${idx}`,
      origin: "seamspec",
      title: `${ops} ${action.preset_id}${wiringHint}`,
      scope: "—",
      mode: action.controls_kind ?? "validator",
      state: "enabled",
      // SeamSpec rewires the preset wiring at registration time — it is
      // a deterministic gate by construction (no LLM judgment), so the
      // helper always returns "deterministic" for seam_action.
      trustClass: trustClassForPolicy({
        source: "seam_spec",
        state: "enabled",
        condition: { kind: "seam_action" },
      }),
      // SeamSpec rows are managed atomically per-doc — no per-action
      // toggle/delete. The whole doc is removed from the Advanced page.
      onToggle: null,
      onDelete: null,
      hint: action.controls_refs
        ? `controls_refs: ${action.controls_refs.join(", ")}`
        : undefined,
    };
  });
}


// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------


export interface RulesTableProps {
  catalog: CustomizeCatalog["verification"];
  presetOverrides: Record<string, boolean>;
  pendingPresets: Set<string>;
  onTogglePreset: (id: string, next: boolean) => void;
  customRules: CustomRule[];
  customRuleBusy: boolean;
  onToggleCustomRule: (rule: CustomRule, enabled: boolean) => void;
  onDeleteCustomRule: (id: string) => void;
  seamSpecs: SeamSpecDoc[];
}


export function RulesTable(props: RulesTableProps): React.ReactElement {
  const {
    catalog,
    presetOverrides,
    pendingPresets,
    onTogglePreset,
    customRules,
    customRuleBusy,
    onToggleCustomRule,
    onDeleteCustomRule,
    seamSpecs,
  } = props;

  const rowsByOrigin = useMemo(() => {
    const out: Record<RuleOrigin, RuleRow[]> = {
      builtin: [],
      custom: [],
      "after-tool": [],
      seamspec: [],
    };
    for (const preset of catalog.harnessPresets) {
      out.builtin.push(
        builtinToRow(preset, presetOverrides, pendingPresets, onTogglePreset),
      );
    }
    // PR-F-UX6: bucket custom rules by groupId. Ungrouped rules render as
    // normal per-rule rows; grouped rules collapse into one expandable
    // hybrid row. Stored order is preserved within each group.
    const groupedBuckets: Map<string, CustomRule[]> = new Map();
    const ungrouped: CustomRule[] = [];
    for (const rule of customRules) {
      const gid = typeof rule.groupId === "string" && rule.groupId.trim()
        ? rule.groupId
        : null;
      if (gid !== null) {
        const bucket = groupedBuckets.get(gid) ?? [];
        bucket.push(rule);
        groupedBuckets.set(gid, bucket);
      } else {
        ungrouped.push(rule);
      }
    }
    for (const rule of ungrouped) {
      out.custom.push(
        customRuleToRow(
          rule,
          customRuleBusy,
          onToggleCustomRule,
          onDeleteCustomRule,
        ),
      );
    }
    for (const [gid, rules] of groupedBuckets) {
      out.custom.push(
        customRuleGroupToRow(
          gid,
          rules,
          customRuleBusy,
          onToggleCustomRule,
          onDeleteCustomRule,
        ),
      );
    }
    for (const spec of seamSpecs) {
      for (const row of seamSpecToRows(spec)) out.seamspec.push(row);
    }
    return out;
  }, [
    catalog,
    presetOverrides,
    pendingPresets,
    onTogglePreset,
    customRules,
    customRuleBusy,
    onToggleCustomRule,
    onDeleteCustomRule,
    seamSpecs,
  ]);

  const [originFilter, setOriginFilter] = useState<RuleOrigin | null>(null);

  const totals = {
    builtin: rowsByOrigin.builtin.length,
    custom: rowsByOrigin.custom.length,
    "after-tool": rowsByOrigin["after-tool"].length,
    seamspec: rowsByOrigin.seamspec.length,
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1">
        <span className="mr-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Filter
        </span>
        <FilterChip
          label="All"
          count={totals.builtin + totals.custom + totals["after-tool"] + totals.seamspec}
          active={originFilter === null}
          onClick={() => setOriginFilter(null)}
        />
        {ORIGIN_ORDER.map((o) => (
          <FilterChip
            key={o}
            label={ORIGIN_LABEL[o]}
            count={totals[o]}
            active={originFilter === o}
            tone={ORIGIN_TONE[o]}
            onClick={() => setOriginFilter(originFilter === o ? null : o)}
          />
        ))}
      </div>

      {ORIGIN_ORDER.map((o) => {
        if (originFilter !== null && originFilter !== o) return null;
        const rows = rowsByOrigin[o];
        if (rows.length === 0) return null;
        return (
          <OriginGroup
            key={o}
            origin={o}
            rows={rows}
            defaultOpen={originFilter === o || o === "builtin"}
          />
        );
      })}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Helpers — chip + group + row
// ---------------------------------------------------------------------------


function FilterChip({
  label,
  count,
  active,
  tone,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  tone?: string;
  onClick: () => void;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors ${
        active
          ? "bg-primary text-white"
          : tone
            ? `${tone} hover:opacity-80`
            : "bg-black/[0.05] text-secondary hover:bg-black/[0.10]"
      }`}
    >
      <span>{label}</span>
      <span className={`tabular-nums ${active ? "text-white/70" : "opacity-60"}`}>
        {count}
      </span>
    </button>
  );
}


function OriginGroup({
  origin,
  rows,
  defaultOpen,
}: {
  origin: RuleOrigin;
  rows: RuleRow[];
  defaultOpen: boolean;
}): React.ReactElement {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl">
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
      >
        <ChevronRight
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-secondary transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${ORIGIN_TONE[origin]}`}>
          {ORIGIN_LABEL[origin]}
        </span>
        <span className="flex-1 text-sm font-semibold text-foreground">
          {rows.length} {rows.length === 1 ? "rule" : "rules"}
        </span>
      </button>
      {open ? (
        <div className="divide-y divide-black/[0.04] border-t border-black/[0.04]">
          {rows.map((row) => (
            <RuleRowView key={row.rowKey} row={row} />
          ))}
        </div>
      ) : null}
    </section>
  );
}


function RuleRowView({ row }: { row: RuleRow }): React.ReactElement {
  const hasChildren = (row.children?.length ?? 0) > 0;
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="flex flex-col">
      <div className="flex items-start gap-3 px-4 py-3">
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setExpanded((p) => !p)}
            aria-label={`${expanded ? "Collapse" : "Expand"} composing primitives`}
            aria-expanded={expanded}
            className="mt-1 shrink-0 text-secondary"
          >
            <ChevronRight
              aria-hidden="true"
              className={`h-4 w-4 transition-transform ${
                expanded ? "rotate-90" : ""
              }`}
            />
          </button>
        ) : null}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">
            {row.title}
          </p>
          {row.hint ? (
            <p className="mt-0.5 truncate text-[11px] text-secondary/80">{row.hint}</p>
          ) : null}
          <p className="mt-1 flex gap-2 text-[10px] uppercase tracking-wider text-secondary/60">
            <span>scope: {row.scope}</span>
            <span>·</span>
            <span>mode: {row.mode}</span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <TrustBadge
            trustClass={row.trustClass}
            ariaLabel="Trust class for this policy"
          />
          <StatePill state={row.state} />
          {row.onToggle ? (
            <ToggleSwitch
              checked={row.state === "enabled"}
              onChange={row.onToggle}
              label={`Toggle ${row.title}`}
            />
          ) : null}
          {row.onDelete ? (
            <button
              type="button"
              onClick={row.onDelete}
              aria-label={`Delete ${row.title}`}
              className="text-secondary transition-colors hover:text-red-600"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          ) : null}
        </div>
      </div>
      {hasChildren && expanded ? (
        <div className="ml-7 divide-y divide-black/[0.04] border-t border-black/[0.04]">
          {row.children!.map((child) => (
            <RuleRowView key={child.rowKey} row={child} />
          ))}
        </div>
      ) : null}
    </div>
  );
}


function StatePill({ state }: { state: RuleRow["state"] }): React.ReactElement {
  const cls: Record<RuleRow["state"], string> = {
    enabled: "bg-emerald-500/15 text-emerald-700",
    disabled: "bg-black/[0.06] text-secondary",
    "always-on": "bg-emerald-700/15 text-emerald-800",
    preview: "bg-amber-500/10 text-amber-700",
  };
  const label: Record<RuleRow["state"], string> = {
    enabled: "on",
    disabled: "off",
    "always-on": "always-on",
    preview: "preview",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${cls[state]}`}
    >
      {label[state]}
    </span>
  );
}


function ToggleSwitch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
}): React.ReactElement {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
        checked ? "bg-primary" : "bg-black/[0.15]"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-4" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}
