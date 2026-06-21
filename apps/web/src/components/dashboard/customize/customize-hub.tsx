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
import { ShieldCheck, Wrench, Layers, Webhook, Wand2, Plus } from "lucide-react";
import {
  useCustomize,
  patchToolOverride,
  patchVerificationOverride,
  putRules,
  putCustomRule,
  deleteCustomRule,
  compileCustomRule,
} from "@/lib/customize-api";
import type {
  ConversationTurn,
  CustomRule,
  ShaclCompileResponse,
} from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import { AddRuleModal, type AddRuleChoice } from "./add-rule-modal";
import {
  CustomRulesSection,
} from "./verification-rule-modal";
import { CustomChecksSection } from "./custom-checks-section";
import { CustomToolPanel } from "./custom-tool-modal";
import { GuidancePanel } from "./guidance-panel";
import { PageHint } from "./page-hint";
import { RulesTable } from "./rules-table";
import { SeamBuilderPanel } from "./seam-builder-panel";

export type CustomizeSection =
  | "rules"
  | "guidance"
  | "tools"
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
      "Everything that gates the agent — built-in presets, your custom rules, after-tool checks, and SeamSpec rewires, all in one list.",
  },
  {
    id: "guidance",
    label: "Guidance",
    icon: <Wand2 className="h-4 w-4" />,
    description:
      "Soft prompt instructions injected into the system prompt every turn. The model is asked to follow them but is not forced to.",
  },
  {
    id: "tools",
    label: "Tools",
    icon: <Wrench className="h-4 w-4" />,
    description: "Enable or disable individual tools.",
  },
  {
    id: "recipes",
    label: "Recipes",
    icon: <Layers className="h-4 w-4" />,
    description: "Opt out of first-party recipe packs (allowlist semantics).",
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

  const recipes = useMemo(() => data?.catalog.verification.recipes ?? [], [data]);

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

        {section === "guidance" ? (
          <GuidancePanel
            userRules={userRules}
            rulesSaving={rulesSaving}
            onSaveRules={handleSaveRules}
          />
        ) : null}

        {section === "tools" ? (
          <CustomToolPanel
            tools={data.catalog.tools}
            overrides={toolOverrides}
            onToggle={handleToggleTool}
            pendingNames={toolPending}
            error={toolError}
          />
        ) : null}

        {section === "recipes" ? <RecipesPanel recipes={recipes} /> : null}

        {section === "hooks" ? <HooksPanel /> : null}
      </section>
    </div>
  );
}


/**
 * Rules section mount — orchestrates the unified RulesTable plus the
 * AddRuleModal + per-choice inline authoring form. The forms themselves
 * (CustomRulesSection / CustomChecksSection / SeamBuilderPanel) are reused
 * as-is so authoring logic stays exactly the same; this wrapper is
 * navigation-only.
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
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [activeChoice, setActiveChoice] = useState<AddRuleChoice | null>(null);
  const seamSpecs = data.overrides.verification.seam_specs ?? [];

  return (
    <div className="space-y-5">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => setAddModalOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90"
        >
          <Plus className="h-3.5 w-3.5" />
          Add rule
        </button>
      </div>

      {ruleError ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-600">
          {ruleError}
        </div>
      ) : null}

      <RulesTable
        catalog={data.catalog.verification}
        presetOverrides={presetOverrides}
        pendingPresets={pendingPresets}
        onTogglePreset={onTogglePreset}
        customRules={customRules}
        customRuleBusy={customRuleBusy}
        onToggleCustomRule={onToggleCustomRule}
        onDeleteCustomRule={onDeleteCustomRule}
        seamSpecs={seamSpecs}
      />

      {activeChoice ? (
        <section
          aria-labelledby="rules-active-form"
          className="rounded-xl border border-primary/20 bg-primary/[0.02] p-4"
        >
          <header className="mb-3 flex items-center justify-between">
            <h3
              id="rules-active-form"
              className="text-sm font-semibold text-foreground"
            >
              Authoring: {LABEL_FOR_CHOICE[activeChoice]}
            </h3>
            <button
              type="button"
              onClick={() => setActiveChoice(null)}
              className="rounded-lg px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04]"
            >
              Close
            </button>
          </header>

          {activeChoice === "block-answer" || activeChoice === "restrict-tool" ? (
            <CustomRulesSection
              menu={data.catalog.verification.customRuleMenu}
              rules={customRules}
              busy={customRuleBusy}
              onAdd={onAddCustomRule}
              onToggle={onToggleCustomRule}
              onDelete={onDeleteCustomRule}
              onCompileShacl={onCompileShacl}
            />
          ) : null}

          {activeChoice === "filter-result" ? (
            <CustomChecksSection busy={customRuleBusy} />
          ) : null}

          {activeChoice === "rewire-builtin" ? (
            <SeamBuilderPanel seamSpecs={seamSpecs} onChange={reload} />
          ) : null}
        </section>
      ) : null}

      <AddRuleModal
        open={addModalOpen}
        onClose={() => setAddModalOpen(false)}
        onPick={(choice) => {
          setActiveChoice(choice);
          setAddModalOpen(false);
        }}
      />
    </div>
  );
}


const LABEL_FOR_CHOICE: Record<AddRuleChoice, string> = {
  "block-answer": "Block bad answer (pre-final)",
  "restrict-tool": "Restrict tool (before-tool)",
  "filter-result": "Filter tool result (after-tool)",
  "rewire-builtin": "Rewire built-in preset (SeamSpec)",
};


/**
 * Phase 3 recipe allowlist UI — read-only list for now.
 *
 * The catalog row carries ``packIds: string[]`` from Phase 3
 * (``customize.catalog.RECIPE_ID_TO_PACK_IDS``). When the array is empty the
 * recipe is a UI-only label — toggling does nothing in the runtime, so the row
 * is greyed out and honest about it. When non-empty, the toggle would set the
 * ``enabled_recipes`` allowlist; this PR ships the read-only surface only.
 */
function RecipesPanel({ recipes }: { recipes: ReadonlyArray<{ id: string; title: string; category: string; description: string; packIds?: string[]; enabled?: boolean }> }): React.ReactElement {
  if (recipes.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-8 text-center text-sm leading-6 text-secondary">
        No recipes catalogued yet.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <p className="mb-3 text-xs leading-relaxed text-secondary">
        Recipes contributed by first-party packs. An empty <code>packIds</code> means the
        UI label has no live mapping — toggling it would be a no-op, so the row is
        greyed out. Mapped recipes can be opted in/out via the allowlist (write surface
        ships in a follow-up PR).
      </p>
      {recipes.map((r) => {
        const mapped = Array.isArray(r.packIds) && r.packIds.length > 0;
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
