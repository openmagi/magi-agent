"use client";

/**
 * Guidance inner-tab body — the Freeform-Guidance / USER-RULES.md textarea.
 *
 * Separated from the Presets and Gates panels so the reliability boundary
 * is honest:
 *
 *  * Presets / Gates change runtime decisions deterministically — the model
 *    cannot opt out of a triggered rule.
 *  * Guidance is **prompt text** injected every turn. The model is asked
 *    to follow it but is free to ignore it. Useful for style and soft
 *    preferences, not for safety-critical constraints.
 *
 * The header copy spells this out so a user who wants hard enforcement
 * navigates to the right tab instead of stuffing safety rules into a
 * textarea and hoping for the best.
 */

import React, { useEffect, useState } from "react";


export interface GuidancePanelProps {
  userRules: string;
  rulesSaving: boolean;
  onSaveRules: (text: string) => void;
}


export function GuidancePanel({
  userRules,
  rulesSaving,
  onSaveRules,
}: GuidancePanelProps): React.ReactElement {
  const [draft, setDraft] = useState(userRules);
  useEffect(() => setDraft(userRules), [userRules]);
  const dirty = draft !== userRules;

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-amber-500/30 bg-amber-50/60 px-4 py-3 text-xs leading-relaxed text-amber-900">
        <strong>Soft instructions.</strong> Free-text injected into the system
        prompt every turn. The model is asked to follow them but is not
        deterministically forced to — for hard enforcement, use Presets or
        Gates instead.
      </div>
      <textarea
        aria-label="Freeform guidance"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={6}
        placeholder="e.g. Always cite sources. Never delete files without confirming."
        className="w-full resize-y rounded-xl border border-black/[0.10] bg-white px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
      />
      <div className="flex justify-end">
        <button
          type="button"
          disabled={!dirty || rulesSaving}
          onClick={() => onSaveRules(draft)}
          className="inline-flex min-h-[36px] items-center rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {rulesSaving ? "Saving…" : dirty ? "Save guidance" : "Saved"}
        </button>
      </div>
    </div>
  );
}
