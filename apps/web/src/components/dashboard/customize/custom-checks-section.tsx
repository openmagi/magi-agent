"use client";

import { useCallback, useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { useAgentFetch } from "@/lib/local-api";
import { slugifyCheckId } from "./custom-checks-section.slug";
import { TrustBadge, type TrustClass } from "./trust-badge";
import {
  deleteDashboardCheck,
  getDashboardChecks,
  getDashboardPacksMenu,
  putDashboardCheck,
  type DashboardAction,
  type DashboardCheck,
  type DashboardScope,
} from "@/lib/packs-dashboard-api";

export { slugifyCheckId };

/**
 * Action → trust-class mapping for the customize check-row badge.
 *
 * The codebase ships `DashboardAction = "block" | "audit"` today; both are
 * deterministic (the producer emits an EvidenceRecord and the pre-final gate
 * decides — tool output is never mutated). The spec carves out a forward-only
 * "override" / strip action that would rewrite tool output before the model
 * sees it; that variant is the only hybrid path. Keep the carve-out wired so
 * the badge lights up automatically the moment such an action is authored.
 */
export function trustClassForCheckAction(
  action: DashboardAction | "override",
): TrustClass {
  if (action === "override") return "hybrid";
  return "deterministic";
}

export interface CustomChecksSectionProps {
  /** Surfaces a save/delete error to the parent modal. */
  onSaveError?: (message: string | null) => void;
  /** Disables row controls while a parent mutation is in flight. */
  busy?: boolean;
}

const SCOPES: DashboardScope[] = ["always", "coding", "research", "delivery"];
const FLAG_NAME = "MAGI_DASHBOARD_PACK_AUTHORING_ENABLED";

const inputCls =
  "mt-1 w-full rounded-lg border border-black/[0.12] bg-white px-2 py-1.5 text-sm";

/**
 * Dashboard-authored custom-checks builder.
 *
 * Structurally parallels `CustomRulesSection`: a state-driven list with an
 * add/edit form and per-row toggle/delete controls. Each check is an after-tool
 * match (tool + pattern, optionally regex via `trigger.match`) plus an action
 * (`block` requires the matched evidence be absent; `audit` only records it).
 *
 * Self-host only — when the runtime returns 410 the section renders a disabled
 * notice naming the env flag rather than a broken form.
 */
export function CustomChecksSection({
  onSaveError,
  busy = false,
}: CustomChecksSectionProps): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [checks, setChecks] = useState<DashboardCheck[]>([]);
  const [tools, setTools] = useState<string[]>([]);
  const [disabled, setDisabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Add-form controlled inputs.
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState<DashboardScope>("always");
  const [tool, setTool] = useState("");
  const [pattern, setPattern] = useState("");
  const [isRegex, setIsRegex] = useState(false);
  const [action, setAction] = useState<DashboardAction>("block");

  const reportError = useCallback(
    (message: string | null) => {
      onSaveError?.(message);
    },
    [onSaveError],
  );

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [checksRes, menuRes] = await Promise.all([
        getDashboardChecks(agentFetch),
        getDashboardPacksMenu(agentFetch),
      ]);
      setChecks(checksRes.checks);
      setTools(menuRes.tools);
      setTool((prev) => prev || menuRes.tools[0] || "");
      setDisabled(false);
    } catch {
      // 410 (flag OFF) or runtime down → render the honest disabled notice.
      setDisabled(true);
    } finally {
      setLoading(false);
    }
  }, [agentFetch]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const resetForm = (): void => {
    setLabel("");
    setScope("always");
    setTool(tools[0] ?? "");
    setPattern("");
    setIsRegex(false);
    setAction("block");
    setAdding(false);
  };

  const canAdd = !!label.trim() && !!tool.trim() && !!pattern.trim();

  const submit = async (): Promise<void> => {
    if (!canAdd) return;
    setSaving(true);
    reportError(null);
    const takenIds = new Set(checks.map((c) => c.id));
    const check: DashboardCheck = {
      id: slugifyCheckId(label, takenIds),
      label: label.trim(),
      scope,
      enabled: true,
      trigger: { tool: tool.trim(), match: { pattern: pattern.trim(), isRegex } },
      action,
    };
    try {
      const res = await putDashboardCheck(agentFetch, check);
      setChecks(res.checks);
      resetForm();
    } catch (err: unknown) {
      reportError(err instanceof Error ? err.message : "Failed to save check");
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (check: DashboardCheck): Promise<void> => {
    setSaving(true);
    reportError(null);
    try {
      const res = await putDashboardCheck(agentFetch, {
        ...check,
        enabled: !check.enabled,
      });
      setChecks(res.checks);
    } catch (err: unknown) {
      reportError(err instanceof Error ? err.message : "Failed to update check");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string): Promise<void> => {
    setSaving(true);
    reportError(null);
    try {
      const res = await deleteDashboardCheck(agentFetch, id);
      setChecks(res.checks);
    } catch (err: unknown) {
      reportError(err instanceof Error ? err.message : "Failed to delete check");
    } finally {
      setSaving(false);
    }
  };

  if (disabled) {
    return (
      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
          Custom Checks
        </h3>
        <div className="rounded-xl border border-black/[0.08] bg-gray-50/60 px-4 py-3 text-[11px] leading-relaxed text-secondary">
          Self-host only — requires <code className="font-mono">{FLAG_NAME}</code>.
        </div>
      </section>
    );
  }

  const rowBusy = busy || saving;

  return (
    <section>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
        Custom Checks
      </h3>
      <p className="mb-2 text-xs leading-relaxed text-secondary">
        Build an after-tool evidence check: match a tool result by substring or
        regex, then <span className="font-medium">block</span> the final answer
        (if the match must be absent) or only <span className="font-medium">audit</span>{" "}
        it. Self-host only — requires <code className="font-mono">{FLAG_NAME}</code>.
      </p>

      {checks.length > 0 ? (
        <div className="mb-2 space-y-2">
          {checks.map((check) => (
            <div
              key={check.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-black/[0.06] bg-[var(--glass-regular-bg)] backdrop-blur-xl px-4 py-2.5"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-medium text-foreground">{check.label}</p>
                  <TrustBadge trustClass={trustClassForCheckAction(check.action)} />
                </div>
                <p className="mt-0.5 text-[11px] text-secondary/80">
                  {check.scope} · {check.trigger.tool} · {check.action}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  role="switch"
                  aria-checked={check.enabled}
                  aria-label={`Toggle check ${check.label}`}
                  disabled={rowBusy}
                  onClick={() => void toggle(check)}
                  className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-40 ${
                    check.enabled ? "bg-primary" : "bg-black/15"
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ${
                      check.enabled ? "translate-x-6" : "translate-x-1"
                    }`}
                  />
                </button>
                <button
                  type="button"
                  disabled={rowBusy}
                  onClick={() => void remove(check.id)}
                  className="p-1 text-secondary transition-colors hover:text-red-600 disabled:opacity-40"
                  aria-label={`Delete check ${check.label}`}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {adding ? (
        <div className="space-y-2 rounded-xl border border-black/[0.08] bg-gray-50/60 p-3">
          <label className="block text-[11px] font-medium text-secondary">
            Label
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Block SSN leak from web_fetch"
              className={inputCls}
            />
          </label>

          <label className="block text-[11px] font-medium text-secondary">
            Tool name
            <select
              value={tool}
              onChange={(e) => setTool(e.target.value)}
              className={inputCls}
            >
              {tools.length === 0 ? <option value="">(no tools)</option> : null}
              {tools.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>

          <label className="block text-[11px] font-medium text-secondary">
            Match the tool result (trigger.match)
            <input
              value={pattern}
              onChange={(e) => setPattern(e.target.value)}
              placeholder="ssn:  or  \d{3}-\d{2}-\d{4}"
              className={inputCls}
            />
          </label>

          <label className="flex items-center gap-1.5 text-[11px] font-medium text-secondary">
            <input
              type="checkbox"
              checked={isRegex}
              onChange={(e) => setIsRegex(e.target.checked)}
            />
            isRegex (treat the pattern as a regular expression)
          </label>

          <label className="block text-[11px] font-medium text-secondary">
            When (scope)
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as DashboardScope)}
              className={inputCls}
            >
              {SCOPES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          <label className="block text-[11px] font-medium text-secondary">
            Action
            <select
              value={action}
              onChange={(e) => setAction(e.target.value as DashboardAction)}
              className={inputCls}
            >
              <option value="block">Block (require the match be absent)</option>
              <option value="audit">Audit (record only, never blocks)</option>
            </select>
          </label>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={resetForm}
              className="rounded-lg px-3 py-1.5 text-sm text-secondary hover:text-foreground"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={rowBusy || !canAdd}
              onClick={() => void submit()}
              className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            >
              {saving ? "Saving…" : "Add check"}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          disabled={loading}
          onClick={() => setAdding(true)}
          className="w-full rounded-xl border border-dashed border-black/[0.12] px-4 py-2.5 text-sm font-medium text-secondary transition-colors hover:border-primary/30 hover:text-foreground disabled:opacity-40"
        >
          + Add custom check
        </button>
      )}
    </section>
  );
}
