"use client";

/**
 * PolicyCardList — the primary Policies surface (PR-2, "policies-first
 * surface unification").
 *
 * The USER's unit of intent is a *policy* (a named 1..N-rule bundle). This
 * component renders one card per policy, regardless of enforcement strength
 * (block / ask / audit / nudge) or origin (user / first-party / built-in).
 * The flat per-rule list (the implementation detail) lives inside each card's
 * drill-down and, for debugging, under an "Advanced" disclosure below the list
 * (see customize-hub.tsx).
 *
 * Sources assembled into cards:
 *   - **Your policies**   — `catalog.policies` with origin=user, plus any
 *     user rule row (custom_rule / dashboard_check / seam_spec) NOT referenced
 *     by a catalog policy, adapted as an implicit 1-rule card.
 *   - **First-party**     — `catalog.policies` with origin=builtin.
 *   - **Built-in**        — the 36 harness presets as 1-rule adapter cards,
 *     grouped into COLLAPSED domain sections (always-on / coding / research /
 *     delivery) by their scope metadata.
 *
 * Precedence rule (kills the "policy shatters into rules" bug): a rule row
 * whose id is referenced by any catalog policy renders ONLY inside that
 * policy's drill-down, never as its own top-level card.
 */

import { ChevronRight, Trash2 } from "lucide-react";
import React, { useMemo, useState } from "react";

import type {
  CitationGateModeDescriptor,
  CustomRule,
  PolicyCatalogEntry,
} from "@/lib/customize-api";
import type { DashboardCheck } from "@/lib/packs-dashboard-api";
import type { PolicyOrigin, RuleRow } from "@/lib/policy-model";
import { Select, Switch, type SelectOption } from "@/components/ui/_ds";

// ---------------------------------------------------------------------------
// Action spectrum: strongest member wins (BLOCK > ASK > AUDIT > NUDGE).
// ---------------------------------------------------------------------------

const ACTION_RANK: Record<string, number> = {
  block: 4,
  ask: 3,
  audit: 2,
  nudge: 1,
};

function strongestAction(rows: RuleRow[]): string | null {
  let best: string | null = null;
  let bestRank = 0;
  for (const r of rows) {
    const a = (r.action ?? "").toLowerCase();
    const rank = ACTION_RANK[a] ?? 0;
    if (rank > bestRank) {
      bestRank = rank;
      best = a;
    }
  }
  return best;
}

const ACTION_TONE: Record<string, string> = {
  block: "bg-red-500/12 text-red-700",
  ask: "bg-amber-500/12 text-amber-700",
  audit: "bg-sky-500/12 text-sky-700",
  nudge: "bg-violet-500/12 text-violet-700",
};

// ---------------------------------------------------------------------------
// Card view models
// ---------------------------------------------------------------------------

type CardKind = "native" | "adapter";

interface PolicyCardVM {
  key: string;
  kind: CardKind;
  displayName: string;
  intent: string;
  origin: PolicyOrigin;
  originLabel: string;
  members: RuleRow[];
  /**
   * Member rule ids that did NOT resolve to a dashboard rule row (runtime-
   * native members of first-party policies, e.g.
   * `verify_before_replying.claim_citation`). They are real rules — the card
   * must count them and list them read-only instead of lying "0 rules".
   */
  unresolvedMemberIds: string[];
  hasBinding: boolean;
  reviewVerdict: string;
  /** Names of modes that force this policy on (union of policy: + member refs). */
  scopedModes: string[];
  /** Optional explicit action chip when there are no member rules (nudges). */
  actionHint?: string;
  /** How the policy-level toggle behaves. */
  toggle:
    | { kind: "native"; policyId: string; enabledState: PolicyCatalogEntry["enabledState"] }
    // PR-3 — first-party policy opt-out (verify_before_replying): a real single
    // Switch routed to PATCH /v1/app/customize/builtin-policies/{id}.
    | { kind: "builtin-policy"; policyId: string; enabled: boolean }
    // PR-3 — control-plane behavior nudge: a real single Switch routed to
    // PATCH /v1/app/customize/control-plane/{id}.
    | { kind: "control-plane"; behaviorId: string; enabled: boolean }
    | { kind: "adapter-row"; row: RuleRow }
    | { kind: "floor" }; // always-on, no toggle
  /**
   * Present only on the floored `source_citation` policy: the 3-way gate-mode
   * opt-DOWN descriptor (repair/audit/off). Its always-on floor card renders a
   * selector under the badge to step enforcement STRICTNESS down. The boolean
   * disable stays floored (no off switch).
   */
  gateMode?: CitationGateModeDescriptor;
  /** Delete affordance: native policies cascade member rules; adapters can't delete. */
  deletable: boolean;
}

// ---------------------------------------------------------------------------
// Toggle / delete callback surface — mirrors the flat table's per-row routing.
// ---------------------------------------------------------------------------

export interface PolicyCardListProps {
  /** Native policy summaries from the catalog (user + first-party builtin). */
  catalogPolicies: PolicyCatalogEntry[];
  /** The flat, unified rule-row list (source of member drill-down + adapters). */
  ruleRows: RuleRow[];
  pendingPresets: Set<string>;
  busy: boolean;
  /** policyId / unified rule-row id -> the display names of the modes that scope it. */
  scopedInModes?: Readonly<Record<string, ReadonlyArray<string>>>;
  /** Native USER policy toggle (member cascade). Caller reloads on success. */
  onTogglePolicy: (policyId: string, next: boolean) => void;
  /** Native USER policy delete (cascades member rules client-side). */
  onDeletePolicy: (policy: PolicyCatalogEntry) => void;
  // PR-3 — first-party policy opt-out + control-plane behavior toggle routing.
  /** Toggle a first-party builtin policy (verify_before_replying) opt-out. */
  onToggleBuiltinPolicy: (policyId: string, next: boolean) => void;
  /** Toggle an in-context control-plane behavior nudge. */
  onToggleControlPlane: (behaviorId: string, next: boolean) => void;
  /** Ids of builtin policies whose PATCH is in flight. */
  pendingBuiltinPolicies?: Set<string>;
  /** Ids of control-plane behaviors whose PATCH is in flight. */
  pendingControlPlane?: Set<string>;
  // source_citation gate-mode opt-DOWN (repair/audit/off). The floored citation
  // card renders a 3-way selector under its always-on badge. The boolean disable
  // stays floored; this only steps enforcement STRICTNESS down.
  /** Current explicit gate-mode override, or `null` (fall back to the catalog value). */
  citationGateMode?: string | null;
  /** Persist a new gate mode. Absent = no selector rendered on the floor card. */
  onCitationGateModeChange?: (mode: string) => void;
  /** True while the gate-mode PATCH is in flight (disables the selector). */
  citationGateModePending?: boolean;
  /** Last gate-mode PATCH error, surfaced under the selector. */
  citationGateModeError?: string | null;
  // Adapter-row routing (built-in presets + orphan user rows).
  onTogglePreset: (presetId: string, next: boolean) => void;
  onToggleCustomRule: (rule: CustomRule, next: boolean) => void;
  onDeleteCustomRule: (id: string) => void;
  onToggleDashboardCheck: (check: DashboardCheck, next: boolean) => void;
  onDeleteDashboardCheck: (id: string) => void;
  onDeleteSeamSpec: (specId: string) => void;
}

const ORIGIN_LABEL: Record<string, string> = {
  user: "Custom",
  builtin: "First-party",
};

// ---------------------------------------------------------------------------

export function PolicyCardList(props: PolicyCardListProps): React.ReactElement {
  const {
    catalogPolicies,
    ruleRows,
    scopedInModes,
  } = props;

  // Index rule rows by their raw (unprefixed) rule id so catalog policies'
  // `ruleIds` (raw ids) join to the prefixed unified rows.
  const rowByRuleId = useMemo(() => {
    const m = new Map<string, RuleRow>();
    for (const row of ruleRows) {
      if (row.rawSource.kind === "custom_rule" && row.rawSource.rule.id) {
        m.set(row.rawSource.rule.id, row);
      }
    }
    return m;
  }, [ruleRows]);

  // Precedence: rule rows referenced by any catalog policy are members-only.
  const referencedRuleIds = useMemo(() => {
    const s = new Set<string>();
    for (const p of catalogPolicies) for (const rid of p.ruleIds) s.add(rid);
    return s;
  }, [catalogPolicies]);

  const scopedFor = (unifiedId: string): string[] =>
    (scopedInModes?.[unifiedId] as string[] | undefined) ?? [];

  // Build native cards (Your policies + First-party) from catalog.policies.
  const nativeCards = useMemo<PolicyCardVM[]>(() => {
    return catalogPolicies.map((p) => {
      const members = p.ruleIds
        .map((rid) => rowByRuleId.get(rid))
        .filter((r): r is RuleRow => r !== undefined);
      // Runtime-native members (builtin policies) have no dashboard row;
      // they still count and list read-only in the drill-down.
      const unresolvedMemberIds = p.ruleIds.filter(
        (rid) => !rowByRuleId.has(rid),
      );
      // Mode-scope: the policy: ref plus any legacy member-row ref.
      const scopedSet = new Set<string>(scopedFor(`policy:${p.id}`));
      for (const m of members) for (const name of scopedFor(m.id)) scopedSet.add(name);
      const isFloor = p.origin === "builtin" && !p.userDisableable;
      const source = p.source ?? "policy";
      // Route the policy-level toggle by the backend `source` discriminator
      // (PR-3). A floor always wins (no toggle); otherwise builtinPolicy /
      // controlPlane get their own real single Switch, and store-backed user
      // policies keep the member-cascade native toggle.
      let toggle: PolicyCardVM["toggle"];
      if (isFloor) {
        toggle = { kind: "floor" as const };
      } else if (source === "builtinPolicy") {
        toggle = {
          kind: "builtin-policy" as const,
          policyId: p.id,
          enabled: p.enabledState === "on",
        };
      } else if (source === "controlPlane") {
        toggle = {
          kind: "control-plane" as const,
          behaviorId: p.id,
          enabled: p.enabledState === "on",
        };
      } else {
        toggle = {
          kind: "native" as const,
          policyId: p.id,
          enabledState: p.enabledState,
        };
      }
      return {
        key: `native:${p.id}`,
        kind: "native" as const,
        displayName: p.displayName,
        intent: p.intent,
        origin: p.origin,
        originLabel: ORIGIN_LABEL[p.origin] ?? p.origin,
        members,
        unresolvedMemberIds,
        hasBinding: p.hasBinding,
        reviewVerdict: p.reviewVerdict,
        scopedModes: [...scopedSet],
        actionHint: p.actionHint,
        toggle,
        // Only the floored source_citation policy carries a gate-mode descriptor.
        gateMode: p.gateMode,
        deletable: p.origin === "user",
      };
    });
  }, [catalogPolicies, rowByRuleId, scopedInModes]);

  // Adapter cards for the built-in presets (grouped by domain) + orphan user rows.
  const { orphanUserCards, presetCardsByDomain } = useMemo(() => {
    const orphan: PolicyCardVM[] = [];
    const byDomain = new Map<string, PolicyCardVM[]>();
    for (const row of ruleRows) {
      // Skip rows already represented as a native-policy member.
      if (
        row.rawSource.kind === "custom_rule" &&
        row.rawSource.rule.id &&
        referencedRuleIds.has(row.rawSource.rule.id)
      ) {
        continue;
      }
      const vm = adapterCardFor(row, scopedFor(row.id));
      if (row.source === "preset_seam") {
        const domain = row.when.scope || "other";
        const bucket = byDomain.get(domain) ?? [];
        bucket.push(vm);
        byDomain.set(domain, bucket);
      } else {
        orphan.push(vm);
      }
    }
    return { orphanUserCards: orphan, presetCardsByDomain: byDomain };
  }, [ruleRows, referencedRuleIds, scopedInModes]);

  const userNative = nativeCards.filter((c) => c.origin === "user");
  const firstParty = nativeCards.filter((c) => c.origin === "builtin");
  const yourPolicies = [...userNative, ...orphanUserCards];

  // Stable domain ordering for the built-in sections.
  const DOMAIN_ORDER = ["always-on", "coding", "research", "delivery"];
  const domains = [...presetCardsByDomain.keys()].sort((a, b) => {
    const ia = DOMAIN_ORDER.indexOf(a);
    const ib = DOMAIN_ORDER.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  const totalCards =
    yourPolicies.length +
    firstParty.length +
    domains.reduce((n, d) => n + (presetCardsByDomain.get(d)?.length ?? 0), 0);

  return (
    <div className="space-y-6" data-testid="policy-card-list">
      {totalCards === 0 ? (
        <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs leading-relaxed text-secondary">
          No policies yet. Use “Add policy” to author one.
        </p>
      ) : null}

      {yourPolicies.length > 0 ? (
        <Section title="Your policies" count={yourPolicies.length} defaultOpen>
          {yourPolicies.map((c) => (
            <PolicyCard key={c.key} vm={c} {...props} />
          ))}
        </Section>
      ) : null}

      {firstParty.length > 0 ? (
        <Section title="First-party" count={firstParty.length} defaultOpen>
          {firstParty.map((c) => (
            <PolicyCard key={c.key} vm={c} {...props} />
          ))}
        </Section>
      ) : null}

      {domains.length > 0 ? (
        <div className="space-y-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
            Built-in
          </p>
          {domains.map((domain) => {
            const cards = presetCardsByDomain.get(domain) ?? [];
            return (
              <Section
                key={domain}
                title={domain === "always-on" ? "Always-on" : titleCase(domain)}
                count={cards.length}
                defaultOpen={false}
              >
                {cards.map((c) => (
                  <PolicyCard key={c.key} vm={c} {...props} />
                ))}
              </Section>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * Human-readable name for a runtime-native member id. Builtin member ids use
 * the `<policy>.<member>` namespace (e.g. `verify_before_replying.evidence_audit`);
 * show the member part with word separators expanded.
 */
function formatRuntimeMemberName(rid: string): string {
  const member = rid.includes(".") ? rid.slice(rid.indexOf(".") + 1) : rid;
  return member.replace(/[_-]+/g, " ");
}

/** Adapter: build a 1-rule policy card view model from a flat rule row. */
function adapterCardFor(row: RuleRow, scopedModes: string[]): PolicyCardVM {
  const isFloor = row.state === "always-on";
  return {
    key: `adapter:${row.id}`,
    kind: "adapter",
    displayName: row.name,
    intent: row.description,
    origin: row.origin,
    originLabel: row.origin === "builtin" ? "Built-in" : "Custom",
    members: [row],
    unresolvedMemberIds: [],
    hasBinding: false,
    reviewVerdict: "unreviewed",
    scopedModes,
    toggle:
      isFloor || !row.togglable ? { kind: "floor" } : { kind: "adapter-row", row },
    deletable: row.deletable,
  };
}

// ---------------------------------------------------------------------------
// Section (collapsible group of cards)
// ---------------------------------------------------------------------------

function Section({
  title,
  count,
  defaultOpen,
  children,
}: {
  title: string;
  count: number;
  defaultOpen: boolean;
  children: React.ReactNode;
}): React.ReactElement {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left"
      >
        <ChevronRight
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-secondary transition-transform ${open ? "rotate-90" : ""}`}
        />
        <span className="text-sm font-semibold text-foreground">
          {title}{" "}
          <span className="text-xs font-normal text-secondary">({count})</span>
        </span>
      </button>
      {open ? <div className="flex flex-col gap-3">{children}</div> : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// PolicyCard
// ---------------------------------------------------------------------------

function PolicyCard({
  vm,
  busy,
  pendingPresets,
  onTogglePolicy,
  onDeletePolicy,
  onTogglePreset,
  onToggleCustomRule,
  onDeleteCustomRule,
  onToggleDashboardCheck,
  onDeleteDashboardCheck,
  onDeleteSeamSpec,
  onToggleBuiltinPolicy,
  onToggleControlPlane,
  pendingBuiltinPolicies,
  pendingControlPlane,
  citationGateMode,
  onCitationGateModeChange,
  citationGateModePending,
  citationGateModeError,
  catalogPolicies,
}: { vm: PolicyCardVM } & PolicyCardListProps): React.ReactElement {
  // Strongest member action wins; when there are no members (control-plane
  // nudge adapters) fall back to the explicit actionHint so the chip still
  // renders (e.g. NUDGE) — design D4.
  const action = strongestAction(vm.members) ?? vm.actionHint?.toLowerCase() ?? null;
  const firesAt = [...new Set(vm.members.map((m) => m.when.firesAt).filter(Boolean))];
  // "N of M rules on" note for mixed native policies.
  const memberOnCount = vm.members.filter(
    (m) => m.state === "enabled" || m.state === "always-on",
  ).length;

  const isMixed = vm.toggle.kind === "native" && vm.toggle.enabledState === "mixed";
  const isManaged = vm.toggle.kind === "native" && vm.toggle.enabledState === "managed";
  const nativeChecked =
    vm.toggle.kind === "native" && vm.toggle.enabledState === "on";

  // Forced-on honesty: off globally but scoped in an active-able mode.
  const globallyOff =
    (vm.toggle.kind === "native" &&
      (vm.toggle.enabledState === "off" || vm.toggle.enabledState === "mixed")) ||
    (vm.toggle.kind === "builtin-policy" && !vm.toggle.enabled) ||
    (vm.toggle.kind === "control-plane" && !vm.toggle.enabled) ||
    (vm.toggle.kind === "adapter-row" &&
      !(vm.toggle.row.state === "enabled" || vm.toggle.row.state === "always-on"));
  const forcedOn = globallyOff && vm.scopedModes.length > 0;

  const handleAdapterToggle = (next: boolean) => {
    if (vm.toggle.kind !== "adapter-row") return;
    const row = vm.toggle.row;
    switch (row.rawSource.kind) {
      case "preset_seam":
        onTogglePreset(row.rawSource.preset.id, next);
        return;
      case "custom_rule":
        onToggleCustomRule(row.rawSource.rule, next);
        return;
      case "dashboard_check":
        onToggleDashboardCheck(row.rawSource.check, next);
        return;
      case "seam_spec":
        return; // seam specs are enabled/deleted whole, not per-action
    }
  };

  const handleAdapterDelete = () => {
    const row = vm.members[0];
    if (!row) return;
    switch (row.rawSource.kind) {
      case "custom_rule":
        if (row.rawSource.rule.id) onDeleteCustomRule(row.rawSource.rule.id);
        return;
      case "dashboard_check":
        onDeleteDashboardCheck(row.rawSource.check.id);
        return;
      case "seam_spec":
        if (row.rawSource.spec.id) onDeleteSeamSpec(row.rawSource.spec.id);
        return;
      case "preset_seam":
        return;
    }
  };

  const presetPending =
    vm.toggle.kind === "adapter-row" &&
    vm.toggle.row.rawSource.kind === "preset_seam" &&
    pendingPresets.has(vm.toggle.row.rawSource.preset.id);

  return (
    <div
      className="rounded-xl border border-black/[0.06] bg-white p-4"
      data-testid={`policy-card-${vm.key}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-semibold text-foreground">
              {vm.displayName}
            </span>
            <span className="shrink-0 rounded-full bg-black/[0.05] px-2 py-0.5 text-[10px] font-medium text-secondary">
              {vm.originLabel}
            </span>
            <ReviewBadge verdict={vm.reviewVerdict} />
          </div>
          {vm.intent ? (
            <p className="mt-1 text-xs italic text-secondary/80">“{vm.intent}”</p>
          ) : null}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px]">
            {vm.members.length + vm.unresolvedMemberIds.length > 0 ? (
              <span className="text-secondary/70">
                {vm.members.length + vm.unresolvedMemberIds.length} rule
                {vm.members.length + vm.unresolvedMemberIds.length === 1
                  ? ""
                  : "s"}
                {vm.members.length === 0 ? " · runtime-managed" : ""}
              </span>
            ) : null}
            {action ? (
              <span
                className={`rounded-full px-2 py-0.5 font-medium uppercase tracking-wide ${ACTION_TONE[action] ?? "bg-black/[0.05] text-secondary"}`}
              >
                {action}
              </span>
            ) : null}
            {firesAt.map((f) => (
              <span
                key={f}
                className="rounded-full bg-black/[0.04] px-2 py-0.5 uppercase tracking-wide text-secondary/70"
              >
                {f}
              </span>
            ))}
            {vm.scopedModes.length > 0 ? (
              <span
                title={`Scoped in: ${vm.scopedModes.join(", ")}`}
                className="rounded-full bg-primary/10 px-2 py-0.5 font-medium text-primary"
              >
                scoped in {vm.scopedModes.join(", ")}
              </span>
            ) : null}
          </div>
          {forcedOn ? (
            <p className="mt-1 text-[10px] font-medium text-amber-700">
              Off globally · forced on in {vm.scopedModes.join(", ")} mode
              {vm.scopedModes.length === 1 ? "" : "s"}
            </p>
          ) : null}
          {isMixed ? (
            <p className="mt-1 text-[10px] text-secondary/70">
              {memberOnCount} of {vm.members.length} rules on
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {vm.toggle.kind === "floor" ? (
            <span className="inline-flex items-center rounded-full bg-emerald-700/15 px-2 py-0.5 text-[10px] font-medium text-emerald-800">
              always-on
            </span>
          ) : isManaged ? (
            <span
              title="This policy's members are runtime-managed — there is nothing to toggle here."
              className="inline-flex items-center rounded-full bg-black/[0.06] px-2 py-0.5 text-[10px] font-medium text-secondary"
            >
              managed
            </span>
          ) : vm.toggle.kind === "native" ? (
            <div className={isMixed ? "opacity-70" : undefined} title={isMixed ? "Some member rules are on, some off" : undefined}>
              <Switch
                checked={nativeChecked}
                disabled={busy}
                onToggle={async (next) => onTogglePolicy(vm.toggle.kind === "native" ? vm.toggle.policyId : "", next)}
                labelOn={`Disable ${vm.displayName}`}
                labelOff={`Enable ${vm.displayName}`}
              />
            </div>
          ) : vm.toggle.kind === "builtin-policy" ? (
            <Switch
              checked={vm.toggle.enabled}
              disabled={
                busy ||
                (pendingBuiltinPolicies?.has(
                  vm.toggle.kind === "builtin-policy" ? vm.toggle.policyId : "",
                ) ??
                  false)
              }
              onToggle={async (next) =>
                onToggleBuiltinPolicy(
                  vm.toggle.kind === "builtin-policy" ? vm.toggle.policyId : "",
                  next,
                )
              }
              labelOn={`Disable ${vm.displayName}`}
              labelOff={`Enable ${vm.displayName}`}
            />
          ) : vm.toggle.kind === "control-plane" ? (
            <Switch
              checked={vm.toggle.enabled}
              disabled={
                busy ||
                (pendingControlPlane?.has(
                  vm.toggle.kind === "control-plane" ? vm.toggle.behaviorId : "",
                ) ??
                  false)
              }
              onToggle={async (next) =>
                onToggleControlPlane(
                  vm.toggle.kind === "control-plane" ? vm.toggle.behaviorId : "",
                  next,
                )
              }
              labelOn={`Disable ${vm.displayName}`}
              labelOff={`Enable ${vm.displayName}`}
            />
          ) : vm.toggle.kind === "adapter-row" ? (
            <Switch
              checked={
                vm.toggle.row.state === "enabled" || vm.toggle.row.state === "always-on"
              }
              disabled={busy || presetPending}
              onToggle={async (next) => handleAdapterToggle(next)}
              labelOn={`Disable ${vm.displayName}`}
              labelOff={`Enable ${vm.displayName}`}
            />
          ) : null}

          {vm.deletable ? (
            <button
              type="button"
              onClick={() => {
                // Honest cascade note (design 3.3): deleting a policy also
                // deletes its member rules; say so before doing it.
                const memberCount = vm.members.length;
                const note =
                  memberCount > 0
                    ? `Delete policy "${vm.displayName}"? This also deletes its ${memberCount} member rule${memberCount === 1 ? "" : "s"}.`
                    : `Delete policy "${vm.displayName}"?`;
                if (!window.confirm(note)) return;
                if (vm.kind === "native") {
                  const entry = catalogPolicies.find((p) => p.id === (vm.toggle.kind === "native" ? vm.toggle.policyId : ""));
                  if (entry) onDeletePolicy(entry);
                } else {
                  handleAdapterDelete();
                }
              }}
              disabled={busy}
              aria-label={`Delete ${vm.displayName}`}
              className="text-secondary transition-colors hover:text-red-600 disabled:opacity-40"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          ) : null}
        </div>
      </div>

      {vm.toggle.kind === "floor" && vm.gateMode && onCitationGateModeChange ? (
        <CitationGateModeSelector
          descriptor={vm.gateMode}
          value={citationGateMode ?? vm.gateMode.value}
          pending={citationGateModePending ?? false}
          error={citationGateModeError ?? null}
          onChange={onCitationGateModeChange}
        />
      ) : null}

      <MemberDrillDown vm={vm} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Citation gate-mode selector (floored source_citation card only)
// ---------------------------------------------------------------------------

const GATE_MODE_LABELS: Record<string, string> = {
  repair: "Repair (revise or search to ground claims)",
  audit: "Audit (record findings, never alter the answer)",
  off: "Off (skip the gate)",
};

/**
 * The 3-way enforcement-strictness selector rendered under the always-on badge
 * of the floored source_citation card. The policy itself is always on (it cannot
 * be disabled); this selector only steps its gate STRICTNESS down. Inline
 * citations and the Sources panel stay on in every mode.
 */
function CitationGateModeSelector({
  descriptor,
  value,
  pending,
  error,
  onChange,
}: {
  descriptor: CitationGateModeDescriptor;
  value: string;
  pending: boolean;
  error: string | null;
  onChange: (mode: string) => void;
}): React.ReactElement {
  const options: SelectOption[] = descriptor.options.map((mode) => ({
    value: mode,
    label: GATE_MODE_LABELS[mode] ?? mode,
  }));
  return (
    <div className="mt-3 rounded-lg border border-black/[0.05] bg-gray-50/40 px-3 py-2.5">
      <Select
        label="Enforcement strictness"
        options={options}
        value={value}
        disabled={pending}
        error={error ?? undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="mt-1.5 text-[10px] leading-relaxed text-secondary/70">
        Always on and cannot be turned off. This only steps its enforcement down.
        Repair can revise or search to ground claims, audit records but never
        alters the answer, off skips the gate. Inline citations and the Sources
        panel stay on in all modes.
      </p>
    </div>
  );
}

function ReviewBadge({ verdict }: { verdict: string }): React.ReactElement | null {
  if (!verdict || verdict === "unreviewed" || verdict === "unknown") return null;
  const pass = verdict === "aligned";
  return (
    <span
      title={`Review: ${verdict}`}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
        pass ? "bg-emerald-500/12 text-emerald-700" : "bg-amber-500/12 text-amber-700"
      }`}
    >
      {pass ? "✓ reviewed" : "⚠ review"}
    </span>
  );
}

/**
 * Drill-down: the member rule rows. When the policy has a producer→gate
 * binding, the first member reads as the producer and the second as the gate
 * (chips make the relationship legible).
 */
function MemberDrillDown({ vm }: { vm: PolicyCardVM }): React.ReactElement | null {
  const n = vm.members.length + vm.unresolvedMemberIds.length;
  // A card with genuinely zero member rules (control-plane nudges: a single
  // behavior, not a composition) gets no drill-down at all - "0 RULES" was
  // noise pretending to be information.
  if (n === 0) return null;
  return (
    <details
      className="group mt-3 rounded-lg border border-black/[0.05] bg-gray-50/40"
      data-testid={`policy-drilldown-${vm.key}`}
    >
      <summary className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-secondary/70 hover:bg-black/[0.02]">
        <span>
          {n} rule{n === 1 ? "" : "s"}
        </span>
        <span
          aria-hidden
          className="inline-block transition-transform duration-150 group-open:rotate-180"
        >
          ▾
        </span>
      </summary>
      <ul className="flex flex-col gap-1.5 px-3 pb-2 pt-1">
        {vm.members.map((m, idx) => (
          <li key={m.id} className="flex flex-wrap items-center gap-2 text-[12px]">
            {vm.hasBinding ? (
              <span
                className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                  idx === 0
                    ? "bg-sky-500/12 text-sky-700"
                    : "bg-emerald-500/12 text-emerald-700"
                }`}
              >
                {idx === 0 ? "producer" : "gate"}
              </span>
            ) : null}
            {vm.hasBinding && idx > 0 ? (
              <span aria-hidden className="text-secondary/50">
                ▶
              </span>
            ) : null}
            <span className="font-medium text-foreground">{m.name}</span>
            <span className="text-secondary/60">
              {m.when.scope} · {m.when.firesAt}
            </span>
            <span className="text-secondary/60">· {m.action}</span>
            <StatePill state={m.state} />
          </li>
        ))}
        {vm.unresolvedMemberIds.map((rid) => (
          <li
            key={rid}
            className="flex flex-wrap items-center gap-2 text-[12px]"
          >
            <span className="font-medium text-foreground">
              {formatRuntimeMemberName(rid)}
            </span>
            <span
              title="This rule is part of the runtime itself; it has no dashboard editor."
              className="rounded bg-black/[0.05] px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-secondary/70"
            >
              runtime-managed
            </span>
          </li>
        ))}
      </ul>
    </details>
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
      className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium ${cls[state]}`}
    >
      {label[state]}
    </span>
  );
}
