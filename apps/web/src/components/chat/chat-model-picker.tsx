"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuthFetch } from "@/hooks/use-auth-fetch";
import {
  getModelOptions,
  normalizeModelSelectionForSettings,
  type ModelOption,
} from "@/lib/models/model-options";
import { filterModelOptionsByConfiguredProviders } from "@/lib/models/model-availability";
import { UNRESOLVED_MODEL_SENTINEL } from "@/chat-core";

interface ChatModelPickerProps {
  botId: string;
  modelSelection: string;
  persistMode?: "bot" | "local";
  menuPlacement?: "bottom" | "top";
  onModelSelectionChange?: (modelSelection: string) => void;
  /** Surface the "Magi (managed)" hosted-inference option (OSS desktop app with
   * managed inference available). Only meaningful in local mode. */
  managedInferenceAvailable?: boolean;
}

// Friendly labels for selections that map to a router rather than a concrete
// model, so the local flat picker never surfaces a raw routing token. OSS does
// not run a smart router, but chat-core's persisted channel default is still
// `clawy_smart_routing`, so we display it as a human label until the user picks
// a concrete model.
const LOCAL_FLAT_FALLBACK_LABELS: Record<string, string> = {
  clawy_smart_routing: "Smart Routing",
};

function localFlatOptions(selectedModel: string, includeManaged: boolean): ModelOption[] {
  const options = getModelOptions(null, { includeManagedInference: includeManaged });
  if (options.some((option) => option.value === selectedModel)) return options;
  return [
    { value: selectedModel, label: LOCAL_FLAT_FALLBACK_LABELS[selectedModel] ?? selectedModel },
    ...options,
  ];
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
    <div ref={ref} className="relative min-w-0 flex-1 sm:flex-none">
      <button
        type="button"
        aria-label={label}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="flex h-10 w-full min-w-0 cursor-pointer items-center justify-between gap-1.5 rounded-md border border-black/[0.06] bg-black/[0.025] px-2.5 text-xs font-medium text-foreground/80 outline-none transition-colors duration-200 hover:bg-black/[0.045] focus:border-primary/30 focus:ring-2 focus:ring-primary/10 disabled:cursor-wait disabled:opacity-60 sm:max-w-[13rem]"
      >
        <span className="truncate">{selected?.label ?? value}</span>
        <svg className={`h-3 w-3 shrink-0 text-secondary transition-transform ${open ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none">
          <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div
          className={`absolute left-0 z-50 min-w-full max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-black/[0.08] bg-white/95 py-1 shadow-lg backdrop-blur-xl ${
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
  persistMode = "bot",
  menuPlacement = "bottom",
  onModelSelectionChange,
  managedInferenceAvailable = false,
}: ChatModelPickerProps) {
  const authFetch = useAuthFetch();
  const [selectedModel, setSelectedModel] = useState(() =>
    normalizeModelSelectionForSettings(modelSelection),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Local self-hosted bots: only advertise models whose provider has a key, so
  // the picker never offers a model that fails with an empty response. `null`
  // means "unknown yet" → fail open (show everything).
  const [configuredProviders, setConfiguredProviders] = useState<ReadonlySet<string> | null>(null);

  useEffect(() => {
    if (persistMode !== "local") return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await authFetch("/v1/app/providers");
        if (!res.ok) return;
        const data = (await res.json()) as {
          providers?: Array<{ name?: string; configured?: boolean }>;
        };
        if (cancelled || !Array.isArray(data.providers)) return;
        setConfiguredProviders(
          new Set(
            data.providers
              .filter((p) => p.configured && typeof p.name === "string")
              .map((p) => p.name as string),
          ),
        );
      } catch {
        // Fail open: leave configuredProviders null so all options stay visible.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authFetch, persistMode]);

  useEffect(() => {
    setSelectedModel(normalizeModelSelectionForSettings(modelSelection));
  }, [modelSelection]);

  const visibleOptions = useMemo(() => {
    // Managed inference is a desktop-only (local) tier.
    const includeManaged = persistMode === "local" && managedInferenceAvailable;
    const base = localFlatOptions(selectedModel, includeManaged);
    if (persistMode !== "local" || !configuredProviders) return base;
    return filterModelOptionsByConfiguredProviders(base, configuredProviders, selectedModel);
  }, [selectedModel, persistMode, configuredProviders, managedInferenceAvailable]);

  const saveModel = useCallback(
    async (nextModelSelection: string) => {
      setError(null);
      const prevModel = selectedModel;
      setSelectedModel(nextModelSelection);

      if (persistMode === "local") {
        onModelSelectionChange?.(nextModelSelection);
        return;
      }

      setSaving(true);
      try {
        const res = await authFetch(`/api/bots/${botId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model_selection: nextModelSelection,
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
        onModelSelectionChange?.(savedModel);
      } catch (err) {
        setSelectedModel(prevModel);
        setError(err instanceof Error ? err.message : "Failed to update model");
      } finally {
        setSaving(false);
      }
    },
    [authFetch, botId, onModelSelectionChange, persistMode, selectedModel],
  );

  // OSS local picker: when chat-core's persisted default sentinel is shown
  // and the providers fetch has resolved, auto-pick the first concrete model
  // the user can actually run, so the first turn doesn't hit a non-existent
  // smart router. Skip when the user has already picked a concrete model.
  useEffect(() => {
    if (persistMode !== "local") return;
    if (selectedModel !== UNRESOLVED_MODEL_SENTINEL) return;
    if (!configuredProviders) return;
    const firstConcrete = visibleOptions.find(
      (option) => option.value !== UNRESOLVED_MODEL_SENTINEL,
    );
    if (!firstConcrete) return;
    void saveModel(firstConcrete.value);
  }, [persistMode, selectedModel, configuredProviders, visibleOptions, saveModel]);

  return (
    <div
      className="relative flex w-full max-w-[calc(100vw-2rem)] min-w-0 flex-nowrap items-center gap-1 rounded-md border border-transparent bg-transparent p-0 shadow-none sm:w-auto sm:max-w-full"
      data-chat-model-picker="true"
    >
      <Dropdown
        label="Model"
        options={visibleOptions}
        value={selectedModel}
        onChange={(value) => void saveModel(value)}
        disabled={saving}
        menuPlacement={menuPlacement}
      />
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
