"use client";

import { Modal } from "@/components/ui/modal";
import type { ToolItem } from "@/lib/customize-api";

interface CustomToolModalProps {
  open: boolean;
  onClose: () => void;
  tools: ToolItem[];
  overrides: Record<string, boolean>;
  onToggle: (name: string, enabled: boolean) => void;
}

const SOURCE_BADGE: Record<string, string> = {
  builtin: "bg-black/5 text-secondary",
  skill: "bg-primary/10 text-primary",
  external: "bg-cta/10 text-cta",
};

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/45 focus-visible:ring-offset-2 ${
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

export function CustomToolModal({
  open,
  onClose,
  tools,
  overrides,
  onToggle,
}: CustomToolModalProps): React.ReactElement | null {
  if (!open) return null;

  return (
    <Modal open={open} onClose={onClose}>
      <div className="p-6">
        {/* Header */}
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
        <p className="mb-6 text-xs text-secondary">
          Enable or disable the tools your agent can call. Changes apply to this local session only.
        </p>

        {/* Tool list */}
        {tools.length === 0 ? (
          <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-8 text-center text-sm leading-6 text-secondary">
            No tools reported by the local runtime.
          </div>
        ) : (
          <div className="space-y-2">
            {tools.map((tool) => {
              const enabled = overrides[tool.name] ?? tool.enabled;
              return (
                <div
                  key={tool.name}
                  className="flex items-start justify-between gap-4 rounded-xl border border-black/[0.06] bg-white px-4 py-3"
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
                  <Toggle
                    checked={enabled}
                    onChange={(next) => onToggle(tool.name, next)}
                    label={`Toggle ${tool.name}`}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
}
