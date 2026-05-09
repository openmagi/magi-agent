"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import {
  getModelOptions,
  normalizeModelSelectionForSettings,
  type ModelOption,
} from "@/lib/models/model-options";
import {
  applyRouterPickerMode,
  getRouterPickerMode,
  ROUTER_PICKER_OPTIONS,
  type RouterPickerMode,
} from "@/lib/models/router-tier";

interface ChatModelPickerProps {
  botId: string;
  modelSelection: string;
  routerType?: string | null;
  apiKeyMode: string;
  subscriptionPlan?: string | null;
  persistMode?: "bot" | "local";
  menuPlacement?: "bottom" | "top";
  onModelSelectionChange?: (modelSelection: string, routerType: string) => void;
}

function ensureSelectedOption(options: ModelOption[], value: string): ModelOption[] {
  if (options.some((option) => option.value === value)) return options;
  return [{ value, label: value }, ...options];
}

function Dropdown({
  label,
  options,
  value,
  onChange,
  disabled,
  menuPlacement = "bottom",
}: {
  label: string;
  options: Array<{ value: string; label: string; description?: string }>;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  menuPlacement?: "bottom" | "top";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  const selected = options.find((o) => o.value === value);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={label}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="flex h-11 max-w-[13rem] cursor-pointer items-center gap-1.5 rounded-lg border border-transparent bg-white/70 px-3 text-xs font-medium text-foreground/80 outline-none transition-all duration-200 hover:bg-white focus:border-primary/30 focus:ring-2 focus:ring-primary/10 disabled:cursor-wait disabled:opacity-60 sm:h-8 sm:px-2.5"
      >
        <span className="truncate">{selected?.label ?? value}</span>
        <svg className={`h-3 w-3 shrink-0 text-secondary transition-transform ${open ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none">
          <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div
          className={`absolute left-0 z-50 min-w-[180px] overflow-hidden rounded-xl border border-black/[0.08] bg-white/95 py-1 shadow-lg backdrop-blur-xl ${
            menuPlacement === "top" ? "bottom-full mb-1" : "top-full mt-1"
          }`}
        >
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
              className={`flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-xs transition-colors ${
                option.value === value
                  ? "bg-primary/[0.06] font-semibold text-primary"
                  : "text-foreground/80 hover:bg-black/[0.03]"
              }`}
            >
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${option.value === value ? "bg-primary" : "bg-transparent"}`} />
              <span className="truncate">{option.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function ChatModelPicker({
  botId,
  modelSelection,
  routerType,
  apiKeyMode,
  subscriptionPlan,
  persistMode = "bot",
  menuPlacement = "bottom",
  onModelSelectionChange,
}: ChatModelPickerProps) {
  const authFetch = useAuthFetch();
  const [selectedModel, setSelectedModel] = useState(() =>
    normalizeModelSelectionForSettings(modelSelection),
  );
  const [currentRouterType, setCurrentRouterType] = useState(routerType ?? "standard");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pickerMode = useMemo(
    () => getRouterPickerMode(selectedModel, currentRouterType),
    [selectedModel, currentRouterType],
  );

  useEffect(() => {
    setSelectedModel(normalizeModelSelectionForSettings(modelSelection));
    setCurrentRouterType(routerType ?? "standard");
  }, [modelSelection, routerType]);

  const advancedOptions = useMemo(
    () => ensureSelectedOption(getModelOptions(subscriptionPlan), selectedModel),
    [selectedModel, subscriptionPlan],
  );

  const saveModel = useCallback(
    async (nextModelSelection: string, nextRouterType: string) => {
      setError(null);
      const prevModel = selectedModel;
      const prevRouter = currentRouterType;
      setSelectedModel(nextModelSelection);
      setCurrentRouterType(nextRouterType);

      if (persistMode === "local") {
        onModelSelectionChange?.(nextModelSelection, nextRouterType);
        return;
      }

      setSaving(true);
      try {
        const res = await authFetch(`/api/bots/${botId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model_selection: nextModelSelection,
            router_type: nextRouterType,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(
            typeof data.error === "string" ? data.error : "Failed to update model",
          );
        }
        const savedModel =
          typeof data.model_selection === "string"
            ? normalizeModelSelectionForSettings(data.model_selection)
            : nextModelSelection;
        setSelectedModel(savedModel);
        onModelSelectionChange?.(savedModel, nextRouterType);
      } catch (err) {
        setSelectedModel(prevModel);
        setCurrentRouterType(prevRouter);
        setError(err instanceof Error ? err.message : "Failed to update model");
      } finally {
        setSaving(false);
      }
    },
    [authFetch, botId, onModelSelectionChange, persistMode, selectedModel, currentRouterType],
  );

  if (apiKeyMode !== "platform_credits") return null;

  return (
    <div
      className="relative flex max-w-full flex-wrap items-center justify-end gap-1 rounded-xl border border-black/[0.06] bg-white/80 p-1 shadow-[0_1px_8px_rgba(15,23,42,0.06)] backdrop-blur"
      data-chat-model-picker="true"
    >
      <Dropdown
        label="Router tier"
        options={ROUTER_PICKER_OPTIONS}
        value={pickerMode}
        onChange={(mode) => {
          const { modelSelection: nextModel, routerType: nextRouter } = applyRouterPickerMode(
            mode as RouterPickerMode,
            selectedModel as Parameters<typeof applyRouterPickerMode>[1],
          );
          void saveModel(nextModel, nextRouter);
        }}
        disabled={saving}
        menuPlacement={menuPlacement}
      />
      {pickerMode === "advanced" && (
        <Dropdown
          label="Model"
          options={advancedOptions}
          value={selectedModel}
          onChange={(value) => void saveModel(value, "standard")}
          disabled={saving}
          menuPlacement={menuPlacement}
        />
      )}
      {saving && (
        <span
          className="pointer-events-none h-3 w-3 rounded-full border border-primary/30 border-t-primary animate-spin"
          aria-hidden="true"
        />
      )}
      {error && (
        <span
          className="pointer-events-none absolute left-0 top-full mt-1 whitespace-nowrap rounded-md border border-red-500/15 bg-white px-2 py-1 text-[11px] text-red-500 shadow-sm"
          role="status"
        >
          {error}
        </span>
      )}
    </div>
  );
}
