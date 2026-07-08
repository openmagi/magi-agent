"use client";

import { Modal } from "@/components/ui/modal";
import { Switch } from "@/components/ui/_ds";
import type { ToolItem } from "@/lib/customize-api";

interface CustomToolModalProps {
  open: boolean;
  onClose: () => void;
  tools: ToolItem[];
  overrides: Record<string, boolean>;
  onToggle: (name: string, enabled: boolean) => void;
  /** Names of tools whose PATCH request is currently in-flight. */
  pendingNames?: Set<string>;
  /** Transient error from the most recent failed PATCH, cleared on next toggle. */
  error?: string | null;
}

const SOURCE_BADGE: Record<string, string> = {
  builtin: "bg-black/5 text-secondary",
  skill: "bg-primary/10 text-primary",
  external: "bg-cta/10 text-cta",
};

export type CustomToolPanelProps = Omit<CustomToolModalProps, "open" | "onClose">;

/** Headless tool panel — shared between the modal and the Phase-4 hub page. */
export function CustomToolPanel({
  tools,
  overrides,
  onToggle,
  pendingNames,
  error,
}: CustomToolPanelProps): React.ReactElement {
  return (
    <>
      {error ? (
        <div className="mb-4 rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-3 text-xs leading-5 text-amber-800">
          {error}
        </div>
      ) : null}

      {tools.length === 0 ? (
        <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-8 text-center text-sm leading-6 text-secondary">
          No tools reported by the local runtime.
        </div>
      ) : (
        <div className="space-y-2">
          {tools.map((tool) => {
            const enabled = overrides[tool.name] ?? tool.enabled;
            const isPending = pendingNames?.has(tool.name) ?? false;
            return (
              <div
                key={tool.name}
                className="flex items-start justify-between gap-4 rounded-xl border border-black/[0.06] bg-[var(--color-surface-raised)] px-4 py-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-semibold text-foreground">{tool.name}</p>
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${
                        SOURCE_BADGE[tool.source] ?? SOURCE_BADGE.builtin
                      }`}
                    >
                      {tool.source}
                    </span>
                    {tool.dangerous ? (
                      <span className="inline-flex items-center rounded-full bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-500">
                        dangerous
                      </span>
                    ) : null}
                  </div>
                  {tool.description ? (
                    <p className="mt-1 text-xs leading-relaxed text-secondary">{tool.description}</p>
                  ) : null}
                </div>
                <Switch
                  checked={enabled}
                  onToggle={async (next) => onToggle(tool.name, next)}
                  labelOn={`Disable ${tool.name}`}
                  labelOff={`Enable ${tool.name}`}
                  disabled={isPending}
                />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}


export function CustomToolModal({
  open,
  onClose,
  ...panelProps
}: CustomToolModalProps): React.ReactElement | null {
  if (!open) return null;

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6">
        <div className="mb-1 flex items-start justify-between">
          <h2 className="text-lg font-semibold text-foreground">Custom Tools</h2>
          <button
            type="button"
            onClick={onClose}
            className="-mr-1 -mt-1 p-1 text-secondary transition-colors hover:text-foreground"
            aria-label="Close"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <p className="mb-4 text-xs text-secondary">
          Enable or disable the tools your agent can call. Changes are saved and take effect
          immediately.
        </p>

        <CustomToolPanel {...panelProps} />
      </div>
    </Modal>
  );
}
