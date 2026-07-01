"use client";

/**
 * Modes panel: CRUD over agent modes (postures).
 *
 * A *mode* is an explicit, user-selected posture: a soft system prompt + a tool
 * allow/deny DELTA from the bot default + the ids of scoped policies active in
 * that mode. The composer's mode selector picks which mode to send per turn;
 * this panel is where the user authors them.
 *
 * All fields are enforced at runtime: `systemPrompt` is injected, `toolDelta`
 * narrows (exclude) / widens within a hard-safety cap (include), and
 * `scopedPolicyIds` force-activates user-authored policies (custom rules +
 * dashboard checks) only while the mode is active. The scoped-policy picker
 * below sources its options from the unified policy index.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Layers, Plus, Trash2, Pencil, Check } from "lucide-react";

import { useAgentFetch } from "@/lib/local-api";
import {
  deleteMode,
  getModes,
  putMode,
  setActiveMode,
  type AgentModeInput,
} from "@/lib/agent-modes-api";
import { useCustomize } from "@/lib/customize-api";
import { getDashboardChecks, type DashboardCheck } from "@/lib/packs-dashboard-api";
import { unifyPolicies } from "@/lib/policy-model";
import type { AgentMode } from "@/chat-core";
import { PageHint } from "./page-hint";
import { parseList, selectedScopedIds, slugifyModeId, toggleScopedId } from "./modes-panel.helpers";

/** A user-authored policy the Modes editor can scope (custom rule or dashboard
 * check), keyed by the resolver's prefixed id (`custom_rule:` / `dashboard_check:`). */
export interface ScopablePolicyOption {
  id: string;
  name: string;
  kind: "custom_rule" | "dashboard_check";
}

interface EditorState {
  /** Existing mode id when editing; null when creating a new mode. */
  modeId: string | null;
  displayName: string;
  systemPrompt: string;
  exclude: string;
  include: string;
  scopedPolicyIds: string;
}

function editorFromMode(mode: AgentMode): EditorState {
  return {
    modeId: mode.id,
    displayName: mode.displayName,
    systemPrompt: mode.systemPrompt,
    exclude: mode.toolDelta.exclude.join("\n"),
    include: mode.toolDelta.include.join("\n"),
    scopedPolicyIds: mode.scopedPolicyIds.join("\n"),
  };
}

const EMPTY_EDITOR: EditorState = {
  modeId: null,
  displayName: "",
  systemPrompt: "",
  exclude: "",
  include: "",
  scopedPolicyIds: "",
};

export function ModesPanel({ botId }: { botId: string }): React.JSX.Element {
  const agentFetch = useAgentFetch();
  const [modes, setModes] = useState<AgentMode[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState | null>(null);

  // Scopable-policy picker source: the user-authored custom rules + dashboard
  // checks, keyed by the resolver's prefixed id. Reuses the dashboard's unified
  // policy index so the ids match exactly what the backend resolver expects.
  const { data: customizeData, loading: customizeLoading } = useCustomize();
  const [dashboardChecks, setDashboardChecks] = useState<DashboardCheck[]>([]);
  const [checksLoading, setChecksLoading] = useState(true);
  useEffect(() => {
    getDashboardChecks(agentFetch)
      .then((resp) => setDashboardChecks(resp.checks))
      .catch(() => setDashboardChecks([])) // 410 (flag-off) → silently empty
      .finally(() => setChecksLoading(false));
  }, [agentFetch]);
  const policiesLoading = customizeLoading || checksLoading;
  const policyOptions = useMemo<ScopablePolicyOption[]>(() => {
    if (!customizeData) return [];
    return unifyPolicies({
      catalog: customizeData.catalog,
      overrides: customizeData.overrides,
      dashboardChecks,
    })
      .filter(
        (p) => p.rawSource.kind === "custom_rule" || p.rawSource.kind === "dashboard_check",
      )
      .map((p) => ({
        id: p.id,
        name: p.name,
        kind: p.rawSource.kind as "custom_rule" | "dashboard_check",
      }));
  }, [customizeData, dashboardChecks]);

  const load = useCallback(() => {
    setLoading(true);
    getModes(agentFetch)
      .then((data) => {
        setModes(data.modes);
        setActive(data.activeMode);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Failed to load modes"),
      )
      .finally(() => setLoading(false));
  }, [agentFetch]);

  useEffect(() => {
    load();
  }, [load, botId]);

  const handleSetActive = useCallback(
    (modeId: string | null) => {
      // Optimistic: snap the selection, reconcile from the response.
      const prev = active;
      setActive(modeId);
      setError(null);
      setBusy(true);
      setActiveMode(agentFetch, modeId)
        .then((res) => setActive(res.activeMode))
        .catch((err: unknown) => {
          setActive(prev);
          setError(err instanceof Error ? err.message : "Failed to set active mode");
        })
        .finally(() => setBusy(false));
    },
    [agentFetch, active],
  );

  const handleSave = useCallback(() => {
    if (!editor) return;
    const displayName = editor.displayName.trim();
    if (!displayName) {
      setError("Display name is required.");
      return;
    }
    // On create, disambiguate against existing ids so a slug collision (two
    // display names that reduce to the same slug) never silently overwrites an
    // existing mode via the id-keyed upsert. On edit the id is fixed.
    const modeId =
      editor.modeId ?? slugifyModeId(displayName, new Set(modes.map((m) => m.id)));
    const input: AgentModeInput = {
      displayName,
      systemPrompt: editor.systemPrompt,
      toolDelta: {
        exclude: parseList(editor.exclude),
        include: parseList(editor.include),
      },
      scopedPolicyIds: parseList(editor.scopedPolicyIds),
    };
    setBusy(true);
    setError(null);
    putMode(agentFetch, modeId, input)
      .then((res) => {
        setModes(res.modes);
        setActive(res.activeMode);
        setEditor(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Failed to save mode"),
      )
      .finally(() => setBusy(false));
  }, [agentFetch, editor, modes]);

  const handleDelete = useCallback(
    (modeId: string) => {
      setBusy(true);
      setError(null);
      deleteMode(agentFetch, modeId)
        .then((res) => {
          setModes(res.modes);
          setActive(res.activeMode);
          setEditor((cur) => (cur?.modeId === modeId ? null : cur));
        })
        .catch((err: unknown) =>
          setError(err instanceof Error ? err.message : "Failed to delete mode"),
        )
        .finally(() => setBusy(false));
    },
    [agentFetch],
  );

  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-secondary">
        Loading modes…
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHint
        title="Modes: saved agent postures"
        can={[
          { text: <>A soft <strong>system prompt</strong> the model follows this turn</> },
          { text: <>A <strong>tool delta</strong>: narrow (exclude) or safely widen (include)</> },
          { text: <>Scope <strong>policies</strong> to fire only in this mode (additive)</> },
        ]}
        cannot={[
          { text: <>Loosen a global policy: scoping only ever tightens a turn</> },
          { text: <>Re-enable a dangerous tool (Bash/exec/net) via <code>include</code></> },
        ]}
        note={
          <>
            Pick a mode in the chat composer to apply it per turn. The active mode
            below is the sticky default the composer starts on.
          </>
        }
      />

      {error ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-600">
          {error}
        </div>
      ) : null}

      {editor === null ? (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => setEditor({ ...EMPTY_EDITOR })}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            New mode
          </button>
        </div>
      ) : (
        <ModeEditor
          editor={editor}
          busy={busy}
          policyOptions={policyOptions}
          policiesLoading={policiesLoading}
          onChange={setEditor}
          onSave={handleSave}
          onCancel={() => setEditor(null)}
        />
      )}

      {/* Active-mode selector */}
      <div className="rounded-xl border border-black/[0.06] bg-white px-4 py-3">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Active mode
        </p>
        <div className="flex flex-wrap gap-2">
          <ActivePill
            label="Default"
            selected={active === null}
            disabled={busy}
            onClick={() => handleSetActive(null)}
          />
          {modes.map((mode) => (
            <ActivePill
              key={mode.id}
              label={mode.displayName}
              selected={active === mode.id}
              disabled={busy}
              onClick={() => handleSetActive(mode.id)}
            />
          ))}
        </div>
      </div>

      {/* Mode list */}
      {modes.length === 0 ? (
        <div className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-8 text-center text-sm leading-6 text-secondary">
          No modes yet. Create one to give the agent a reusable posture.
        </div>
      ) : (
        <div className="space-y-2">
          {modes.map((mode) => (
            <div
              key={mode.id}
              className="flex items-start justify-between gap-4 rounded-xl border border-black/[0.06] bg-white px-4 py-3"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="truncate text-sm font-semibold text-foreground">{mode.displayName}</p>
                  <span className="inline-flex items-center rounded-full bg-black/5 px-2 py-0.5 font-mono text-[11px] text-secondary">
                    {mode.id}
                  </span>
                  {active === mode.id ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                      <Check className="h-3 w-3" /> active
                    </span>
                  ) : null}
                </div>
                {mode.systemPrompt ? (
                  <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-secondary">
                    {mode.systemPrompt}
                  </p>
                ) : null}
                <p className="mt-1 text-[11px] leading-relaxed text-secondary/80">
                  {mode.toolDelta.exclude.length} excluded · {mode.toolDelta.include.length} included ·{" "}
                  {mode.scopedPolicyIds.length} scoped {mode.scopedPolicyIds.length === 1 ? "policy" : "policies"}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => setEditor(editorFromMode(mode))}
                  disabled={busy}
                  aria-label={`Edit mode ${mode.displayName}`}
                  className="rounded-lg p-1.5 text-secondary hover:bg-black/[0.04] hover:text-foreground disabled:opacity-50"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => handleDelete(mode.id)}
                  disabled={busy}
                  aria-label={`Delete mode ${mode.displayName}`}
                  className="rounded-lg p-1.5 text-secondary hover:bg-red-500/[0.08] hover:text-red-600 disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ActivePill({
  label,
  selected,
  disabled,
  onClick,
}: {
  label: string;
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={selected}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${
        selected
          ? "border-primary bg-primary/10 text-primary"
          : "border-black/[0.08] text-secondary hover:bg-black/[0.04] hover:text-foreground"
      }`}
    >
      {selected ? <Check className="h-3 w-3" /> : null}
      {label}
    </button>
  );
}

function ModeEditor({
  editor,
  busy,
  policyOptions,
  policiesLoading,
  onChange,
  onSave,
  onCancel,
}: {
  editor: EditorState;
  busy: boolean;
  policyOptions: ScopablePolicyOption[];
  policiesLoading: boolean;
  onChange: (next: EditorState) => void;
  onSave: () => void;
  onCancel: () => void;
}): React.ReactElement {
  const set = <K extends keyof EditorState>(key: K, value: EditorState[K]) =>
    onChange({ ...editor, [key]: value });
  // Scoped-policy picker: the editor keeps scopedPolicyIds as a newline string
  // (the wire shape); the checkboxes toggle prefixed ids in/out of it, and the
  // advanced textarea stays the escape hatch for ids not in the known list.
  const scopedSelected = selectedScopedIds(editor.scopedPolicyIds);
  const toggleScoped = (id: string) =>
    set("scopedPolicyIds", toggleScopedId(editor.scopedPolicyIds, id));
  const labelCls = "block text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70";
  const inputCls =
    "mt-1 w-full rounded-lg border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50";
  return (
    <section className="space-y-4 rounded-xl border border-primary/20 bg-primary/[0.02] px-4 py-4">
      <div className="flex items-center gap-2">
        <Layers className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-bold text-foreground">
          {editor.modeId ? `Edit "${editor.displayName || editor.modeId}"` : "New mode"}
        </h3>
      </div>

      <div>
        <label className={labelCls} htmlFor="mode-display-name">
          Display name
        </label>
        <input
          id="mode-display-name"
          type="text"
          value={editor.displayName}
          onChange={(e) => set("displayName", e.target.value)}
          placeholder="e.g. Careful coding"
          className={inputCls}
        />
        {editor.modeId ? (
          <p className="mt-1 text-[11px] text-secondary/70">
            id: <code>{editor.modeId}</code> (fixed)
          </p>
        ) : null}
      </div>

      <div>
        <label className={labelCls} htmlFor="mode-system-prompt">
          System prompt (soft)
        </label>
        <textarea
          id="mode-system-prompt"
          value={editor.systemPrompt}
          onChange={(e) => set("systemPrompt", e.target.value)}
          rows={4}
          placeholder="Injected into the system prompt this turn. The model is asked to follow it."
          className={`${inputCls} resize-y font-mono text-xs`}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={labelCls} htmlFor="mode-exclude">
            Exclude tools
          </label>
          <textarea
            id="mode-exclude"
            value={editor.exclude}
            onChange={(e) => set("exclude", e.target.value)}
            rows={3}
            placeholder="One per line: turns a default-ON tool off"
            className={`${inputCls} resize-y font-mono text-xs`}
          />
        </div>
        <div>
          <label className={labelCls} htmlFor="mode-include">
            Include tools{" "}
            <span className="normal-case text-secondary/60">(re-enable a default-off tool)</span>
          </label>
          <textarea
            id="mode-include"
            value={editor.include}
            onChange={(e) => set("include", e.target.value)}
            rows={3}
            placeholder="One per line: re-enables a default-off tool (safe tools only; never Bash/exec/net)"
            className={`${inputCls} resize-y font-mono text-xs`}
          />
        </div>
      </div>

      <div>
        <label className={labelCls}>
          Scoped policies{" "}
          <span className="normal-case text-secondary/60">
            (active only in this mode: additive, tightens the turn)
          </span>
        </label>
        {policyOptions.length > 0 ? (
          <div className="mt-1 max-h-44 space-y-1 overflow-y-auto rounded-lg border border-black/[0.08] bg-white p-2">
            {policyOptions.map((option) => (
              <label
                key={option.id}
                className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-black/[0.03]"
              >
                <input
                  type="checkbox"
                  checked={scopedSelected.has(option.id)}
                  onChange={() => toggleScoped(option.id)}
                  className="h-3.5 w-3.5 accent-primary"
                />
                <span className="truncate text-foreground">{option.name}</span>
                <span className="ml-auto shrink-0 rounded-full bg-black/5 px-1.5 py-0.5 text-[10px] text-secondary">
                  {option.kind === "custom_rule" ? "rule" : "check"}
                </span>
              </label>
            ))}
          </div>
        ) : policiesLoading ? (
          <p className="mt-1 rounded-lg border border-dashed border-black/[0.10] bg-gray-50/60 px-3 py-2 text-xs text-secondary">
            Loading policies…
          </p>
        ) : (
          <p className="mt-1 rounded-lg border border-dashed border-black/[0.10] bg-gray-50/60 px-3 py-2 text-xs text-secondary">
            No user-authored policies yet. Create one under{" "}
            <strong>Policies</strong>, then scope it to this mode.
          </p>
        )}
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-secondary/70">
            Advanced: raw ids
          </summary>
          <textarea
            id="mode-policies"
            value={editor.scopedPolicyIds}
            onChange={(e) => set("scopedPolicyIds", e.target.value)}
            rows={2}
            placeholder="One prefixed id per line, e.g. custom_rule:cr_… / dashboard_check:…"
            className={`${inputCls} resize-y font-mono text-xs`}
          />
        </details>
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-secondary hover:bg-black/[0.04] disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={busy || !editor.displayName.trim()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:opacity-50"
        >
          <Check className="h-3.5 w-3.5" />
          Save mode
        </button>
      </div>
    </section>
  );
}
