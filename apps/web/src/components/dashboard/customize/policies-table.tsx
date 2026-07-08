"use client";

/**
 * PoliciesTable — unified single-list view over the four backend stores.
 *
 * Replaces ``RulesTable``'s origin-grouped surface with one flat list
 * driven by the :type:`RuleRow` adapter. Each row carries its own
 * togglable/editable/deletable affordance derived from ``policy.source``
 * so the consumer does not need per-source branching.
 *
 * Action routing
 * --------------
 * Toggle / delete callbacks dispatch by ``policy.source``:
 *   - preset_seam     → ``onTogglePreset(id, next)``
 *   - custom_rule     → ``onToggleCustomRule(rule, next)`` / ``onDeleteCustomRule(id)``
 *   - dashboard_check → ``onToggleDashboardCheck(check, next)`` / ``onDeleteDashboardCheck(id)``
 *   - seam_spec       → ``onDeleteSeamSpec(specId)`` (toggle not supported)
 *
 * The PoliciesSection mount is the single place that knows how to call
 * the backend; this table is a pure render.
 */

import { ChevronRight, Trash2 } from "lucide-react";
import React, { useMemo, useState } from "react";

import type { CustomRule } from "@/lib/customize-api";
import type { DashboardCheck } from "@/lib/packs-dashboard-api";
import type { RuleRow, PolicyOrigin, PolicySource } from "@/lib/policy-model";
import { Switch } from "@/components/ui/_ds";


export interface PoliciesTableProps {
  policies: RuleRow[];
  pendingPresets: Set<string>;
  busy: boolean;
  onTogglePreset: (presetId: string, next: boolean) => void;
  onToggleCustomRule: (rule: CustomRule, next: boolean) => void;
  onDeleteCustomRule: (id: string) => void;
  onToggleDashboardCheck: (check: DashboardCheck, next: boolean) => void;
  onDeleteDashboardCheck: (id: string) => void;
  onDeleteSeamSpec: (specId: string) => void;
  onEdit?: (policy: RuleRow) => void;
  /** PR-U4a reverse cross-link: policy id maps to the display names of the modes
   * that scope it. Rows whose id is present show a "scoped in N modes" badge so the
   * operator can see, from the Rules tab, which stances force a rule on (the
   * forward direction lives in the Modes editor's scoped-rule picker). */
  scopedInModes?: Readonly<Record<string, ReadonlyArray<string>>>;
}


const ORIGIN_TONE: Record<PolicyOrigin, string> = {
  builtin: "bg-emerald-500/10 text-emerald-700",
  user: "bg-blue-500/10 text-blue-700",
};


const SOURCE_LABEL: Record<PolicySource, string> = {
  preset_seam: "Built-in",
  custom_rule: "Custom",
  dashboard_check: "After-tool",
  seam_spec: "Override",
};


type OriginFilter = PolicyOrigin | null;


export function PoliciesTable({
  policies,
  pendingPresets,
  busy,
  onTogglePreset,
  onToggleCustomRule,
  onDeleteCustomRule,
  onToggleDashboardCheck,
  onDeleteDashboardCheck,
  onDeleteSeamSpec,
  onEdit,
  scopedInModes,
}: PoliciesTableProps): React.ReactElement {
  const [originFilter, setOriginFilter] = useState<OriginFilter>(null);
  const [scopeFilter, setScopeFilter] = useState<string | null>(null);
  const [firesAtFilter, setFiresAtFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  // Pre-compute the value sets for the chip rows so empty buckets don't
  // surface as zero-count chips (they would mislead the user).
  const scopes = useMemo(
    () => [...new Set(policies.map((p) => p.when.scope))].sort(),
    [policies],
  );
  const firesAts = useMemo(
    () => [...new Set(policies.map((p) => p.when.firesAt))].sort(),
    [policies],
  );

  const totals = useMemo(() => {
    let builtin = 0;
    let user = 0;
    for (const p of policies) {
      if (p.origin === "builtin") builtin++;
      else user++;
    }
    return { builtin, user, all: policies.length };
  }, [policies]);

  const needle = search.trim().toLowerCase();
  const visible = useMemo(
    () =>
      policies.filter((p) => {
        if (originFilter !== null && p.origin !== originFilter) return false;
        if (scopeFilter !== null && p.when.scope !== scopeFilter) return false;
        if (firesAtFilter !== null && p.when.firesAt !== firesAtFilter) return false;
        if (needle) {
          const hay = `${p.name} ${p.description} ${p.condition.summary}`.toLowerCase();
          if (!hay.includes(needle)) return false;
        }
        return true;
      }),
    [policies, originFilter, scopeFilter, firesAtFilter, needle],
  );

  // Group by origin so the head of the list is the user's own rules and the
  // long built-in catalog can be collapsed if needed. PR-P2: rules whose
  // enforcement is not wired yet (state "preview") are pulled OUT of the live
  // groups into a collapsed "Dormant" section so the main list only shows rules
  // that actually gate a turn.
  const isDormant = (p: RuleRow) => p.state === "preview";
  const userPolicies = visible.filter((p) => p.origin === "user" && !isDormant(p));
  const builtinPolicies = visible.filter((p) => p.origin === "builtin" && !isDormant(p));
  const dormantPolicies = visible.filter(isDormant);

  return (
    <div className="space-y-4">
      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by name, description, or condition…"
        aria-label="Search rules"
        className="w-full rounded-lg border border-black/[0.08] bg-white px-3 py-1.5 text-xs text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
      />

      <FilterRow label="Origin">
        <FilterChip
          label="All"
          count={totals.all}
          active={originFilter === null}
          onClick={() => setOriginFilter(null)}
        />
        <FilterChip
          label="Custom"
          count={totals.user}
          active={originFilter === "user"}
          tone="bg-blue-500/10 text-blue-700"
          onClick={() => setOriginFilter(originFilter === "user" ? null : "user")}
        />
        <FilterChip
          label="Built-in"
          count={totals.builtin}
          active={originFilter === "builtin"}
          tone="bg-emerald-500/10 text-emerald-700"
          onClick={() => setOriginFilter(originFilter === "builtin" ? null : "builtin")}
        />
      </FilterRow>

      {scopes.length > 1 ? (
        <FilterRow label="Scope">
          <FilterChip
            label="All"
            count={policies.length}
            active={scopeFilter === null}
            onClick={() => setScopeFilter(null)}
          />
          {scopes.map((s) => {
            const count = policies.filter((p) => p.when.scope === s).length;
            return (
              <FilterChip
                key={s}
                label={s}
                count={count}
                active={scopeFilter === s}
                onClick={() => setScopeFilter(scopeFilter === s ? null : s)}
              />
            );
          })}
        </FilterRow>
      ) : null}

      {firesAts.length > 1 ? (
        <FilterRow label="When">
          <FilterChip
            label="All"
            count={policies.length}
            active={firesAtFilter === null}
            onClick={() => setFiresAtFilter(null)}
          />
          {firesAts.map((f) => {
            const count = policies.filter((p) => p.when.firesAt === f).length;
            return (
              <FilterChip
                key={f}
                label={f}
                count={count}
                active={firesAtFilter === f}
                onClick={() => setFiresAtFilter(firesAtFilter === f ? null : f)}
              />
            );
          })}
        </FilterRow>
      ) : null}

      {userPolicies.length > 0 ? (
        <Group
          title="Your rules"
          rows={userPolicies}
          pendingPresets={pendingPresets}
          busy={busy}
          onTogglePreset={onTogglePreset}
          onToggleCustomRule={onToggleCustomRule}
          onDeleteCustomRule={onDeleteCustomRule}
          onToggleDashboardCheck={onToggleDashboardCheck}
          onDeleteDashboardCheck={onDeleteDashboardCheck}
          onDeleteSeamSpec={onDeleteSeamSpec}
          onEdit={onEdit}
          scopedInModes={scopedInModes}
          defaultOpen
        />
      ) : null}
      {builtinPolicies.length > 0 ? (
        <Group
          title="Built-in"
          rows={builtinPolicies}
          pendingPresets={pendingPresets}
          busy={busy}
          onTogglePreset={onTogglePreset}
          onToggleCustomRule={onToggleCustomRule}
          onDeleteCustomRule={onDeleteCustomRule}
          onToggleDashboardCheck={onToggleDashboardCheck}
          onDeleteDashboardCheck={onDeleteDashboardCheck}
          onDeleteSeamSpec={onDeleteSeamSpec}
          onEdit={onEdit}
          scopedInModes={scopedInModes}
          defaultOpen={userPolicies.length === 0}
        />
      ) : null}
      {dormantPolicies.length > 0 ? (
        <Group
          title={`Dormant · not wired yet (${dormantPolicies.length})`}
          rows={dormantPolicies}
          pendingPresets={pendingPresets}
          busy={busy}
          onTogglePreset={onTogglePreset}
          onToggleCustomRule={onToggleCustomRule}
          onDeleteCustomRule={onDeleteCustomRule}
          onToggleDashboardCheck={onToggleDashboardCheck}
          onDeleteDashboardCheck={onDeleteDashboardCheck}
          onDeleteSeamSpec={onDeleteSeamSpec}
          onEdit={onEdit}
          scopedInModes={scopedInModes}
          defaultOpen={false}
        />
      ) : null}
      {visible.length === 0 ? (
        <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs leading-relaxed text-secondary">
          No rules match this filter.
        </p>
      ) : null}
    </div>
  );
}


function FilterRow({
  label,
  children,
}: { label: string; children: React.ReactNode }): React.ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="mr-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
        {label}
      </span>
      {children}
    </div>
  );
}


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
      <span
        className={`tabular-nums ${
          active ? "text-white/70" : "opacity-60"
        }`}
      >
        {count}
      </span>
    </button>
  );
}


interface GroupProps extends Omit<PoliciesTableProps, "policies"> {
  title: string;
  rows: RuleRow[];
  defaultOpen: boolean;
}


function Group({
  title,
  rows,
  defaultOpen,
  pendingPresets,
  busy,
  onTogglePreset,
  onToggleCustomRule,
  onDeleteCustomRule,
  onToggleDashboardCheck,
  onDeleteDashboardCheck,
  onDeleteSeamSpec,
  onEdit,
  scopedInModes,
}: GroupProps): React.ReactElement {
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
        <span className="flex-1 text-sm font-semibold text-foreground">
          {title}{" "}
          <span className="text-xs font-normal text-secondary">
            ({rows.length})
          </span>
        </span>
      </button>
      {open ? (
        <div className="divide-y divide-black/[0.04] border-t border-black/[0.04]">
          {rows.map((p) => (
            <PolicyRowView
              key={p.id}
              policy={p}
              pending={
                p.rawSource.kind === "preset_seam"
                  ? pendingPresets.has(p.rawSource.preset.id)
                  : false
              }
              busy={busy}
              onTogglePreset={onTogglePreset}
              onToggleCustomRule={onToggleCustomRule}
              onDeleteCustomRule={onDeleteCustomRule}
              onToggleDashboardCheck={onToggleDashboardCheck}
              onDeleteDashboardCheck={onDeleteDashboardCheck}
              onDeleteSeamSpec={onDeleteSeamSpec}
              onEdit={onEdit}
              scopedModes={scopedInModes?.[p.id]}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}


function PolicyRowView({
  policy,
  pending,
  busy,
  onTogglePreset,
  onToggleCustomRule,
  onDeleteCustomRule,
  onToggleDashboardCheck,
  onDeleteDashboardCheck,
  onDeleteSeamSpec,
  onEdit,
  scopedModes,
}: {
  policy: RuleRow;
  pending: boolean;
  busy: boolean;
  onTogglePreset: (id: string, next: boolean) => void;
  onToggleCustomRule: (rule: CustomRule, next: boolean) => void;
  onDeleteCustomRule: (id: string) => void;
  onToggleDashboardCheck: (check: DashboardCheck, next: boolean) => void;
  onDeleteDashboardCheck: (id: string) => void;
  onDeleteSeamSpec: (specId: string) => void;
  onEdit?: (policy: RuleRow) => void;
  scopedModes?: ReadonlyArray<string>;
}): React.ReactElement {
  const checked = policy.state === "enabled" || policy.state === "always-on";
  const handleToggle = (next: boolean) => {
    if (!policy.togglable) return;
    switch (policy.rawSource.kind) {
      case "preset_seam":
        onTogglePreset(policy.rawSource.preset.id, next);
        return;
      case "custom_rule":
        onToggleCustomRule(policy.rawSource.rule, next);
        return;
      case "dashboard_check":
        onToggleDashboardCheck(policy.rawSource.check, next);
        return;
      case "seam_spec":
        // Seam-spec rows don't support per-action toggling — the whole doc
        // is enabled or deleted.
        return;
    }
  };
  const handleDelete = () => {
    if (!policy.deletable) return;
    switch (policy.rawSource.kind) {
      case "custom_rule":
        if (policy.rawSource.rule.id) onDeleteCustomRule(policy.rawSource.rule.id);
        return;
      case "dashboard_check":
        onDeleteDashboardCheck(policy.rawSource.check.id);
        return;
      case "seam_spec":
        if (policy.rawSource.spec.id) onDeleteSeamSpec(policy.rawSource.spec.id);
        return;
      case "preset_seam":
        return; // never deletable
    }
  };

  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium text-foreground">{policy.name}</p>
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${ORIGIN_TONE[policy.origin]}`}
          >
            {SOURCE_LABEL[policy.source]}
          </span>
          {scopedModes && scopedModes.length > 0 ? (
            <span
              title={`Forced on in this mode${scopedModes.length === 1 ? "" : "s"}: ${scopedModes.join(", ")}`}
              className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary"
            >
              scoped in {scopedModes.length} mode{scopedModes.length === 1 ? "" : "s"}
            </span>
          ) : null}
        </div>
        {policy.description ? (
          <p className="mt-0.5 truncate text-[11px] text-secondary/80">
            {policy.description}
          </p>
        ) : null}
        <p className="mt-1 flex gap-2 text-[10px] uppercase tracking-wider text-secondary/60">
          <span>when: {policy.when.scope} · {policy.when.firesAt}</span>
          <span>·</span>
          <span>action: {policy.action}</span>
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <StatePill state={policy.state} />
        {policy.togglable ? (
          <Switch
            checked={checked}
            disabled={pending || busy}
            onToggle={async (next) => handleToggle(next)}
            labelOn={`Disable ${policy.name}`}
            labelOff={`Enable ${policy.name}`}
          />
        ) : null}
        {policy.editable && onEdit ? (
          <button
            type="button"
            onClick={() => onEdit(policy)}
            className="rounded px-2 py-0.5 text-[11px] font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground"
          >
            Edit
          </button>
        ) : null}
        {policy.deletable ? (
          <button
            type="button"
            onClick={handleDelete}
            disabled={busy}
            aria-label={`Delete ${policy.name}`}
            className="text-secondary transition-colors hover:text-red-600 disabled:opacity-40"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        ) : null}
      </div>
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
    preview: "not wired",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${cls[state]}`}
    >
      {label[state]}
    </span>
  );
}


