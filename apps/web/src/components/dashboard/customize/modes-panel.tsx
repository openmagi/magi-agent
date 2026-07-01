"use client";

/**
 * Modes panel — CRUD over agent modes (postures).
 *
 * A *mode* is an explicit, user-selected posture: a soft system prompt + a tool
 * allow/deny DELTA from the bot default + the ids of scoped policies active in
 * that mode. The composer's mode selector picks which mode to send per turn;
 * this panel is where the user authors them.
 *
 * Storage-only fields today (surfaced, but the runtime does not yet apply them):
 * `toolDelta.include` (re-enabling a default-off tool needs the universal
 * hard-safety cap) and `scopedPolicyIds` (needs the policy resolver). The
 * editor labels those as "not yet enforced" so the operator is not misled.
 */

import React, { useCallback, useEffect, useState } from "react";
import { Layers, Plus, Trash2, Pencil, Check } from "lucide-react";

import { useAgentFetch } from "@/lib/local-api";
import {
  deleteMode,
  getModes,
  putMode,
  setActiveMode,
  type AgentModeInput,
} from "@/lib/agent-modes-api";
import type { AgentMode } from "@/chat-core";
import { PageHint } from "./page-hint";
import { parseList, slugifyModeId } from "./modes-panel.helpers";

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
      // Optimistic — snap the selection, reconcile from the response.
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
        title="Modes — saved agent postures"
        can={[
          { text: <>A soft <strong>system prompt</strong> the model follows this turn</> },
          { text: <>A <strong>tool delta</strong> — turn default tools off for this posture</> },
        ]}
        cannot={[
          { text: <>Hard enforcement → use <strong>Policies</strong></> },
          { text: <>Re-enabling default-off tools (<code>include</code>) is not yet applied</> },
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
  onChange,
  onSave,
  onCancel,
}: {
  editor: EditorState;
  busy: boolean;
  onChange: (next: EditorState) => void;
  onSave: () => void;
  onCancel: () => void;
}): React.ReactElement {
  const set = <K extends keyof EditorState>(key: K, value: EditorState[K]) =>
    onChange({ ...editor, [key]: value });
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
            placeholder="One per line — turns a default-ON tool off"
            className={`${inputCls} resize-y font-mono text-xs`}
          />
        </div>
        <div>
          <label className={labelCls} htmlFor="mode-include">
            Include tools <span className="normal-case text-secondary/60">(not yet enforced)</span>
          </label>
          <textarea
            id="mode-include"
            value={editor.include}
            onChange={(e) => set("include", e.target.value)}
            rows={3}
            placeholder="Stored, but re-enabling default-off tools is not applied yet"
            className={`${inputCls} resize-y font-mono text-xs`}
          />
        </div>
      </div>

      <div>
        <label className={labelCls} htmlFor="mode-policies">
          Scoped policy ids <span className="normal-case text-secondary/60">(not yet enforced)</span>
        </label>
        <textarea
          id="mode-policies"
          value={editor.scopedPolicyIds}
          onChange={(e) => set("scopedPolicyIds", e.target.value)}
          rows={2}
          placeholder="Policy ids active only in this mode. Stored; resolver wiring pending."
          className={`${inputCls} resize-y font-mono text-xs`}
        />
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
