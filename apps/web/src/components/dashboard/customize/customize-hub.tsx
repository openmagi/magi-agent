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
import { ShieldCheck, Wrench, Layers, Webhook, Plus, SlidersHorizontal, Gauge, Drama } from "lucide-react";
import {
  useCustomize,
  patchToolOverride,
  patchVerificationOverride,
  patchRecipeOverride,
  patchControlPlaneOverride,
  putRules,
  putCustomRule,
  deleteCustomRule,
  compileCustomRule,
  getBudgets,
  putBudgets,
} from "@/lib/customize-api";
import type {
  BudgetsResponse,
  ConversationTurn,
  CustomRule,
  CustomizeOverrides,
  ShaclCompileResponse,
  VerificationBudgets,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import { AddRulePicker, type AddRuleChoice } from "./add-rule-modal";
import { AddPolicyModePicker, type AddPolicyMode } from "./add-policy-mode-picker";
import { GuidedWizard } from "./guided-wizard";
import { NlRuleCompose } from "./nl-rule-compose";
import {
  CustomRulesSection,
} from "./verification-rule-modal";
import { CustomChecksSection } from "./custom-checks-section";
import { CustomToolPanel } from "./custom-tool-modal";
import { BehaviorsPanel } from "./behaviors-panel";
import { BudgetsTab } from "./budgets-tab";
import { GuidancePanel } from "./guidance-panel";
import { ModesPanel } from "./modes-panel";
import { PageHint } from "./page-hint";
import { PoliciesTable } from "./policies-table";
import { ReusableEvidenceTab } from "./reusable-evidence-tab";
import { ReusableConditionsTab } from "./reusable-conditions-tab";
import { SeamBuilderPanel } from "./seam-builder-panel";
import {
  extractBuiltinJudgmentRefs,
  extractEvidenceTypes,
  extractNamedConditions,
  unifyPolicies,
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
    label: "Rules",
    icon: <ShieldCheck className="h-4 w-4" />,
    description:
      "Enforcement: rules that gate the agent (block / audit / require). Built-in + your own, same shape, same controls. Toggles here set the GLOBAL default for every turn; to apply a rule only in a specific stance, scope it in Modes.",
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
    id: "behaviors",
    label: "Behaviors",
    icon: <SlidersHorizontal className="h-4 w-4" />,
    description:
      "Capability (soft): things that nudge or help the agent but never block. Your freeform guidance plus the built-in in-context behaviors (facts survey, goal nudge, tool-synthesis nudge, empty-response recovery).",
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
      "First-party packs that contribute rules, behaviors, and tools. Opt a pack in or out (allowlist semantics); opting out drops the refs it contributes.",
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

  const [section, setSection] = useState<CustomizeSection>(initialSection);
  useEffect(() => {
    setSection(initialSection);
  }, [initialSection]);

  const handleSection = useCallback(
    (next: CustomizeSection) => {
      setSection(next);
      onSectionChange?.(next);
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
  const [behaviorOverrides, setBehaviorOverrides] = useState<Record<string, boolean>>({});
  const [behaviorPending, setBehaviorPending] = useState<Set<string>>(new Set());
  const [behaviorError, setBehaviorError] = useState<string | null>(null);

  useEffect(() => {
    setBehaviorOverrides(data?.overrides.control_plane ?? {});
  }, [data]);

  const handleToggleBehavior = useCallback(
    (id: string, enabled: boolean) => {
      setBehaviorOverrides((prev) => ({ ...prev, [id]: enabled }));
      setBehaviorError(null);
      setBehaviorPending((prev) => new Set(prev).add(id));
      patchControlPlaneOverride(agentFetch, id, enabled)
        .then((overrides) => {
          setBehaviorOverrides(overrides.control_plane);
        })
        .catch((err: unknown) => {
          setBehaviorOverrides((prev) => ({ ...prev, [id]: !enabled }));
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
    [agentFetch],
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
            onCompileShacl={handleCompileShacl}
            ruleError={ruleError}
          />
        ) : null}

        {section === "behaviors" ? (
          <div className="space-y-8">
            <section className="space-y-3">
              <div>
                <h3 className="text-sm font-bold text-foreground">Your guidance</h3>
                <p className="mt-0.5 text-xs leading-relaxed text-secondary">
                  Soft prompt instructions injected into the system prompt every
                  turn. The model is asked to follow them but is never forced to.
                </p>
              </div>
              <GuidancePanel
                userRules={userRules}
                rulesSaving={rulesSaving}
                onSaveRules={handleSaveRules}
              />
            </section>
            <section className="space-y-3">
              <div>
                <h3 className="text-sm font-bold text-foreground">Built-in behaviors</h3>
                <p className="mt-0.5 text-xs leading-relaxed text-secondary">
                  In-context nudges and recovery (facts survey, goal nudge,
                  tool-synthesis nudge, empty-response recovery). Seeded ON by the
                  lab/dogfood profile; a toggle here overrides that. Never blocks.
                </p>
              </div>
              <BehaviorsPanel
                behaviors={data.catalog.controlPlane ?? []}
                overrides={behaviorOverrides}
                onToggle={handleToggleBehavior}
                pendingIds={behaviorPending}
                error={behaviorError}
              />
            </section>
          </div>
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
          <RecipesPanel
            recipes={recipes}
            enabledRecipeIds={enabledRecipeIds}
            pendingIds={recipePending}
            error={recipeError}
            onToggle={handleToggleRecipe}
          />
        ) : null}

        {section === "hooks" ? <HooksPanel /> : null}
      </section>
    </div>
  );
}


/**
 * Policies section mount — single-list view over the four backend stores
 * unified via :func:`unifyPolicies`. Provides a 3-sub-tab surface
 * (Policies / Evidence types / Conditions) and a 3-mode Add Policy entry
 * (NL / Guided[placeholder] / Raw).
 *
 * The legacy ``RulesTable`` + 4-card AddRulePicker still mount under
 * the Raw mode so power-users keep their direct-form path while we land
 * the Guided wizard in PR-E2.
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
  onCompileShacl: (
    nlText: string,
    sampleRecords?: unknown[],
    priorTurns?: ConversationTurn[],
  ) => Promise<ShaclCompileResponse>;
  ruleError: string | null;
}): React.ReactElement {
  // 5-phase Add state. picking_mode shows the NL/Guided/Raw cards; nl,
  // raw_picking, and raw_authoring are the actual authoring surfaces.
  // PR-F-HANDOFF — the ``nl`` phase carries an optional ``nlPrefill`` seed
  // sourced from the guided wizard's "Continue in NL" handoff. The NL
  // surface reads it via NlRuleCompose's ``initialNlText`` prop.
  type AddState =
    | { phase: "idle" }
    | { phase: "picking_mode" }
    | { phase: "nl"; nlPrefill?: string }
    | { phase: "guided" }
    | { phase: "raw_picking" }
    | { phase: "raw_authoring"; choice: AddRuleChoice };
  const [addState, setAddState] = useState<AddState>({ phase: "idle" });

  type SubTab = "policies" | "evidence" | "conditions";
  const [subTab, setSubTab] = useState<SubTab>("policies");

  const agentFetch = useAgentFetch();
  const [dashboardChecks, setDashboardChecks] = useState<DashboardCheck[]>([]);
  const [dashboardBusy, setDashboardBusy] = useState(false);

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

  const policies = useMemo(
    () =>
      unifyPolicies({
        catalog: data.catalog,
        overrides: { ...data.overrides, verification: { ...data.overrides.verification, custom_rules: customRules, preset_overrides: presetOverrides } },
        dashboardChecks,
      }),
    [data, customRules, presetOverrides, dashboardChecks],
  );
  const evidenceTypes = useMemo(() => extractEvidenceTypes(policies), [policies]);
  const conditions = useMemo(() => extractNamedConditions(policies), [policies]);
  // PR-F-UX5 — built-in verdict primitives sourced from catalog.judgmentMenu.
  // The Conditions tab merges this with user-authored conditions; the
  // counter sums both halves so it matches what the tab body renders.
  const builtinJudgments = useMemo(
    () => extractBuiltinJudgmentRefs(data.catalog),
    [data.catalog],
  );
  const seamSpecs = data.overrides.verification.seam_specs ?? [];

  const handleModePick = (mode: AddPolicyMode) => {
    if (mode === "nl") setAddState({ phase: "nl" });
    else if (mode === "guided") setAddState({ phase: "guided" });
    else if (mode === "raw") setAddState({ phase: "raw_picking" });
  };

  return (
    <div className="space-y-5">
      {/* sub-tab nav + Add button */}
      <div className="flex items-center justify-between gap-3">
        <nav
          aria-label="Policy sub-tabs"
          className="flex rounded-xl border border-black/[0.06] bg-white p-1 text-xs"
        >
          {(
            [
              { id: "policies", label: `Policies (${policies.length})` },
              // PR-F-UX5 — Evidence counter = built-in evidence menu (raw
              // producer records the runtime knows about) + the user-consumed
              // refs the policies-derived index has surfaced. Both halves
              // appear in the Evidence tab body, so the counter mirrors the
              // visible row count.
              {
                id: "evidence",
                label: `Evidence (${
                  data.catalog.verification.evidenceMenu.length
                  + evidenceTypes.length
                })`,
              },
              // PR-F-UX5 — Conditions counter = built-in verdict primitives
              // (judgmentMenu) + user-authored named conditions. The tab body
              // merges them under origin badges so the counter equals the row
              // count there too.
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
        {addState.phase === "idle" && subTab === "policies" ? (
          <button
            type="button"
            onClick={() => setAddState({ phase: "picking_mode" })}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90"
          >
            <Plus className="h-3.5 w-3.5" />
            Add policy
          </button>
        ) : null}
      </div>

      {addState.phase === "picking_mode" ? (
        <AddPolicyModePicker
          onCancel={() => setAddState({ phase: "idle" })}
          onPick={handleModePick}
        />
      ) : null}

      {addState.phase === "nl" ? (
        <section className="space-y-2">
          <AuthoringHeader
            label="Natural language"
            onPickDifferent={() => setAddState({ phase: "picking_mode" })}
            onClose={() => setAddState({ phase: "idle" })}
          />
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
          onPickDifferent={() => setAddState({ phase: "picking_mode" })}
          onCancel={() => setAddState({ phase: "idle" })}
          // PR-F-HANDOFF — operator clicked "Continue in NL" mid-wizard.
          // Flip to the NL surface and seed the textarea with the
          // serialized draft primer so the chat resumes where the
          // wizard left off.
          onContinueInNl={(primer) =>
            setAddState({ phase: "nl", nlPrefill: primer })
          }
        />
      ) : null}

      {addState.phase === "raw_picking" ? (
        <section className="space-y-2">
          <AuthoringHeader
            label="Advanced — pick a rule kind"
            onPickDifferent={() => setAddState({ phase: "picking_mode" })}
            onClose={() => setAddState({ phase: "idle" })}
          />
          <AddRulePicker
            onCancel={() => setAddState({ phase: "picking_mode" })}
            onPick={(choice) =>
              setAddState({ phase: "raw_authoring", choice })
            }
          />
        </section>
      ) : null}

      {addState.phase === "raw_authoring" ? (
        <section className="space-y-2">
          <AuthoringHeader
            label={`Advanced — ${LABEL_FOR_CHOICE[addState.choice]}`}
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
          {subTab === "policies" ? (
            <PoliciesTable
              policies={policies}
              pendingPresets={pendingPresets}
              busy={customRuleBusy || dashboardBusy}
              onTogglePreset={onTogglePreset}
              onToggleCustomRule={onToggleCustomRule}
              onDeleteCustomRule={onDeleteCustomRule}
              onToggleDashboardCheck={handleToggleDashboardCheck}
              onDeleteDashboardCheck={handleDeleteDashboardCheck}
              onDeleteSeamSpec={handleDeleteSeamSpec}
            />
          ) : null}
          {subTab === "evidence" ? (
            <ReusableEvidenceTab entries={evidenceTypes} />
          ) : null}
          {subTab === "conditions" ? (
            <ReusableConditionsTab
              entries={conditions}
              builtinEntries={builtinJudgments}
            />
          ) : null}
        </>
      ) : (
        <div className="rounded-xl border border-dashed border-black/[0.08] bg-gray-50/60 px-4 py-3 text-xs text-secondary">
          List hidden while adding a policy. Cancel above to return.
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
  onPickDifferent: () => void;
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
        <button
          type="button"
          onClick={onPickDifferent}
          className="rounded-lg px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04]"
        >
          ← Pick different
        </button>
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
function RecipeToggle({
  checked,
  onChange,
  label,
  disabled,
  title,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  title?: string;
}): React.ReactElement {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={title}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${
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
            <RecipeToggle
              checked={checked}
              onChange={(next) => onToggle(r.id, next)}
              label={`Toggle recipe ${r.title}`}
              disabled={toggleDisabled}
              title={tooltip}
            />
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
