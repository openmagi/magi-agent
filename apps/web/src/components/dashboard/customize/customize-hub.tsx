"use client";

/**
 * Customize hub — full-page tab with left sub-nav (Phase 4).
 *
 * Replaces the dismiss-and-reopen modal duo (Verification Rules + Custom Tools)
 * with a full-page surface that hosts a sub-nav for each customize area:
 *
 *   - Verification Rules (scope-grouped preset toggles + custom rules + USER-RULES.md)
 *   - Custom Tools       (enable/disable per-tool)
 *   - Recipes            (Phase 3 enabled_recipes allowlist; greyed-out for unmapped)
 *   - Hooks              (read-only HookBus settings.json surface — placeholder)
 *
 * Why full-page instead of modal: the verification preset list is 38 rows + the
 * custom-rule builder + the freeform-guidance editor. Modal scrolling was the
 * UX bottleneck (search / filter / cross-comparison all impractical in a 5-row
 * tall scroll box). The hub also gives Phase 5 (SeamSpec NL builder) a natural
 * mount point as a new sub-nav entry without rebuilding the modal.
 *
 * Implementation is intentionally a thin wrapper around the existing modals'
 * panel bodies (``VerificationRulePanel`` + ``CustomToolPanel``) — those were
 * extracted from the modals so the runtime contract + handlers are shared. The
 * legacy ``CustomizeRuntimeConsole`` modal duo is preserved for tests but the
 * page route now mounts this hub.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Switch } from "@/components/ui/_ds";
import { ShieldCheck, Wrench, Layers, Webhook, Plus, Gauge, Drama } from "lucide-react";
import {
  useCustomize,
  patchToolOverride,
  patchVerificationOverride,
  patchRecipeOverride,
  patchControlPlaneOverride,
  patchBuiltinPolicyOverride,
  patchCitationGateMode,
  putRules,
  putCustomRule,
  deleteCustomRule,
  compileCustomRule,
  patchPolicyEnabled,
  deletePolicy,
  getBudgets,
  putBudgets,
} from "@/lib/customize-api";
import type {
  BudgetsResponse,
  ConversationTurn,
  CustomRule,
  CustomizeOverrides,
  PolicyCatalogEntry,
  ShaclCompileResponse,
  VerificationBudgets,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import { getModes } from "@/lib/agent-modes-api";
import type { AgentMode } from "@/chat-core";
import { AddRulePicker, type AddRuleChoice } from "./add-rule-modal";
import { GuidedWizard } from "./guided-wizard";
import { NlRuleCompose } from "./nl-rule-compose";
import { ConversationalPolicyCompose } from "./conversational-policy-compose";
import {
  CustomRulesSection,
} from "./verification-rule-modal";
import { CustomChecksSection } from "./custom-checks-section";
import { CustomToolPanel } from "./custom-tool-modal";
import { BudgetsTab } from "./budgets-tab";
import { GuidancePanel } from "./guidance-panel";
import { ModesPanel } from "./modes-panel";
import { PacksPanel } from "./packs-panel";
import { PageHint } from "./page-hint";
import { PoliciesTable } from "./policies-table";
import { PolicyCardList } from "./policy-card-list";
import { PrebuiltComponentsPanel } from "./prebuilt-components-panel";
import { ReusableEvidenceTab } from "./reusable-evidence-tab";
import { ReusableConditionsTab } from "./reusable-conditions-tab";
import { SeamBuilderPanel } from "./seam-builder-panel";
import {
  extractBuiltinJudgmentRefs,
  extractEvidenceTypes,
  extractNamedConditions,
  unifyRuleRows,
} from "@/lib/policy-model";
import {
  deleteDashboardCheck,
  getDashboardChecks,
  putDashboardCheck,
  type DashboardCheck,
} from "@/lib/packs-dashboard-api";
import {
  deleteSeamSpec as deleteSeamSpecApi,
} from "@/lib/customize-api";

export type CustomizeSection =
  | "rules"
  | "modes"
  | "tools"
  // PR-3 (design D4): the "behaviors" section is retired — its cards fold into
  // the Policies surface. The literal is kept in the union so an old deep link
  // (?section=behaviors) still type-checks; ``normalizeSection`` redirects it to
  // ``"rules"`` so the tab is never rendered and the URL lands on Policies.
  | "behaviors"
  | "budgets"
  | "recipes"
  | "hooks";

const SECTIONS: ReadonlyArray<{
  id: CustomizeSection;
  label: string;
  icon: React.ReactNode;
  description: string;
}> = [
  {
    id: "rules",
    label: "Policies",
    icon: <ShieldCheck className="h-4 w-4" />,
    description:
      "Your policies: named units of intent that gate or guide the agent (block / ask / audit / nudge). Built-in + first-party + your own, one card each. Toggles here set the GLOBAL default for every turn; to apply a policy only in a specific stance, scope it in Modes. Individual rules live inside each policy's drill-down.",
  },
  {
    id: "modes",
    label: "Modes",
    icon: <Drama className="h-4 w-4" />,
    description:
      "Saved agent postures. A mode carries a soft system prompt + a tool allow/deny delta; pick one in the chat composer to apply it per turn. The active mode here is the sticky default the composer starts on.",
  },
  {
    id: "tools",
    label: "Tools",
    icon: <Wrench className="h-4 w-4" />,
    description: "Enable or disable individual tools.",
  },
  {
    id: "budgets",
    label: "Budgets",
    icon: <Gauge className="h-4 w-4" />,
    description:
      "Per-bot cost ceilings (max tool calls per turn, max-steps brake, loop-guard hard threshold). Saved values are projected onto the matching MAGI_* env at turn entry; an explicit operator env always wins.",
  },
  {
    id: "recipes",
    label: "Packs",
    icon: <Layers className="h-4 w-4" />,
    description:
      "Packs bundle rules, behaviors, and tools. Install a pack to make its contributions available, or remove it to drop them (first-party packs are always recoverable). Installing does not activate a rule by itself: turn it on globally in Rules, or scope it per turn in Modes.",
  },
  {
    id: "hooks",
    label: "Hooks",
    icon: <Webhook className="h-4 w-4" />,
    description:
      "Read-only view of file-authored lifecycle handlers (Python entry points registered in settings.json). The dashboard does not write these by design.",
  },
];

const DEFAULT_SECTION: CustomizeSection = "rules";

/**
 * Deep-link safety (PR-3): the retired "behaviors" section redirects to the
 * Policies tab ("rules") so an old bookmark / query string lands on Policies
 * instead of a blank/404 panel. Any section id not backed by a live SECTIONS
 * entry also falls back to the default.
 */
function normalizeSection(section: CustomizeSection): CustomizeSection {
  if (section === "behaviors") return "rules";
  return SECTIONS.some((s) => s.id === section) ? section : DEFAULT_SECTION;
}


interface CustomizeHubProps {
  botId: string;
  /** Initial sub-nav section. The route can pass this from a query string
   * (?section=tools) so deep links land on the correct sub-page. */
  initialSection?: CustomizeSection;
  /** Optional callback when the sub-nav changes — lets the page sync the
   * query string back. Omit for purely internal nav. */
  onSectionChange?: (section: CustomizeSection) => void;
}

export function CustomizeHub({
  botId,
  initialSection = DEFAULT_SECTION,
  onSectionChange,
}: CustomizeHubProps): React.JSX.Element {
  const { data, loading, error, reload } = useCustomize();
  const agentFetch = useAgentFetch();

  const [section, setSection] = useState<CustomizeSection>(
    normalizeSection(initialSection),
  );
  useEffect(() => {
    setSection(normalizeSection(initialSection));
  }, [initialSection]);

  const handleSection = useCallback(
    (next: CustomizeSection) => {
      const resolved = normalizeSection(next);
      setSection(resolved);
      onSectionChange?.(resolved);
    },
    [onSectionChange],
  );

  // ---- Verification state (same shape as legacy CustomizeRuntimeConsole) ----
  const [presetOverrides, setPresetOverrides] = useState<Record<string, boolean>>({});
  const [presetPending, setPresetPending] = useState<Set<string>>(new Set());
  const [customRules, setCustomRules] = useState<CustomRule[]>([]);
  const [customRuleBusy, setCustomRuleBusy] = useState(false);
  const [userRules, setUserRules] = useState("");
  const [rulesSaving, setRulesSaving] = useState(false);
  const [ruleError, setRuleError] = useState<string | null>(null);

  useEffect(() => {
    setPresetOverrides(data?.overrides.verification.preset_overrides ?? {});
    setCustomRules(data?.overrides.verification.custom_rules ?? []);
    setUserRules(data?.overrides.user_rules ?? "");
  }, [data]);

  const runCustomRuleOp = useCallback(
    (op: () => Promise<{ verification: { custom_rules: CustomRule[] } }>) => {
      setCustomRuleBusy(true);
      setRuleError(null);
      op()
        .then((overrides) => setCustomRules(overrides.verification.custom_rules))
        .catch((err: unknown) =>
          setRuleError(err instanceof Error ? err.message : "Custom rule failed"),
        )
        .finally(() => setCustomRuleBusy(false));
    },
    [],
  );

  const handleAddCustomRule = useCallback(
    (rule: CustomRule) => runCustomRuleOp(() => putCustomRule(agentFetch, rule)),
    [agentFetch, runCustomRuleOp],
  );
  const handleToggleCustomRule = useCallback(
    (rule: CustomRule, enabled: boolean) =>
      runCustomRuleOp(() => putCustomRule(agentFetch, { ...rule, enabled })),
    [agentFetch, runCustomRuleOp],
  );
  const handleDeleteCustomRule = useCallback(
    (id: string) => runCustomRuleOp(() => deleteCustomRule(agentFetch, id)),
    [agentFetch, runCustomRuleOp],
  );

  // ---- Native (catalog) policy handlers (PR-2) ----
  // A native policy toggle cascades member custom-rule `enabled` server-side
  // (PATCH /v1/app/policies/{id}); we reload to pick up the projected states.
  const handleTogglePolicy = useCallback(
    (policyId: string, enabled: boolean) => {
      setCustomRuleBusy(true);
      setRuleError(null);
      patchPolicyEnabled(agentFetch, policyId, enabled)
        .then(() => reload())
        .catch((err: unknown) =>
          setRuleError(err instanceof Error ? err.message : "Failed to toggle policy"),
        )
        .finally(() => setCustomRuleBusy(false));
    },
    [agentFetch, reload],
  );

  // Delete: the backend DELETE /v1/app/policies/{id} removes only the policy
  // record. Per the magi-cp cascade precedent, we then delete the member
  // custom rules client-side so they do not re-orphan onto the surface.
  const handleDeletePolicy = useCallback(
    (policy: PolicyCatalogEntry) => {
      setCustomRuleBusy(true);
      setRuleError(null);
      (async () => {
        await deletePolicy(agentFetch, policy.id);
        for (const rid of policy.ruleIds) {
          try {
            await deleteCustomRule(agentFetch, rid);
          } catch {
            // A member that is not a stored custom rule (builtin-native /
            // dashboard-check producer) 404s here — ignore, the policy record
            // is already gone.
          }
        }
      })()
        .then(() => reload())
        .catch((err: unknown) =>
          setRuleError(err instanceof Error ? err.message : "Failed to delete policy"),
        )
        .finally(() => setCustomRuleBusy(false));
    },
    [agentFetch, reload],
  );

  const handleTogglePreset = useCallback(
    (id: string, enabled: boolean) => {
      setPresetOverrides((prev) => ({ ...prev, [id]: enabled }));
      setRuleError(null);
      setPresetPending((prev) => new Set(prev).add(id));
      patchVerificationOverride(agentFetch, "harness_presets", id, enabled)
        .then((overrides) => {
          setPresetOverrides(overrides.verification.preset_overrides);
        })
        .catch((err: unknown) => {
          setPresetOverrides((prev) => ({ ...prev, [id]: !enabled }));
          setRuleError(
            err instanceof Error ? err.message : `Failed to update "${id}"`,
          );
        })
        .finally(() => {
          setPresetPending((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        });
    },
    [agentFetch],
  );

  const handleCompileShacl = useCallback(
    (
      nlText: string,
      sampleRecords?: unknown[],
      priorTurns?: ConversationTurn[],
    ): Promise<ShaclCompileResponse> =>
      compileCustomRule(agentFetch, nlText, sampleRecords, priorTurns),
    [agentFetch],
  );

  const handleSaveRules = useCallback(
    (text: string) => {
      setRulesSaving(true);
      setRuleError(null);
      putRules(agentFetch, text)
        .then((overrides) => {
          setUserRules(overrides.user_rules);
        })
        .catch((err: unknown) => {
          setRuleError(err instanceof Error ? err.message : "Failed to save rules");
        })
        .finally(() => setRulesSaving(false));
    },
    [agentFetch],
  );

  // ---- Tools state ----
  const [toolOverrides, setToolOverrides] = useState<Record<string, boolean>>({});
  const [toolPending, setToolPending] = useState<Set<string>>(new Set());
  const [toolError, setToolError] = useState<string | null>(null);

  useEffect(() => {
    setToolOverrides(data?.overrides.tools ?? {});
  }, [data]);

  const handleToggleTool = useCallback(
    (name: string, enabled: boolean) => {
      setToolOverrides((prev) => ({ ...prev, [name]: enabled }));
      setToolError(null);
      setToolPending((prev) => new Set(prev).add(name));
      patchToolOverride(agentFetch, name, enabled)
        .then((overrides) => {
          setToolOverrides(overrides.tools);
        })
        .catch((err: unknown) => {
          setToolOverrides((prev) => ({ ...prev, [name]: !enabled }));
          setToolError(
            err instanceof Error ? err.message : `Failed to update tool "${name}"`,
          );
        })
        .finally(() => {
          setToolPending((prev) => {
            const next = new Set(prev);
            next.delete(name);
            return next;
          });
        });
    },
    [agentFetch],
  );

  // --- Control-plane behavior toggles (facts-survey replan, goal nudge, …) ---
  // PR-3: these now render as NUDGE cards inside the Policies surface. Their
  // enabled state is read from the catalog (`policies` list, derived server-side
  // from the control_plane env projection), so a successful PATCH calls
  // ``reload()`` to re-derive the card state rather than mirroring into a local
  // override map the card no longer reads.
  const [behaviorPending, setBehaviorPending] = useState<Set<string>>(new Set());
  const [behaviorError, setBehaviorError] = useState<string | null>(null);

  const handleToggleBehavior = useCallback(
    (id: string, enabled: boolean) => {
      setBehaviorError(null);
      setBehaviorPending((prev) => new Set(prev).add(id));
      patchControlPlaneOverride(agentFetch, id, enabled)
        .then(() => reload())
        .catch((err: unknown) => {
          setBehaviorError(
            err instanceof Error ? err.message : `Failed to update behavior "${id}"`,
          );
        })
        .finally(() => {
          setBehaviorPending((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        });
    },
    [agentFetch, reload],
  );

  // --- Built-in (first-party) policy opt-out toggles -----------------------
  // Mirrors the control-plane behavior toggles above, but for first-party
  // POLICIES (verify-before-replying). Only user-disableable builtins appear in
  // ``catalog.builtinPolicies``; floors (source_citation) are excluded server-
  // side so they can never be turned off here.
  const [builtinPolicyPending, setBuiltinPolicyPending] = useState<Set<string>>(
    new Set(),
  );
  const [builtinPolicyError, setBuiltinPolicyError] = useState<string | null>(null);

  const handleToggleBuiltinPolicy = useCallback(
    (id: string, enabled: boolean) => {
      setBuiltinPolicyError(null);
      setBuiltinPolicyPending((prev) => new Set(prev).add(id));
      patchBuiltinPolicyOverride(agentFetch, id, enabled)
        .then(() => reload())
        .catch((err: unknown) => {
          setBuiltinPolicyError(
            err instanceof Error
              ? err.message
              : `Failed to update built-in policy "${id}"`,
          );
        })
        .finally(() => {
          setBuiltinPolicyPending((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        });
    },
    [agentFetch, reload],
  );

  // --- source_citation gate-mode opt-down (repair/audit/off) ----------------
  // The citation policy's BOOLEAN disable stays floored; this MODE step-down is
  // the acceptable opt-DOWN lever. It projects onto MAGI_SOURCE_CITATION_GATE_MODE
  // and never touches MAGI_SOURCE_CITATION_ENABLED, so capture / inline
  // citations / Sources stay on in all three modes.
  const [citationGateMode, setCitationGateMode] = useState<string | null>(null);
  const [citationGateModePending, setCitationGateModePending] = useState(false);
  const [citationGateModeError, setCitationGateModeError] = useState<string | null>(
    null,
  );

  useEffect(() => {
    setCitationGateMode(data?.overrides.citation_gate_mode ?? null);
  }, [data]);

  const handleCitationGateModeChange = useCallback(
    (mode: string) => {
      const previous = citationGateMode;
      setCitationGateMode(mode);
      setCitationGateModeError(null);
      setCitationGateModePending(true);
      patchCitationGateMode(agentFetch, mode)
        .then((overrides) => {
          setCitationGateMode(overrides.citation_gate_mode ?? mode);
        })
        .catch((err: unknown) => {
          setCitationGateMode(previous);
          setCitationGateModeError(
            err instanceof Error ? err.message : "Failed to update citation gate mode",
          );
        })
        .finally(() => {
          setCitationGateModePending(false);
        });
    },
    [agentFetch, citationGateMode],
  );

  // --- Recipes allowlist (F-UX10) ------------------------------------------
  // ``verification.recipes[]`` is allowlist-shaped: empty = no opt-out
  // (legacy default, every recipe behaves as enabled); non-empty filters out
  // refs contributed by recipe ids NOT in the list. The toggle UI shows
  // ``enabledRecipeIds.has(id) || enabledRecipeIds.size === 0`` as ON so the
  // user sees the live runtime view; the PATCH then mutates the bucket per
  // ``set_verification_override`` (append on enable, remove on disable).
  const [enabledRecipeIds, setEnabledRecipeIds] = useState<Set<string>>(new Set());
  const [recipePending, setRecipePending] = useState<Set<string>>(new Set());
  const [recipeError, setRecipeError] = useState<string | null>(null);

  useEffect(() => {
    setEnabledRecipeIds(
      new Set(data?.overrides.verification.recipes ?? []),
    );
  }, [data]);

  // Catalog list of recipes the dashboard renders. Pulled up above
  // ``handleToggleRecipe`` so the first-disable seed branch can read every
  // peer recipe's id + packIds without a TDZ hazard.
  const recipes = useMemo(
    () => data?.catalog.verification.recipes ?? [],
    [data],
  );

  // F-UX10 first-disable seed: from the legacy default state the persisted
  // ``verification.recipes[]`` is empty, which the backend semantics treat as
  // "no override → every recipe ON". A naïve PATCH(false, id) is a silent
  // no-op (``set_verification_override`` only ``remove()``s when the id is
  // already in the list) and the toggle snaps back to ON. To honour the
  // first-disable intent we explicitly seed the allowlist with every OTHER
  // mapped recipe first (sequential PATCHes so the backend can validate each
  // id against the curated catalog and 404 on typos), then the recipe being
  // disabled is implicitly excluded. ``packIds.length > 0`` filters out UI-
  // only labels whose toggle is disabled in the panel anyway.
  const handleToggleRecipe = useCallback(
    (id: string, enabled: boolean) => {
      // Optimistic update — append/remove the id, mirroring the backend
      // ``set_verification_override`` semantics. For the first-disable case
      // the optimistic set is seeded with every OTHER mapped recipe so the
      // UI immediately reflects the post-seed allowlist (the row being
      // disabled greys out, the rest stay ON).
      const firstDisable =
        enabled === false && enabledRecipeIds.size === 0;
      const otherMapped = firstDisable
        ? recipes
            .filter(
              (r) =>
                r.id !== id &&
                Array.isArray(r.packIds) &&
                r.packIds.length > 0,
            )
            .map((r) => r.id)
        : [];
      setEnabledRecipeIds((prev) => {
        if (firstDisable) return new Set(otherMapped);
        const next = new Set(prev);
        if (enabled) next.add(id);
        else next.delete(id);
        return next;
      });
      setRecipeError(null);
      setRecipePending((prev) => new Set(prev).add(id));
      const persist = async (): Promise<CustomizeOverrides> => {
        if (firstDisable) {
          // Seed the allowlist with every OTHER mapped recipe so the backend
          // flips from the "no override → everything ON" state into an
          // explicit allowlist that omits ``id``. Sequential PATCH calls keep
          // the unknown-id 404 guard intact and let any single failure abort
          // the seed before it corrupts the list.
          let overrides: CustomizeOverrides | null = null;
          for (const seedId of otherMapped) {
            overrides = await patchRecipeOverride(agentFetch, seedId, true);
          }
          if (overrides === null) {
            // No other mapped recipes to seed (only one mapped recipe exists
            // in the catalog and the user disabled it). Fall through to the
            // normal PATCH(false) — once the bucket is non-empty the
            // ``remove()`` branch fires, but with zero mapped peers the
            // allowlist would round-trip to ``[]`` again. In that degenerate
            // shape there is nothing to bulk-seed; we surface a banner so the
            // operator understands why the toggle cannot disable.
            throw new Error(
              `Cannot disable "${id}": no other mapped packs to seed the allowlist with. Add another pack first.`,
            );
          }
          return overrides;
        }
        return patchRecipeOverride(agentFetch, id, enabled);
      };
      persist()
        .then((overrides) => {
          setEnabledRecipeIds(new Set(overrides.verification.recipes ?? []));
        })
        .catch((err: unknown) => {
          // Revert the optimistic state change so the toggle snaps back.
          setEnabledRecipeIds((prev) => {
            if (firstDisable) return new Set();
            const next = new Set(prev);
            if (enabled) next.delete(id);
            else next.add(id);
            return next;
          });
          setRecipeError(
            err instanceof Error ? err.message : `Failed to update recipe "${id}"`,
          );
        })
        .finally(() => {
          setRecipePending((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        });
    },
    [agentFetch, enabledRecipeIds, recipes],
  );

  // --- Budgets (PR-F7) ------------------------------------------------------
  // Independent fetch from /v1/app/customize/budgets so the UI carries the
  // `effectiveEnv` snapshot the dashboard /v1/app/customize GET does not emit.
  const [budgetsData, setBudgetsData] = useState<BudgetsResponse | null>(null);
  const [budgetsLoading, setBudgetsLoading] = useState(false);
  const [budgetsSaving, setBudgetsSaving] = useState(false);
  const [budgetsError, setBudgetsError] = useState<string | null>(null);

  const loadBudgets = useCallback(() => {
    setBudgetsLoading(true);
    setBudgetsError(null);
    getBudgets(agentFetch)
      .then((next) => setBudgetsData(next))
      .catch((err: unknown) =>
        setBudgetsError(err instanceof Error ? err.message : "Failed to load budgets"),
      )
      .finally(() => setBudgetsLoading(false));
  }, [agentFetch]);

  // Lazy-load: only fetch once the user navigates to the Budgets sub-tab so
  // the rest of the hub stays a single round-trip on first paint.
  useEffect(() => {
    if (section === "budgets" && budgetsData === null && !budgetsLoading) {
      loadBudgets();
    }
  }, [section, budgetsData, budgetsLoading, loadBudgets]);

  const handleSaveBudgets = useCallback(
    (next: VerificationBudgets) => {
      setBudgetsSaving(true);
      setBudgetsError(null);
      putBudgets(agentFetch, next)
        .then((res) => setBudgetsData(res))
        .catch((err: unknown) =>
          setBudgetsError(err instanceof Error ? err.message : "Failed to save budgets"),
        )
        .finally(() => setBudgetsSaving(false));
    },
    [agentFetch],
  );

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-secondary">
        Loading customize catalog…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-4 text-sm leading-6 text-amber-800">
        <div className="font-semibold">Could not load customization from the local runtime.</div>
        <div className="mt-1">{error}</div>
        <button
          type="button"
          onClick={reload}
          className="mt-3 inline-flex min-h-[40px] items-center rounded-lg border border-amber-500/30 bg-white px-4 py-2 text-sm font-semibold text-amber-800 transition-colors hover:bg-amber-50"
        >
          Retry
        </button>
      </div>
    );
  }
  if (!data) return <></>;

  const active = SECTIONS.find((s) => s.id === section) ?? SECTIONS[0];

  return (
    <div className="mx-auto flex max-w-7xl gap-6 pb-20">
      {/* Left sub-nav */}
      <aside className="w-56 shrink-0">
        <header className="mb-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-secondary/60">
            {botId ? `route: ${botId}` : "local"}
          </p>
          <h1 className="mt-1 text-lg font-bold leading-tight text-foreground">Customize</h1>
        </header>
        <nav aria-label="Customize sections" className="space-y-1">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => handleSection(s.id)}
              aria-current={s.id === section ? "page" : undefined}
              className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                s.id === section
                  ? "bg-primary/10 font-semibold text-primary"
                  : "text-secondary hover:bg-black/[0.04] hover:text-foreground"
              }`}
            >
              {s.icon}
              <span>{s.label}</span>
            </button>
          ))}
        </nav>
      </aside>

      {/* Active section panel */}
      <section className="min-w-0 flex-1">
        <header className="mb-5">
          <h2 className="text-2xl font-bold leading-tight text-foreground">{active.label}</h2>
          <p className="mt-1 text-sm leading-6 text-secondary">{active.description}</p>
        </header>

        {section === "rules" ? (
          <RulesSectionMount
            data={data}
            reload={reload}
            presetOverrides={presetOverrides}
            pendingPresets={presetPending}
            onTogglePreset={handleTogglePreset}
            customRules={customRules}
            customRuleBusy={customRuleBusy}
            onAddCustomRule={handleAddCustomRule}
            onToggleCustomRule={handleToggleCustomRule}
            onDeleteCustomRule={handleDeleteCustomRule}
            onTogglePolicy={handleTogglePolicy}
            onDeletePolicy={handleDeletePolicy}
            onToggleBuiltinPolicy={handleToggleBuiltinPolicy}
            onToggleControlPlane={handleToggleBehavior}
            pendingBuiltinPolicies={builtinPolicyPending}
            pendingControlPlane={behaviorPending}
            behaviorError={behaviorError}
            builtinPolicyError={builtinPolicyError}
            citationGateMode={citationGateMode}
            onCitationGateModeChange={handleCitationGateModeChange}
            citationGateModePending={citationGateModePending}
            citationGateModeError={citationGateModeError}
            userRules={userRules}
            rulesSaving={rulesSaving}
            onSaveRules={handleSaveRules}
            onCompileShacl={handleCompileShacl}
            ruleError={ruleError}
          />
        ) : null}

        {section === "modes" ? <ModesPanel botId={botId} /> : null}

        {section === "tools" ? (
          <CustomToolPanel
            tools={data.catalog.tools}
            overrides={toolOverrides}
            onToggle={handleToggleTool}
            pendingNames={toolPending}
            error={toolError}
          />
        ) : null}


        {section === "budgets" ? (
          <BudgetsTab
            budgets={budgetsData?.budgets ?? {}}
            effectiveEnv={budgetsData?.effectiveEnv ?? {}}
            envMap={budgetsData?.envMap ?? {}}
            loading={budgetsLoading}
            saving={budgetsSaving}
            error={budgetsError}
            onSave={handleSaveBudgets}
            onReload={loadBudgets}
          />
        ) : null}

        {section === "recipes" ? (
          <div className="space-y-6">
            <PacksPanel />
            <div>
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
                First-party bundles (opt in / out)
              </p>
              <RecipesPanel
                recipes={recipes}
                enabledRecipeIds={enabledRecipeIds}
                pendingIds={recipePending}
                error={recipeError}
                onToggle={handleToggleRecipe}
              />
            </div>
          </div>
        ) : null}

        {section === "hooks" ? <HooksPanel /> : null}
      </section>
    </div>
  );
}


/**
 * Policies section mount (PR-2 "policies-first surface").
 *
 * PRIMARY surface: the :class:`PolicyCardList` — one card per policy from the
 * catalog `policies` list (Your / First-party / Built-in), with the flat rule
 * rows (unified via :func:`unifyRuleRows`) supplying each card's member
 * drill-down.
 *
 * The legacy flat :class:`PoliciesTable` + the reusable Evidence / Conditions
 * catalogs move under a collapsed "Advanced" disclosure below the card list
 * (design D3) — kept functional for debugging. Authoring is consolidated
 * behind the single "+ Add policy" entry (PR-4, design D2): conversational NL
 * is the default surface, "Producer + gate" is the multi-rule composer, and
 * the Guided wizard + the legacy ``CustomRulesSection`` / AddRulePicker Raw
 * forms mount unchanged behind the flow's "Advanced" entry.
 */
function RulesSectionMount({
  data,
  reload,
  presetOverrides,
  pendingPresets,
  onTogglePreset,
  customRules,
  customRuleBusy,
  onAddCustomRule,
  onToggleCustomRule,
  onDeleteCustomRule,
  onTogglePolicy,
  onDeletePolicy,
  onToggleBuiltinPolicy,
  onToggleControlPlane,
  pendingBuiltinPolicies,
  pendingControlPlane,
  behaviorError,
  builtinPolicyError,
  citationGateMode,
  onCitationGateModeChange,
  citationGateModePending,
  citationGateModeError,
  userRules,
  rulesSaving,
  onSaveRules,
  onCompileShacl,
  ruleError,
}: {
  data: NonNullable<ReturnType<typeof useCustomize>["data"]>;
  reload: () => void;
  presetOverrides: Record<string, boolean>;
  pendingPresets: Set<string>;
  onTogglePreset: (id: string, next: boolean) => void;
  customRules: CustomRule[];
  customRuleBusy: boolean;
  onAddCustomRule: (rule: CustomRule) => void;
  onToggleCustomRule: (rule: CustomRule, enabled: boolean) => void;
  onDeleteCustomRule: (id: string) => void;
  onTogglePolicy: (policyId: string, next: boolean) => void;
  onDeletePolicy: (policy: PolicyCatalogEntry) => void;
  // PR-3 — first-party policy opt-out + control-plane behavior nudge routing,
  // folded into the Policies surface (Behaviors tab retired).
  onToggleBuiltinPolicy: (policyId: string, next: boolean) => void;
  onToggleControlPlane: (behaviorId: string, next: boolean) => void;
  pendingBuiltinPolicies: Set<string>;
  pendingControlPlane: Set<string>;
  behaviorError: string | null;
  builtinPolicyError: string | null;
  // source_citation gate-mode opt-DOWN (repair/audit/off): the floored citation
  // policy card renders a 3-way selector. `null` means no explicit override yet
  // (the card falls back to the catalog entry's gateMode.value = fleet default).
  citationGateMode: string | null;
  onCitationGateModeChange: (mode: string) => void;
  citationGateModePending: boolean;
  citationGateModeError: string | null;
  // "Your guidance" freeform system-prompt text, relocated from the retired
  // Behaviors tab into the Policies surface.
  userRules: string;
  rulesSaving: boolean;
  onSaveRules: (text: string) => void;
  onCompileShacl: (
    nlText: string,
    sampleRecords?: unknown[],
    priorTurns?: ConversationTurn[],
  ) => Promise<ShaclCompileResponse>;
  ruleError: string | null;
}): React.ReactElement {
  // Add-policy state machine (PR-4 authoring consolidation). "+ Add policy"
  // is the SINGLE entry point: it opens the ``policy`` phase whose default
  // ``describe`` surface is the conversational NL composer — a 1-rule save
  // auto-promotes to a 1-rule Policy server-side (PR-1), the degenerate case
  // of the original design intent. The ``linked`` surface is the multi-rule
  // producer + gate composer. The Guided wizard and the Raw forms survive
  // unchanged as an "Advanced" entry INSIDE the flow (guided / raw_picking /
  // raw_authoring phases); the standalone add-rule button/picker is retired.
  // PR-F-HANDOFF — ``nlPrefill`` seeds the NL surface from the guided
  // wizard's "Continue in NL" handoff (NlRuleCompose ``initialNlText``).
  type AddState =
    | { phase: "idle" }
    | { phase: "policy"; surface: "describe" | "linked"; nlPrefill?: string }
    | { phase: "guided" }
    | { phase: "raw_picking" }
    | { phase: "raw_authoring"; choice: AddRuleChoice };
  const [addState, setAddState] = useState<AddState>({ phase: "idle" });

  type SubTab = "policies" | "evidence" | "conditions";
  const [subTab, setSubTab] = useState<SubTab>("policies");

  const agentFetch = useAgentFetch();
  const [dashboardChecks, setDashboardChecks] = useState<DashboardCheck[]>([]);
  const [dashboardBusy, setDashboardBusy] = useState(false);

  // PR-U4a: modes, for the reverse "scoped in N modes" indicator on each rule
  // row. The forward direction (which rules a mode scopes) lives in the Modes
  // editor's scoped-rule picker; this shows the same relationship from Rules.
  const [modes, setModes] = useState<AgentMode[]>([]);
  useEffect(() => {
    getModes(agentFetch)
      .then((resp) => setModes(resp.modes))
      .catch(() => setModes([])); // modes endpoint unavailable = no badges
  }, [agentFetch]);

  const reloadDashboardChecks = useCallback(() => {
    getDashboardChecks(agentFetch)
      .then((resp) => setDashboardChecks(resp.checks))
      .catch(() => setDashboardChecks([])); // 410 (flag-off) = silently empty
  }, [agentFetch]);

  useEffect(() => {
    reloadDashboardChecks();
  }, [reloadDashboardChecks, data]);

  const handleToggleDashboardCheck = useCallback(
    (check: DashboardCheck, next: boolean) => {
      setDashboardBusy(true);
      putDashboardCheck(agentFetch, { ...check, enabled: next })
        .then((resp) => setDashboardChecks(resp.checks))
        .catch(() => undefined)
        .finally(() => setDashboardBusy(false));
    },
    [agentFetch],
  );

  const handleDeleteDashboardCheck = useCallback(
    (id: string) => {
      setDashboardBusy(true);
      deleteDashboardCheck(agentFetch, id)
        .then((resp) => setDashboardChecks(resp.checks))
        .catch(() => undefined)
        .finally(() => setDashboardBusy(false));
    },
    [agentFetch],
  );

  const handleDeleteSeamSpec = useCallback(
    (specId: string) => {
      deleteSeamSpecApi(agentFetch, specId)
        .then(() => reload())
        .catch(() => undefined);
    },
    [agentFetch, reload],
  );

  const ruleRows = useMemo(
    () =>
      unifyRuleRows({
        catalog: data.catalog,
        overrides: { ...data.overrides, verification: { ...data.overrides.verification, custom_rules: customRules, preset_overrides: presetOverrides } },
        dashboardChecks,
      }),
    [data, customRules, presetOverrides, dashboardChecks],
  );
  // PR-U4a: policy id maps to the display names of the modes that scope it. Modes'
  // scopedPolicyIds use the same prefixed ids as the unified policy index, so
  // the join is a direct id match.
  const scopedInModes = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const mode of modes) {
      for (const id of mode.scopedPolicyIds) {
        (map[id] ??= []).push(mode.displayName);
      }
    }
    return map;
  }, [modes]);
  const evidenceTypes = useMemo(() => extractEvidenceTypes(ruleRows), [ruleRows]);
  const conditions = useMemo(() => extractNamedConditions(ruleRows), [ruleRows]);
  // PR-2: native policy summaries from the catalog (user + first-party
  // builtin). The Policies card list renders from these; the flat rule rows
  // supply the member drill-down. An absent key (pre-U3 backend) = empty list.
  const catalogPolicies = data.catalog.policies ?? [];
  // Policy CARD count for the tab chip (design D: count = policies, not rules):
  // native policies + every rule row NOT referenced by a native policy (each of
  // those renders as its own 1-rule adapter card).
  const policyCardCount = useMemo(() => {
    const referenced = new Set<string>();
    for (const p of catalogPolicies) for (const rid of p.ruleIds) referenced.add(rid);
    const adapters = ruleRows.filter(
      (r) =>
        !(
          r.rawSource.kind === "custom_rule" &&
          r.rawSource.rule.id &&
          referenced.has(r.rawSource.rule.id)
        ),
    ).length;
    return catalogPolicies.length + adapters;
  }, [catalogPolicies, ruleRows]);
  // PR-F-UX5 — built-in verdict primitives sourced from catalog.judgmentMenu.
  // The Conditions tab merges this with user-authored conditions; the
  // counter sums both halves so it matches what the tab body renders.
  const builtinJudgments = useMemo(
    () => extractBuiltinJudgmentRefs(data.catalog),
    [data.catalog],
  );
  const seamSpecs = data.overrides.verification.seam_specs ?? [];

  const composeSurface = (surface: "describe" | "linked") =>
    setAddState({ phase: "policy", surface });

  return (
    <div className="space-y-5">
      {/* Policies header. "+ Add policy" is the SINGLE authoring entry point
          (PR-4): every path — conversational NL (default), producer + gate,
          Guided, Raw — yields a Policy (1-rule saves auto-promote via PR-1). */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">
          Policies{" "}
          <span className="font-normal text-secondary">({policyCardCount})</span>
        </h3>
        {addState.phase === "idle" ? (
          <button
            type="button"
            onClick={() => composeSurface("describe")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90"
          >
            <Plus className="h-3.5 w-3.5" />
            Add policy
          </button>
        ) : null}
      </div>

      {addState.phase === "policy" ? (
        <section className="space-y-2">
          <AuthoringHeader
            label={
              addState.surface === "linked"
                ? "Add policy: link two rules (producer + gate)"
                : "Add policy"
            }
            onClose={() => setAddState({ phase: "idle" })}
          />
          {/* Surface switcher + the demoted Advanced editors. Conversational
              is the default; Guided/Raw survive unchanged behind "Advanced". */}
          <div className="flex flex-wrap items-center justify-between gap-2 px-1">
            <div className="flex items-center gap-1.5" role="group" aria-label="Authoring surface">
              <button
                type="button"
                aria-pressed={addState.surface === "describe"}
                onClick={() => composeSurface("describe")}
                className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
                  addState.surface === "describe"
                    ? "bg-primary text-white"
                    : "border border-black/[0.08] bg-white text-secondary hover:text-foreground"
                }`}
              >
                Describe it
              </button>
              <button
                type="button"
                aria-pressed={addState.surface === "linked"}
                onClick={() => composeSurface("linked")}
                title="Author a multi-step policy: a producer that records evidence + a gate that blocks a tool until that evidence exists this session."
                className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
                  addState.surface === "linked"
                    ? "bg-primary text-white"
                    : "border border-black/[0.08] bg-white text-secondary hover:text-foreground"
                }`}
              >
                Producer + gate
              </button>
            </div>
            <div className="flex items-center gap-1.5 text-[11px] text-secondary/80">
              <span className="font-medium">Advanced:</span>
              <button
                type="button"
                onClick={() => setAddState({ phase: "guided" })}
                title="Answer a few questions, step by step. No jargon, no blank form."
                className="rounded-full border border-black/[0.08] bg-white px-2.5 py-1 font-medium text-secondary hover:text-foreground"
              >
                Guided steps
              </button>
              <button
                type="button"
                onClick={() => setAddState({ phase: "raw_picking" })}
                title="Fill the form by hand — for when you already know the exact rule shape you want."
                className="rounded-full border border-black/[0.08] bg-white px-2.5 py-1 font-medium text-secondary hover:text-foreground"
              >
                Raw form
              </button>
            </div>
          </div>
          {addState.surface === "describe" ? (
            <NlRuleCompose
              onActivated={() => {
                reload();
                reloadDashboardChecks();
                setAddState({ phase: "idle" });
              }}
              onBrowseEvidence={() => setSubTab("evidence")}
              onAuthorManually={() => setAddState({ phase: "guided" })}
              initialNlText={addState.nlPrefill}
            />
          ) : (
            <ConversationalPolicyCompose
              agentFetch={agentFetch}
              onSaved={() => {
                reload();
                reloadDashboardChecks();
                setAddState({ phase: "idle" });
              }}
            />
          )}
        </section>
      ) : null}

      {addState.phase === "guided" ? (
        <GuidedWizard
          catalog={data.catalog}
          evidenceTypes={evidenceTypes}
          onActivated={() => {
            reload();
            reloadDashboardChecks();
            setAddState({ phase: "idle" });
          }}
          onPickDifferent={() => composeSurface("describe")}
          onCancel={() => setAddState({ phase: "idle" })}
          // PR-F-HANDOFF — operator clicked "Continue in NL" mid-wizard.
          // Flip to the NL surface and seed the textarea with the
          // serialized draft primer so the chat resumes where the
          // wizard left off.
          onContinueInNl={(primer) =>
            setAddState({ phase: "policy", surface: "describe", nlPrefill: primer })
          }
        />
      ) : null}

      {addState.phase === "raw_picking" ? (
        <section className="space-y-2">
          <AuthoringHeader
            label="Advanced: pick a rule kind"
            onPickDifferent={() => composeSurface("describe")}
            onClose={() => setAddState({ phase: "idle" })}
          />
          <AddRulePicker
            onCancel={() => composeSurface("describe")}
            onPick={(choice) =>
              setAddState({ phase: "raw_authoring", choice })
            }
          />
        </section>
      ) : null}

      {addState.phase === "raw_authoring" ? (
        <section className="space-y-2">
          <AuthoringHeader
            label={`Advanced: ${LABEL_FOR_CHOICE[addState.choice]}`}
            onPickDifferent={() => setAddState({ phase: "raw_picking" })}
            onClose={() => setAddState({ phase: "idle" })}
          />
          {addState.choice === "block-answer" || addState.choice === "restrict-tool" ? (
            <CustomRulesSection
              menu={data.catalog.verification.customRuleMenu}
              rules={customRules}
              busy={customRuleBusy}
              onAdd={onAddCustomRule}
              onToggle={onToggleCustomRule}
              onDelete={onDeleteCustomRule}
              onCompileShacl={onCompileShacl}
              autoOpen
              initialKind={
                addState.choice === "restrict-tool" ? "tool_perm" : "deterministic_ref"
              }
            />
          ) : null}
          {addState.choice === "filter-result" ? (
            <CustomChecksSection busy={customRuleBusy} />
          ) : null}
          {addState.choice === "rewire-builtin" ? (
            <SeamBuilderPanel seamSpecs={seamSpecs} onChange={reload} />
          ) : null}
        </section>
      ) : null}

      {ruleError ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-600">
          {ruleError}
        </div>
      ) : null}

      {addState.phase === "idle" ? (
        <>
          {/* Behavior / first-party toggle errors surface here now that those
              cards live in the Policies surface (Behaviors tab retired). */}
          {behaviorError || builtinPolicyError ? (
            <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.08] px-3 py-2 text-xs text-amber-800">
              {behaviorError ?? builtinPolicyError}
            </div>
          ) : null}

          {/* PRIMARY: the policies-first card list (PR-2 + PR-3). First-party
              policies (verify_before_replying) get a real Switch routed to the
              builtin-policies PATCH; the 4 control-plane behaviors render as
              NUDGE cards routed to the control-plane PATCH. */}
          <PolicyCardList
            catalogPolicies={catalogPolicies}
            ruleRows={ruleRows}
            pendingPresets={pendingPresets}
            busy={customRuleBusy || dashboardBusy}
            scopedInModes={scopedInModes}
            onTogglePolicy={onTogglePolicy}
            onDeletePolicy={onDeletePolicy}
            onTogglePreset={onTogglePreset}
            onToggleCustomRule={onToggleCustomRule}
            onDeleteCustomRule={onDeleteCustomRule}
            onToggleDashboardCheck={handleToggleDashboardCheck}
            onDeleteDashboardCheck={handleDeleteDashboardCheck}
            onDeleteSeamSpec={handleDeleteSeamSpec}
            onToggleBuiltinPolicy={onToggleBuiltinPolicy}
            onToggleControlPlane={onToggleControlPlane}
            pendingBuiltinPolicies={pendingBuiltinPolicies}
            pendingControlPlane={pendingControlPlane}
            citationGateMode={citationGateMode}
            onCitationGateModeChange={onCitationGateModeChange}
            citationGateModePending={citationGateModePending}
            citationGateModeError={citationGateModeError}
          />

          {/* "Your guidance": freeform soft system-prompt text, relocated from
              the retired Behaviors tab. Soft-only — never blocks. */}
          <section className="space-y-2" data-testid="guidance-section">
            <div>
              <h4 className="text-sm font-semibold text-foreground">Your guidance</h4>
              <p className="mt-0.5 text-xs leading-relaxed text-secondary">
                Soft prompt instructions injected into the system prompt every
                turn. The model is asked to follow them but is never forced to.
              </p>
            </div>
            <GuidancePanel
              userRules={userRules}
              rulesSaving={rulesSaving}
              onSaveRules={onSaveRules}
            />
          </section>

          <PrebuiltComponentsPanel />

          {/* ADVANCED (D3): the legacy flat rule table + reusable Evidence /
              Conditions catalogs, kept for debugging under a collapsed
              disclosure below the card list. */}
          <details
            className="group rounded-xl border border-black/[0.06] bg-white"
            data-testid="policies-advanced"
          >
            <summary className="flex cursor-pointer items-center justify-between gap-2 rounded-xl px-4 py-3 text-xs font-semibold text-secondary hover:bg-black/[0.02]">
              <span>Advanced · flat rule list, evidence &amp; conditions</span>
              <span
                aria-hidden
                className="inline-block transition-transform duration-150 group-open:rotate-180"
              >
                ▾
              </span>
            </summary>
            <div className="space-y-4 px-4 pb-4 pt-1">
              <nav
                aria-label="Advanced sub-tabs"
                className="flex rounded-xl border border-black/[0.06] bg-white p-1 text-xs"
              >
                {(
                  [
                    { id: "policies", label: `Rules (${ruleRows.length})` },
                    {
                      id: "evidence",
                      label: `Evidence (${
                        data.catalog.verification.evidenceMenu.length +
                        evidenceTypes.length
                      })`,
                    },
                    {
                      id: "conditions",
                      label: `Conditions (${
                        builtinJudgments.length + conditions.length
                      })`,
                    },
                  ] as ReadonlyArray<{ id: SubTab; label: string }>
                ).map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setSubTab(t.id)}
                    aria-current={subTab === t.id ? "page" : undefined}
                    className={`rounded-lg px-3 py-1.5 font-medium transition-colors ${
                      subTab === t.id
                        ? "bg-primary text-white"
                        : "text-secondary hover:bg-black/[0.04] hover:text-foreground"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </nav>
              {subTab === "policies" ? (
                <PoliciesTable
                  policies={ruleRows}
                  pendingPresets={pendingPresets}
                  busy={customRuleBusy || dashboardBusy}
                  onTogglePreset={onTogglePreset}
                  onToggleCustomRule={onToggleCustomRule}
                  onDeleteCustomRule={onDeleteCustomRule}
                  onToggleDashboardCheck={handleToggleDashboardCheck}
                  onDeleteDashboardCheck={handleDeleteDashboardCheck}
                  onDeleteSeamSpec={handleDeleteSeamSpec}
                  scopedInModes={scopedInModes}
                />
              ) : null}
              {subTab === "evidence" ? (
                <ReusableEvidenceTab
                  entries={evidenceTypes}
                  knownRefs={data.catalog.verification.evidenceMenu}
                />
              ) : null}
              {subTab === "conditions" ? (
                <ReusableConditionsTab
                  entries={conditions}
                  builtinEntries={builtinJudgments}
                />
              ) : null}
            </div>
          </details>
        </>
      ) : (
        <div className="rounded-xl border border-dashed border-black/[0.08] bg-gray-50/60 px-4 py-3 text-xs text-secondary">
          List hidden while adding a policy. Close above to return.
        </div>
      )}
    </div>
  );
}


function AuthoringHeader({
  label,
  onPickDifferent,
  onClose,
}: {
  label: string;
  /** Optional; omit when the surface has its own switcher (the Add-policy
   *  phase renders the Describe/Producer+gate pills + Advanced entries
   *  itself). Guided/Raw wire this back to the Add-policy surface. */
  onPickDifferent?: () => void;
  onClose: () => void;
}): React.ReactElement {
  return (
    <header className="flex items-center justify-between rounded-xl border border-primary/20 bg-primary/[0.02] px-4 py-2">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Authoring
        </p>
        <h3 className="text-sm font-bold text-foreground">{label}</h3>
      </div>
      <div className="flex items-center gap-1">
        {onPickDifferent ? (
          <button
            type="button"
            onClick={onPickDifferent}
            className="rounded-lg px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04]"
          >
            ← Pick different
          </button>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04]"
        >
          Close
        </button>
      </div>
    </header>
  );
}


const LABEL_FOR_CHOICE: Record<AddRuleChoice, string> = {
  "block-answer": "Block bad answer (pre-final)",
  "restrict-tool": "Restrict tool (before-tool)",
  "filter-result": "Filter tool result (after-tool)",
  "rewire-builtin": "Rewire built-in preset (SeamSpec)",
};


/**
 * Phase 3 recipe allowlist UI — per-row toggle (F-UX10, 2026-06-24).
 *
 * The catalog row carries ``packIds: string[]`` from Phase 3
 * (``customize.catalog.RECIPE_ID_TO_PACK_IDS``). When the array is empty the
 * recipe is a UI-only label — toggling persists in ``verification.recipes[]``
 * but the runtime has no packIds to filter, so the row is greyed out and the
 * toggle is disabled (with an explanatory tooltip) to keep the contract
 * honest.
 *
 * Allowlist semantics (single source of truth =
 * ``set_verification_override`` / ``_disabled_recipe_pack_refs``):
 *   - ``enabledRecipeIds.size === 0`` (no operator override) → every recipe
 *     row reads as enabled (byte-identical to legacy behavior).
 *   - Otherwise → only ids in ``enabledRecipeIds`` are enabled; the rest
 *     have their mapped pack's evidence/validator refs filtered out at
 *     assembly time.
 *
 * The PATCH is optimistic: the toggle snaps to the new state immediately,
 * the backend response reconciles the allowlist, and a failure reverts the
 * row and surfaces the error banner so the operator never sees a silent
 * disagreement between UI state and disk state.
 */
function RecipesPanel({
  recipes,
  enabledRecipeIds,
  pendingIds,
  error,
  onToggle,
}: {
  recipes: ReadonlyArray<{ id: string; title: string; category: string; description: string; packIds?: string[]; enabled?: boolean }>;
  enabledRecipeIds: Set<string>;
  pendingIds: Set<string>;
  error: string | null;
  onToggle: (id: string, enabled: boolean) => void;
}): React.ReactElement {
  if (recipes.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-8 text-center text-sm leading-6 text-secondary">
        No packs catalogued yet.
      </div>
    );
  }
  // Allowlist semantics: empty list → no opt-out → every row reads as
  // enabled. Once at least one id is present, only listed ids are enabled.
  const hasExplicitAllowlist = enabledRecipeIds.size > 0;
  return (
    <div className="space-y-2">
      {error ? (
        <div className="mb-4 rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-3 text-xs leading-5 text-amber-800">
          {error}
        </div>
      ) : null}
      <p className="mb-3 text-xs leading-relaxed text-secondary">
        First-party packs and the refs they contribute (rules / behaviors / tools).
        An empty <code>packIds</code> means the label has no live mapping, so the toggle
        is disabled (flipping it would have no runtime effect). Mapped packs can be opted
        in/out via the allowlist: with no override, every pack is enabled; the first
        opt-out seeds the allowlist with every other mapped pack (so only the one you
        turned off is dropped), then the list behaves as an explicit allowlist (only the
        ids you keep on stay enabled).
      </p>
      {recipes.map((r) => {
        const mapped = Array.isArray(r.packIds) && r.packIds.length > 0;
        const checked = hasExplicitAllowlist ? enabledRecipeIds.has(r.id) : true;
        const isPending = pendingIds.has(r.id);
        const toggleDisabled = !mapped || isPending;
        const tooltip = !mapped
          ? "UI label has no live mapping; toggling is a no-op"
          : undefined;
        return (
          <div
            key={r.id}
            className={`flex items-start justify-between gap-4 rounded-xl border px-4 py-3 ${
              mapped ? "border-black/[0.06] bg-white" : "border-black/[0.04] bg-gray-50/60 opacity-70"
            }`}
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <p className="truncate text-sm font-semibold text-foreground">{r.title}</p>
                <span className="inline-flex items-center rounded-full bg-black/5 px-2 py-0.5 text-[11px] font-medium text-secondary">
                  {r.category}
                </span>
                {!mapped ? (
                  <span className="inline-flex items-center rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                    no live effect
                  </span>
                ) : null}
              </div>
              {r.description ? (
                <p className="mt-1 text-xs leading-relaxed text-secondary">{r.description}</p>
              ) : null}
              {mapped ? (
                <p className="mt-1 text-[11px] leading-relaxed text-secondary/80">
                  packs: {r.packIds!.join(", ")}
                </p>
              ) : null}
            </div>
            <span title={tooltip} className="shrink-0">
              <Switch
                checked={checked}
                onToggle={async (next) => onToggle(r.id, next)}
                labelOn={`Disable recipe ${r.title}`}
                labelOff={`Enable recipe ${r.title}`}
                disabled={toggleDisabled}
              />
            </span>
          </div>
        );
      })}
    </div>
  );
}


/**
 * Hooks placeholder — read-only "see your settings.json" surface.
 *
 * HookBus loads handlers from ``~/.magi/settings.json`` (user) and
 * ``<workspace>/.magi/settings.json`` (project). There is no dashboard write
 * path today by design (self-host only, file-based authoring). This panel
 * exists so the sub-nav reaches parity with the four customize layers.
 */
function HooksPanel(): React.ReactElement {
  const exampleSettings = `{
  "hooks": {
    "beforeToolUse": [
      { "module": "my_pkg.hooks", "callable": "audit_tool_call" }
    ],
    "afterTurnEnd": [
      { "module": "my_pkg.hooks", "callable": "log_turn_summary" }
    ]
  }
}`;
  return (
    <div className="space-y-4">
      <PageHint
        title="Hooks — Python callables at lifecycle events"
        can={[
          { text: <>Custom Python at <code>beforeToolUse</code> / <code>afterTurnEnd</code> / etc.</> },
          { text: <>Anything a Preset or Gate cannot express</> },
        ]}
        cannot={[
          { text: <>Declarative gates → use <strong>Verification → Gates</strong></> },
          { text: <>Built-in preset toggles → use <strong>Verification → Presets</strong></> },
        ]}
        note={
          <>
            Authoring is <strong>file-only</strong> (self-host security:
            code-shaped handlers must be explicit in a file). Edit{" "}
            <code>~/.magi/settings.json</code> or{" "}
            <code>&lt;workspace&gt;/.magi/settings.json</code> and restart.
          </>
        }
      />

      <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/60 px-4 py-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          settings.json example
        </p>
        <pre className="mt-2 overflow-auto rounded-lg border border-black/[0.06] bg-white px-3 py-2 text-[11px] leading-snug text-foreground">
          {exampleSettings}
        </pre>
      </div>

      <p className="text-[11px] leading-relaxed text-secondary/80">
        A read-only listing of currently-loaded hook handlers will appear
        here in a follow-up PR; the underlying registry already supports it.
      </p>
    </div>
  );
}
