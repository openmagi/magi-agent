"use client";

import { useCallback, useState } from "react";
import { ShieldCheck, Wrench } from "lucide-react";
import {
  useCustomize,
  patchToolOverride,
  patchVerificationOverride,
  putRules,
  putCustomRule,
  deleteCustomRule,
  compileCustomRule,
} from "@/lib/customize-api";
import type { ConversationTurn, CustomRule, ShaclCompileResponse } from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";
import { VerificationRuleModal } from "./verification-rule-modal";
import { CustomToolModal } from "./custom-tool-modal";

interface CustomizeRuntimeConsoleProps {
  botId: string;
}

function Card({
  icon,
  iconClass,
  title,
  description,
  onClick,
  disabled,
}: {
  icon: React.ReactNode;
  iconClass: string;
  title: string;
  description: string;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group flex items-start gap-4 rounded-2xl border border-black/[0.06] bg-white px-5 py-5 text-left transition-all hover:border-primary/20 hover:shadow-sm disabled:opacity-50"
    >
      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-colors ${iconClass}`}>
        {icon}
      </div>
      <div>
        <p className="text-sm font-semibold text-foreground">{title}</p>
        <p className="mt-1 text-xs leading-relaxed text-secondary">{description}</p>
      </div>
    </button>
  );
}

export function CustomizeRuntimeConsole({ botId }: CustomizeRuntimeConsoleProps): React.JSX.Element {
  const { data, loading, error, reload } = useCustomize();
  const agentFetch = useAgentFetch();

  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [toolModalOpen, setToolModalOpen] = useState(false);

  // Override state. Presets persist via PATCH /v1/app/customize/verification/...
  // (explicit tri-state in preset_overrides); tools via PATCH .../tools/{name}.
  const [presetOverrides, setPresetOverrides] = useState<Record<string, boolean>>({});
  const [toolOverrides, setToolOverrides] = useState<Record<string, boolean>>({});

  // Per-item in-flight sets while a PATCH is running.
  const [presetPending, setPresetPending] = useState<Set<string>>(new Set());
  const [toolPending, setToolPending] = useState<Set<string>>(new Set());

  // Structured custom rules.
  const [customRules, setCustomRules] = useState<CustomRule[]>([]);
  const [customRuleBusy, setCustomRuleBusy] = useState(false);

  // USER-RULES.md editor state.
  const [userRules, setUserRules] = useState("");
  const [rulesSaving, setRulesSaving] = useState(false);

  // Transient errors surfaced inside each modal on PATCH failure.
  const [ruleError, setRuleError] = useState<string | null>(null);
  const [toolError, setToolError] = useState<string | null>(null);

  const openRuleModal = useCallback(() => {
    setPresetOverrides(data?.overrides.verification.preset_overrides ?? {});
    setCustomRules(data?.overrides.verification.custom_rules ?? []);
    setUserRules(data?.overrides.user_rules ?? "");
    setRuleError(null);
    setRuleModalOpen(true);
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

  const openToolModal = useCallback(() => {
    setToolOverrides(data?.overrides.tools ?? {});
    setToolModalOpen(true);
  }, [data]);

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

  const handleToggleTool = useCallback(
    (name: string, enabled: boolean) => {
      // Optimistic update
      setToolOverrides((prev) => ({ ...prev, [name]: enabled }));
      setToolError(null);
      setToolPending((prev) => new Set(prev).add(name));

      patchToolOverride(agentFetch, name, enabled)
        .then((overrides) => {
          setToolOverrides(overrides.tools);
        })
        .catch((err: unknown) => {
          // Revert the optimistic update
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

  return (
    <div className="max-w-5xl space-y-6 pb-20">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-secondary/60">
          {botId ? `route: ${botId}` : "local"}
        </p>
        <h1 className="mt-2 text-2xl font-bold leading-tight text-foreground">Customize</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
          Tune the verification rules that gate your agent&apos;s output and choose which tools it can
          call. Changes are saved to the local runtime immediately.
        </p>
      </header>

      {loading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="h-24 animate-pulse rounded-2xl border border-black/[0.06] bg-gray-50" />
          <div className="h-24 animate-pulse rounded-2xl border border-black/[0.06] bg-gray-50" />
        </div>
      ) : error ? (
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
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Card
            icon={<ShieldCheck className="h-5 w-5 text-blue-600" />}
            iconClass="bg-blue-50 group-hover:bg-blue-100"
            title="Verification Rules"
            description="Recipes, harness presets, and always-on safety hooks that gate output."
            onClick={openRuleModal}
            disabled={!data}
          />
          <Card
            icon={<Wrench className="h-5 w-5 text-purple-600" />}
            iconClass="bg-purple-50 group-hover:bg-purple-100"
            title="Custom Tools"
            description="Enable or disable the tools your agent is allowed to call."
            onClick={openToolModal}
            disabled={!data}
          />
        </div>
      )}

      {data ? (
        <>
          <VerificationRuleModal
            open={ruleModalOpen}
            onClose={() => setRuleModalOpen(false)}
            catalog={data.catalog.verification}
            presetOverrides={presetOverrides}
            pendingPresets={presetPending}
            onTogglePreset={handleTogglePreset}
            customRules={customRules}
            onAddCustomRule={handleAddCustomRule}
            onToggleCustomRule={handleToggleCustomRule}
            onDeleteCustomRule={handleDeleteCustomRule}
            customRuleBusy={customRuleBusy}
            userRules={userRules}
            rulesSaving={rulesSaving}
            onSaveRules={handleSaveRules}
            onCompileShacl={handleCompileShacl}
            error={ruleError}
          />
          <CustomToolModal
            open={toolModalOpen}
            onClose={() => setToolModalOpen(false)}
            tools={data.catalog.tools}
            overrides={toolOverrides}
            onToggle={handleToggleTool}
            pendingNames={toolPending}
            error={toolError}
          />
        </>
      ) : null}
    </div>
  );
}
