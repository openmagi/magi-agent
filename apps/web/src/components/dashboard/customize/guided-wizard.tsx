"use client";

/**
 * Guided policy wizard — PR-E5 thin shell over the unified AuthorWizard.
 *
 * Kevin's 2026-06-22 design call collapsed the four kind-specific
 * sub-wizards (PR-E2/E3) into one wizard with scope + lifecycle +
 * archetype as orthogonal axes. The router behavior is gone — Guided
 * mode click goes straight into the AuthorWizard.
 *
 * This shell stays so the Hub keeps the same import path; the parent
 * still owns activation + cancel + pick-different callbacks. ``Pick
 * different`` reuses the parent's mode-picker route (back to NL / Guided
 * / Raw selector) since there is no longer a sibling sub-wizard to
 * navigate to within Guided.
 */

import React from "react";

import type { CustomizeCatalog } from "@/lib/customize-api";
import type { EvidenceTypeEntry } from "@/lib/policy-model";

import { AuthorWizard } from "./guided/author-wizard";


export interface GuidedWizardProps {
  catalog: CustomizeCatalog;
  evidenceTypes: EvidenceTypeEntry[];
  onActivated: () => void;
  onPickDifferent: () => void;
  onCancel: () => void;
}


export function GuidedWizard({
  catalog,
  evidenceTypes,
  onActivated,
  onPickDifferent,
  onCancel,
}: GuidedWizardProps): React.ReactElement {
  return (
    <AuthorWizard
      catalog={catalog}
      evidenceTypes={evidenceTypes}
      onActivated={onActivated}
      // The unified wizard treats "Cancel" and "Pick different mode" as
      // the same affordance — there is no longer an intermediate
      // kind-picker to step back to.
      onCancel={onPickDifferent}
    />
  );
}
