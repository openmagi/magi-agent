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
import { Layers, Plus, Trash2, Pencil, Check, Sparkles, Copy } from "lucide-react";

import { useAgentFetch } from "@/lib/local-api";
import {
  compileMode,
  deleteMode,
  getModes,
  putMode,
  setActiveMode,
  type AgentModeInput,
  type ModeCompileDraft,
} from "@/lib/agent-modes-api";
import { useCustomize, type ToolItem } from "@/lib/customize-api";
import { getDashboardChecks, type DashboardCheck } from "@/lib/packs-dashboard-api";
import { unifyPolicies } from "@/lib/policy-model";
import type { AgentMode } from "@/chat-core";
import { PageHint } from "./page-hint";
import { parseList, selectedScopedIds, slugifyModeId, toggleListItem, toggleScopedId } from "./modes-panel.helpers";

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
  /** "" = inherit the deployment posture (stored as null). */
  permissionMode: string;
}

function editorFromMode(mode: AgentMode): EditorState {
  return {
    modeId: mode.id,
    displayName: mode.displayName,
    systemPrompt: mode.systemPrompt,
    exclude: mode.toolDelta.exclude.join("\n"),
    include: mode.toolDelta.include.join("\n"),
    scopedPolicyIds: mode.scopedPolicyIds.join("\n"),
    permissionMode: mode.permissionMode ?? "",
  };
}

const EMPTY_EDITOR: EditorState = {
  modeId: null,
  displayName: "",
  systemPrompt: "",
  exclude: "",
  include: "",
  scopedPolicyIds: "",
  permissionMode: "",
};

/** PR-U3.4: drop an NL-compiled draft into the editable editor (create path).
 * The compiler never returns an id; the user reviews + names, then saves. */
function editorFromDraft(draft: ModeCompileDraft): EditorState {
  return {
    modeId: null,
    displayName: draft.displayName,
    systemPrompt: draft.systemPrompt,
    exclude: draft.toolDelta.exclude.join("\n"),
    include: draft.toolDelta.include.join("\n"),
    scopedPolicyIds: draft.scopedPolicyIds.join("\n"),
    permissionMode: draft.permissionMode ?? "",
  };
}

export function ModesPanel({ botId }: { botId: string }): React.JSX.Element {
  const agentFetch = useAgentFetch();
  const [modes, setModes] = useState<AgentMode[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorState | null>(null);
  // PR-U3.4: NL "describe a mode" compose surface + the warnings the compiler
  // surfaced (dropped tools/ids, capped permission mode) for the current draft.
  const [composing, setComposing] = useState(false);
  const [draftWarnings, setDraftWarnings] = useState<string[]>([]);

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

  // PR-P1: the live tool catalog powers the mode tool picker (checkbox lists),
  // replacing the freeform "type tool names" textareas.
  const toolItems = useMemo<ToolItem[]>(
    () => customizeData?.catalog.tools ?? [],
    [customizeData],
  );

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
      permissionMode: editor.permissionMode || null,
    };
    setBusy(true);
    setError(null);
    putMode(agentFetch, modeId, input)
      .then((res) => {
        setModes(res.modes);
        setActive(res.activeMode);
        setEditor(null);
        setDraftWarnings([]);
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
          { text: <>Scope <strong>rules</strong> to fire only in this mode (additive)</> },
        ]}
        cannot={[
          { text: <>Loosen a global rule: scoping only ever tightens a turn</> },
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

      {editor !== null ? (
        <ModeEditor
          editor={editor}
          busy={busy}
          warnings={draftWarnings}
          tools={toolItems}
          policyOptions={policyOptions}
          policiesLoading={policiesLoading}
          onChange={setEditor}
          onSave={handleSave}
          onCancel={() => {
            setEditor(null);
            setDraftWarnings([]);
          }}
        />
      ) : composing ? (
        <ModeCompose
          agentFetch={agentFetch}
          scopablePolicyIds={policyOptions.map((o) => o.id)}
          onDrafted={(draft, warnings) => {
            setEditor(editorFromDraft(draft));
            setDraftWarnings(warnings);
            setComposing(false);
          }}
          onCancel={() => setComposing(false)}
        />
      ) : (
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setComposing(true)}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-primary/30 bg-white px-3 py-1.5 text-xs font-semibold text-primary shadow-sm hover:bg-primary/[0.04] disabled:opacity-50"
          >
            <Sparkles className="h-3.5 w-3.5" />
            Describe a mode
          </button>
          <button
            type="button"
            onClick={() => {
              setEditor({ ...EMPTY_EDITOR });
              setDraftWarnings([]);
            }}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            New mode
          </button>
        </div>
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
          {modes.map((mode) => {
            // PR-P5.1: built-in posture modes are read-only (backend rejects
            // edit/delete). Offer Clone instead so the user customizes a copy.
            const isBuiltin = mode.id.startsWith("builtin-");
            return (
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
                  {isBuiltin ? (
                    <span className="inline-flex items-center rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                      built-in
                    </span>
                  ) : null}
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
                  {mode.toolDelta.exclude.length} off · {mode.toolDelta.include.length} on ·{" "}
                  {mode.scopedPolicyIds.length} scoped {mode.scopedPolicyIds.length === 1 ? "rule" : "rules"}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {isBuiltin ? (
                  <button
                    type="button"
                    onClick={() => {
                      setEditor({
                        ...editorFromMode(mode),
                        modeId: null,
                        displayName: `${mode.displayName} (copy)`,
                      });
                      setDraftWarnings([]);
                    }}
                    disabled={busy}
                    aria-label={`Clone mode ${mode.displayName}`}
                    className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-secondary hover:bg-black/[0.04] hover:text-foreground disabled:opacity-50"
                  >
                    <Copy className="h-3.5 w-3.5" /> Clone
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setEditor(editorFromMode(mode));
                        setDraftWarnings([]);
                      }}
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
                  </>
                )}
              </div>
            </div>
            );
          })}
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
  warnings = [],
  tools,
  policyOptions,
  policiesLoading,
  onChange,
  onSave,
  onCancel,
}: {
  editor: EditorState;
  busy: boolean;
  warnings?: string[];
  tools: ToolItem[];
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

      {editor.modeId ? null : (
        <p className="text-xs leading-relaxed text-secondary">
          Describe the stance you want the agent to take. A mode bundles how it
          should behave, which tools are on or off, which rules apply, and how
          strict approvals are. You pick it per turn in the chat composer.
        </p>
      )}

      {warnings.length > 0 ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-800">
          <p className="font-semibold">We adjusted the draft:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <label className={labelCls} htmlFor="mode-display-name">
          Name this stance
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
          How the agent should behave{" "}
          <span className="normal-case text-secondary/60">(guidance, not a hard rule)</span>
        </label>
        <textarea
          id="mode-system-prompt"
          value={editor.systemPrompt}
          onChange={(e) => set("systemPrompt", e.target.value)}
          rows={4}
          placeholder="e.g. Act as a careful read-only reviewer. Explain findings and cite sources; do not modify files."
          className={`${inputCls} resize-y font-mono text-xs`}
        />
      </div>

      <div>
        <label className={labelCls} htmlFor="mode-permission">
          How strict are approvals?{" "}
          <span className="normal-case text-secondary/60">(a mode can only tighten, never loosen)</span>
        </label>
        <select
          id="mode-permission"
          value={editor.permissionMode}
          onChange={(e) => set("permissionMode", e.target.value)}
          className={`${inputCls} cursor-pointer`}
        >
          <option value="">Inherit (deployment default)</option>
          <option value="default">default (prompt for every mutation)</option>
          <option value="smartApprove">smartApprove (LLM judges each)</option>
          <option value="acceptEdits">acceptEdits (auto-allow edits)</option>
          <option value="bypassPermissions">bypassPermissions (auto-allow all)</option>
        </select>
        <p className="mt-1 text-[11px] leading-relaxed text-secondary/70">
          A mode may only make approvals more restrictive than the deployment
          baseline, never looser. Hard-safety denies always apply regardless.
        </p>
      </div>

      <ModeToolPicker
        tools={tools}
        exclude={editor.exclude}
        include={editor.include}
        onToggleExclude={(name) => set("exclude", toggleListItem(editor.exclude, name))}
        onToggleInclude={(name) => set("include", toggleListItem(editor.include, name))}
      />
      <details>
        <summary className="cursor-pointer text-[11px] text-secondary/70">
          Advanced: tools by name (one per line)
        </summary>
        <div className="mt-2 grid gap-4 sm:grid-cols-2">
          <div>
            <label className={labelCls} htmlFor="mode-exclude">
              Turn tools off
            </label>
            <textarea
              id="mode-exclude"
              value={editor.exclude}
              onChange={(e) => set("exclude", e.target.value)}
              rows={3}
              placeholder="One per line: turns a default-ON tool off in this mode"
              className={`${inputCls} resize-y font-mono text-xs`}
            />
          </div>
          <div>
            <label className={labelCls} htmlFor="mode-include">
              Turn extra tools on{" "}
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
      </details>

      <div>
        <label className={labelCls}>
          Rules active in this mode{" "}
          <span className="normal-case text-secondary/60">
            (additive: they fire on top of the global defaults, only while this mode is active)
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
            Loading rules…
          </p>
        ) : (
          <p className="mt-1 rounded-lg border border-dashed border-black/[0.10] bg-gray-50/60 px-3 py-2 text-xs text-secondary">
            No rules of your own yet. Create one under <strong>Rules</strong>,
            then scope it to this mode.
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

/**
 * ModeToolPicker (PR-P1): checkbox lists over the live tool catalog, replacing
 * the freeform "type tool names one per line" textareas.
 *
 *  - "Turn tools off": tools on by default (enabled) → checking one adds it to
 *    the mode's exclude list.
 *  - "Turn extra tools on": tools off by default AND not dangerous → checking
 *    one adds it to the include list. Dangerous tools are never offered here
 *    (hard-safety: a mode can never re-enable Bash/exec/net via include).
 *
 * Selection round-trips through the same newline exclude/include strings the
 * editor already stores; the Advanced textareas remain the escape hatch for a
 * name not in the catalog.
 */
function ModeToolPicker({
  tools,
  exclude,
  include,
  onToggleExclude,
  onToggleInclude,
}: {
  tools: ToolItem[];
  exclude: string;
  include: string;
  onToggleExclude: (name: string) => void;
  onToggleInclude: (name: string) => void;
}): React.ReactElement {
  const [query, setQuery] = useState("");
  const excluded = useMemo(() => new Set(parseList(exclude)), [exclude]);
  const included = useMemo(() => new Set(parseList(include)), [include]);
  const needle = query.trim().toLowerCase();
  const match = (t: ToolItem) =>
    !needle || t.name.toLowerCase().includes(needle);

  const onByDefault = tools.filter((t) => t.enabled && match(t));
  // Only safe, currently-off tools can be re-enabled by a mode.
  const offAndSafe = tools.filter((t) => !t.enabled && !t.dangerous && match(t));

  const labelCls =
    "block text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70";
  const listCls =
    "mt-1 max-h-44 space-y-1 overflow-y-auto rounded-lg border border-black/[0.08] bg-white p-2";
  const rowCls =
    "flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-black/[0.03]";
  const emptyCls =
    "mt-1 rounded-lg border border-dashed border-black/[0.10] bg-gray-50/60 px-3 py-2 text-xs text-secondary";

  return (
    <div className="space-y-3">
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Filter tools…"
        aria-label="Filter tools"
        className="w-full rounded-lg border border-black/[0.08] bg-white px-3 py-1.5 text-xs text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={labelCls}>Turn tools off</label>
          {onByDefault.length > 0 ? (
            <div className={listCls}>
              {onByDefault.map((t) => (
                <label key={t.name} className={rowCls} title={t.description}>
                  <input
                    type="checkbox"
                    checked={excluded.has(t.name)}
                    onChange={() => onToggleExclude(t.name)}
                    className="h-3.5 w-3.5 accent-primary"
                  />
                  <span className="truncate text-foreground">{t.name}</span>
                </label>
              ))}
            </div>
          ) : (
            <p className={emptyCls}>No matching default-on tools.</p>
          )}
        </div>
        <div>
          <label className={labelCls}>
            Turn extra tools on{" "}
            <span className="normal-case text-secondary/60">(safe, default-off only)</span>
          </label>
          {offAndSafe.length > 0 ? (
            <div className={listCls}>
              {offAndSafe.map((t) => (
                <label key={t.name} className={rowCls} title={t.description}>
                  <input
                    type="checkbox"
                    checked={included.has(t.name)}
                    onChange={() => onToggleInclude(t.name)}
                    className="h-3.5 w-3.5 accent-primary"
                  />
                  <span className="truncate text-foreground">{t.name}</span>
                </label>
              ))}
            </div>
          ) : (
            <p className={emptyCls}>No safe default-off tools to re-enable.</p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * ModeCompose (PR-U3.4): the "describe a mode" natural-language surface.
 *
 * The operator types the stance in plain words; the backend NL→mode compiler
 * drafts a full mode which we hand to :func:`ModeEditor` (via ``onDrafted``)
 * for review before saving. Nothing is activated here. When the compiler is
 * disabled on the deployment (flag-off / no model) the compile fails soft and
 * we point the operator at the manual "New mode" form.
 */
function ModeCompose({
  agentFetch,
  scopablePolicyIds,
  onDrafted,
  onCancel,
}: {
  agentFetch: (path: string, init?: RequestInit) => Promise<Response>;
  scopablePolicyIds: string[];
  onDrafted: (draft: ModeCompileDraft, warnings: string[]) => void;
  onCancel: () => void;
}): React.ReactElement {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const draft = useCallback(() => {
    const nlText = text.trim();
    if (!nlText) return;
    setBusy(true);
    setError(null);
    compileMode(agentFetch, { nlText, scopablePolicyIds })
      .then((res) => {
        if (res.ok) {
          onDrafted(res.draft, res.warnings);
          return;
        }
        setError(
          res.error === "nl-mode compiler disabled"
            ? "Natural-language drafting isn't enabled on this deployment. Use \"New mode\" to author by hand."
            : res.error === "compiler unavailable"
              ? "No model is configured for drafting. Use \"New mode\" to author by hand."
              : `Couldn't draft a mode: ${res.error}`,
        );
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Couldn't draft a mode"),
      )
      .finally(() => setBusy(false));
  }, [agentFetch, onDrafted, scopablePolicyIds, text]);

  return (
    <section className="space-y-3 rounded-xl border border-primary/20 bg-primary/[0.02] px-4 py-4">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-bold text-foreground">Describe a mode</h3>
      </div>
      <p className="text-xs leading-relaxed text-secondary">
        Say what stance you want in plain words. We draft a mode (behavior +
        tools + rules + approvals) for you to review and edit before saving.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder="e.g. A careful read-only reviewer that explains findings and cites sources, and never edits files."
        className="w-full resize-y rounded-lg border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
      />
      {error ? (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-800">
          {error}
        </p>
      ) : null}
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
          onClick={draft}
          disabled={busy || !text.trim()}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-primary/90 disabled:opacity-50"
        >
          <Sparkles className="h-3.5 w-3.5" />
          {busy ? "Drafting…" : "Draft mode"}
        </button>
      </div>
    </section>
  );
}
