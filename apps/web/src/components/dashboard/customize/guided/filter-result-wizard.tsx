"use client";

/**
 * Filter-tool-result Guided wizard — DashboardCheck kind (after-tool).
 *
 * 6-step flow that produces a DashboardCheck via the existing dashboard
 * pack PUT route. Self-host-only — the route fails with 410 when
 * MAGI_DASHBOARD_PACK_AUTHORING_ENABLED is off; the wizard surfaces
 * that error verbatim on Save.
 *
 * Steps:
 *   1. When?      (scope)
 *   2. Which tool? (free-text or chip — pulled from menu when available)
 *   3. Pattern    (literal / regex)
 *   4. Action     (block / audit)
 *   5. Name       (id + label)
 *   6. Review
 */

import React, { useEffect, useState } from "react";

import { useAgentFetch } from "@/lib/local-api";
import {
  getDashboardPacksMenu,
  putDashboardCheck,
  type DashboardCheck,
  type DashboardScope,
  type DashboardAction,
} from "@/lib/packs-dashboard-api";

import { RadioCard, WizardChrome } from "./wizard-chrome";


interface Draft {
  scope: DashboardScope;
  tool: string;
  pattern: string;
  isRegex: boolean;
  action: DashboardAction;
  id: string;
  label: string;
}


const EMPTY: Draft = {
  scope: "always",
  tool: "",
  pattern: "",
  isRegex: false,
  action: "block",
  id: "",
  label: "",
};


const TOTAL = 6;


export interface FilterResultWizardProps {
  onActivated: () => void;
  onPickDifferent: () => void;
  onCancel: () => void;
}


export function FilterResultWizard({
  onActivated,
  onPickDifferent,
  onCancel,
}: FilterResultWizardProps): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [tools, setTools] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDashboardPacksMenu(agentFetch)
      .then((resp) => {
        if (!cancelled) setTools(resp.tools);
      })
      .catch(() => {
        // Menu unavailable (flag off or older runtime); leave tools empty
        // — the user can still type a tool name by hand.
      });
    return () => {
      cancelled = true;
    };
  }, [agentFetch]);

  return (
    <WizardChrome
      step={step}
      total={TOTAL}
      onPickDifferent={onPickDifferent}
      onCancel={onCancel}
      onBack={() => setStep((s) => Math.max(s - 1, 0))}
      onNext={() => setStep((s) => Math.min(s + 1, TOTAL - 1))}
      onSave={async () => {
        setSaving(true);
        setSaveError(null);
        try {
          await putDashboardCheck(agentFetch, buildCheck(draft));
          onActivated();
        } catch (err) {
          setSaveError(err instanceof Error ? err.message : "Save failed");
        } finally {
          setSaving(false);
        }
      }}
      canAdvance={stepIsComplete(step, draft)}
      saving={saving}
      error={saveError}
    >
      {step === 0 ? <ScopeStep draft={draft} setDraft={setDraft} /> : null}
      {step === 1 ? <ToolStep draft={draft} setDraft={setDraft} tools={tools} /> : null}
      {step === 2 ? <PatternStep draft={draft} setDraft={setDraft} /> : null}
      {step === 3 ? <ActionStep draft={draft} setDraft={setDraft} /> : null}
      {step === 4 ? <NameStep draft={draft} setDraft={setDraft} /> : null}
      {step === 5 ? <ReviewStep draft={draft} /> : null}
    </WizardChrome>
  );
}


const SCOPE_OPTIONS: ReadonlyArray<{
  id: DashboardScope;
  label: string;
  description: string;
  recommended?: boolean;
}> = [
  { id: "always", label: "Every turn", description: "Inspect tool results regardless of turn scope.", recommended: true },
  { id: "coding", label: "Coding turns", description: "Only on coding turns." },
  { id: "research", label: "Research turns", description: "Only on research turns." },
  { id: "delivery", label: "Delivery turns", description: "Only on delivery turns." },
];


function ScopeStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">When should this check run?</h2>
      <p className="text-xs text-secondary">
        The check fires <strong>after</strong> the chosen tool returns and
        before the agent reads the result.
      </p>
      <div className="space-y-2">
        {SCOPE_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.scope === opt.id}
            onClick={() => setDraft((d) => ({ ...d, scope: opt.id }))}
            label={opt.label}
            description={opt.description}
            badge={opt.recommended ? "recommended" : undefined}
          />
        ))}
      </div>
    </div>
  );
}


function ToolStep({
  draft,
  setDraft,
  tools,
}: {
  draft: Draft;
  setDraft: React.Dispatch<React.SetStateAction<Draft>>;
  tools: string[];
}): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Which tool's result should be checked?</h2>
      <p className="text-xs text-secondary">
        Pick a tool from the runtime's catalog or type a name. Use{" "}
        <code>mcp_server_tool</code> for MCP tools.
      </p>
      <label className="block">
        <input
          type="text"
          value={draft.tool}
          onChange={(e) => setDraft((d) => ({ ...d, tool: e.target.value }))}
          placeholder="fetch_url"
          aria-label="Tool name"
          className="w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
      {tools.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {tools.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setDraft((d) => ({ ...d, tool: t }))}
              className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors ${
                draft.tool === t
                  ? "bg-primary text-white"
                  : "bg-black/[0.05] text-secondary hover:bg-primary/10 hover:text-primary"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}


function PatternStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">What pattern should fire the check?</h2>
      <p className="text-xs text-secondary">
        The check matches the pattern against the tool's result text. Use
        a regex when you need anchors / character classes; otherwise a
        literal substring match.
      </p>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Pattern
        </span>
        <input
          type="text"
          value={draft.pattern}
          onChange={(e) => setDraft((d) => ({ ...d, pattern: e.target.value }))}
          placeholder={draft.isRegex ? "AKIA[0-9A-Z]{16}" : "secret"}
          aria-label="Pattern"
          className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm font-mono text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
      <label className="flex items-center gap-2 text-xs text-secondary">
        <input
          type="checkbox"
          checked={draft.isRegex}
          onChange={(e) => setDraft((d) => ({ ...d, isRegex: e.target.checked }))}
          className="rounded border-black/[0.20] text-primary focus:ring-primary/30"
        />
        Treat pattern as a regular expression
      </label>
    </div>
  );
}


const ACTION_OPTIONS: ReadonlyArray<{
  id: DashboardAction;
  label: string;
  description: string;
  recommended?: boolean;
}> = [
  { id: "block", label: "Block", description: "Stop the final answer when the pattern matches — safest.", recommended: true },
  { id: "audit", label: "Audit only", description: "Record the match without blocking the answer." },
];


function ActionStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">What happens on a match?</h2>
      <p className="text-xs text-secondary">
        The check emits an evidence record; this picks whether that record
        also stops the final answer.
      </p>
      <div className="space-y-2">
        {ACTION_OPTIONS.map((opt) => (
          <RadioCard
            key={opt.id}
            checked={draft.action === opt.id}
            onClick={() => setDraft((d) => ({ ...d, action: opt.id }))}
            label={opt.label}
            description={opt.description}
            badge={opt.recommended ? "recommended" : undefined}
          />
        ))}
      </div>
    </div>
  );
}


function NameStep({
  draft,
  setDraft,
}: { draft: Draft; setDraft: React.Dispatch<React.SetStateAction<Draft>> }): React.ReactElement {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Name your check</h2>
      <p className="text-xs text-secondary">
        Shown in the policies list and audit logs.
      </p>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Check ID
        </span>
        <input
          type="text"
          value={draft.id}
          onChange={(e) => setDraft((d) => ({ ...d, id: e.target.value }))}
          placeholder="block-aws-key-leak"
          aria-label="Check ID"
          className="mt-1 w-full rounded-lg border border-primary/30 bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
        <p className="mt-1 text-[11px] text-secondary">
          Lowercase alphanumeric + dash / underscore, 1-63 chars, must
          start with an alphanumeric.
        </p>
      </label>
      <label className="block">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
          Label
        </span>
        <input
          type="text"
          value={draft.label}
          onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))}
          placeholder="Block AWS access keys in fetch results"
          aria-label="Check label"
          className="mt-1 w-full rounded-lg border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </label>
    </div>
  );
}


function ReviewStep({ draft }: { draft: Draft }): React.ReactElement {
  const flavor = draft.isRegex ? "matches regex" : "contains";
  const verb = draft.action === "block" ? "block the final answer" : "audit-log the turn";
  const whenClause = draft.scope === "always" ? "After any tool returns" : `On ${draft.scope} turns, after ${draft.tool} returns`;
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-bold text-foreground">Review</h2>
      <p className="text-xs text-secondary">
        Saving applies the check to the dashboard pack immediately.
      </p>
      <div className="rounded-xl border border-black/[0.06] bg-white p-4">
        <p className="text-sm font-semibold text-foreground">What this check does</p>
        <p className="mt-1 text-xs leading-relaxed text-foreground">
          {whenClause}, {verb} when the result {flavor} <code>{draft.pattern}</code>.
        </p>
        <hr className="my-3 border-black/[0.05]" />
        <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5 text-xs">
          <dt className="text-secondary">ID</dt>
          <dd className="font-mono text-foreground">{draft.id || "(unnamed)"}</dd>
          <dt className="text-secondary">Label</dt>
          <dd>{draft.label || "(no label)"}</dd>
          <dt className="text-secondary">When</dt>
          <dd>{draft.scope} · after-tool</dd>
          <dt className="text-secondary">Tool</dt>
          <dd className="font-mono text-foreground">{draft.tool}</dd>
          <dt className="text-secondary">Pattern</dt>
          <dd className="font-mono text-foreground">
            {draft.pattern} {draft.isRegex ? "(regex)" : "(literal)"}
          </dd>
          <dt className="text-secondary">Action</dt>
          <dd>{draft.action}</dd>
        </dl>
      </div>
    </div>
  );
}


function stepIsComplete(step: number, draft: Draft): boolean {
  if (step === 0) return !!draft.scope;
  if (step === 1) return draft.tool.trim().length > 0;
  if (step === 2) return draft.pattern.trim().length > 0;
  if (step === 3) return !!draft.action;
  if (step === 4) return /^[a-z0-9][a-z0-9_-]{0,62}$/.test(draft.id) && draft.label.trim().length > 0;
  return true;
}


function buildCheck(draft: Draft): DashboardCheck {
  return {
    id: draft.id,
    label: draft.label,
    scope: draft.scope,
    enabled: true,
    trigger: {
      tool: draft.tool.trim(),
      match: { pattern: draft.pattern.trim(), isRegex: draft.isRegex },
    },
    action: draft.action,
  };
}
