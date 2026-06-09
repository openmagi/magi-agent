"use client";

import { useCallback, useState } from "react";
import { ShieldCheck, Wrench } from "lucide-react";
import { useCustomize } from "@/lib/customize-api";
import type { CustomizeOverrides } from "@/lib/customize-api";
import { VerificationRuleModal } from "./verification-rule-modal";
import { CustomToolModal } from "./custom-tool-modal";

interface CustomizeRuntimeConsoleProps {
  botId: string;
}

const EMPTY_VERIFICATION: CustomizeOverrides["verification"] = {
  recipes: [],
  harness_presets: [],
  hooks: {},
  custom_rules: [],
};

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

  const [ruleModalOpen, setRuleModalOpen] = useState(false);
  const [toolModalOpen, setToolModalOpen] = useState(false);

  // Local-only override state seeded from the backend snapshot. This phase does
  // not persist changes — that lands in a later phase.
  //
  // Recipes and presets use Record<string,boolean> (same pattern as hooks) so
  // that toggling OFF a default-ON item (remove from array) is not shadowed by
  // the catalog's `enabled: true`. The backend arrays represent "explicitly
  // enabled" items; we seed them as true and resolve via `record[id] ?? item.enabled`.
  const [recipeOverrides, setRecipeOverrides] = useState<Record<string, boolean>>({});
  const [presetOverrides, setPresetOverrides] = useState<Record<string, boolean>>({});
  const [hookOverrides, setHookOverrides] = useState<CustomizeOverrides["verification"]["hooks"]>({});
  const [toolOverrides, setToolOverrides] = useState<Record<string, boolean>>({});

  const openRuleModal = useCallback(() => {
    const v = data?.overrides.verification ?? EMPTY_VERIFICATION;
    const recipes: Record<string, boolean> = {};
    for (const id of v.recipes) recipes[id] = true;
    const presets: Record<string, boolean> = {};
    for (const id of v.harness_presets) presets[id] = true;
    setRecipeOverrides(recipes);
    setPresetOverrides(presets);
    setHookOverrides(v.hooks);
    setRuleModalOpen(true);
  }, [data]);

  const openToolModal = useCallback(() => {
    setToolOverrides(data?.overrides.tools ?? {});
    setToolModalOpen(true);
  }, [data]);

  const handleToggleRecipe = useCallback((id: string, enabled: boolean) => {
    setRecipeOverrides((prev) => ({ ...prev, [id]: enabled }));
  }, []);

  const handleTogglePreset = useCallback((id: string, enabled: boolean) => {
    setPresetOverrides((prev) => ({ ...prev, [id]: enabled }));
  }, []);

  const handleToggleHook = useCallback((name: string, enabled: boolean) => {
    setHookOverrides((prev) => ({ ...prev, [name]: enabled }));
  }, []);

  const handleToggleTool = useCallback((name: string, enabled: boolean) => {
    setToolOverrides((prev) => ({ ...prev, [name]: enabled }));
  }, []);

  return (
    <div className="max-w-5xl space-y-6 pb-20">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-secondary/60">
          {botId ? `route: ${botId}` : "local"}
        </p>
        <h1 className="mt-2 text-2xl font-bold leading-tight text-foreground">Customize</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
          Tune the verification rules that gate your agent&apos;s output and choose which tools it can
          call. Changes apply to this local session only.
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
            recipeOverrides={recipeOverrides}
            presetOverrides={presetOverrides}
            hookOverrides={hookOverrides}
            onToggleRecipe={handleToggleRecipe}
            onTogglePreset={handleTogglePreset}
            onToggleHook={handleToggleHook}
          />
          <CustomToolModal
            open={toolModalOpen}
            onClose={() => setToolModalOpen(false)}
            tools={data.catalog.tools}
            overrides={toolOverrides}
            onToggle={handleToggleTool}
          />
        </>
      ) : null}
    </div>
  );
}
