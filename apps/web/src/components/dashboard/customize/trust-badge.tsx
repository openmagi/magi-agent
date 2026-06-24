/**
 * Shared trust-class badge for the Customize surface.
 *
 * Encodes the honesty taxonomy that the rest of the customize tab speaks:
 *
 *  * deterministic — runtime gate; the model cannot opt out
 *  * advisory      — prompt-injected guidance; the model may ignore it
 *  * hybrid        — a check that both records evidence AND may rewrite the
 *                    tool output (e.g. dashboard_check action="override" /
 *                    strip). Currently no built-in action takes this branch,
 *                    but the badge is forward-compatible so adding such an
 *                    action later lights it up automatically.
 *  * preview       — visible but inert; not feeding the runtime
 *
 * The component intentionally lives under `customize/` (not the generic
 * `ui/_ds/Badge`) because the trust-class semantics belong to this domain,
 * not to the design-system token sheet. Visually it converges on the F1
 * inline Advisory pill (amber, uppercase, rounded-full) so the swap is
 * byte-equivalent for the advisory variant.
 */

import type { ReactElement } from "react";

import {
  trustClassForPolicy as policyTrustClassForPolicy,
  type PolicyTrustInput,
  type TrustClass as PolicyTrustClass,
} from "../../../lib/policy-model";

import { cn } from "../../ui/_ds/cn";

/**
 * Trust-class taxonomy — re-exported from :mod:`@/lib/policy-model` so the
 * customize surface has exactly one definition. The local alias preserves
 * the historical ``import { type TrustClass } from "./trust-badge"`` paths
 * (used by ``nl-rule-compose.tsx`` / ``custom-checks-section.tsx`` / the
 * guided author wizard) without duplicating the union literal.
 *
 * Previously a second copy lived here AND a richer copy lived in
 * ``policy-model.ts``. The two had different signatures and different
 * mapping tables; ``rules-table.tsx`` imported the simpler one, silently
 * losing the ``action === "override" → "hybrid"`` distinguisher that PR-F5
 * shipped in ``policy-model.ts``. Collapsing to a single source guarantees
 * future contributors can't pick the wrong helper.
 */
export type TrustClass = PolicyTrustClass;

export interface TrustBadgeProps {
  /** Honesty taxonomy bucket — drives palette + default label + aria-label. */
  trustClass: TrustClass;
  /** Override the visible text. Defaults to the capitalized trust class. */
  label?: string;
  /** Override the aria-label. Defaults to ``Trust class: <Class>``. Callers
   *  that render many pills in one surface may pass a more specific
   *  description (e.g. "Trust class for this policy") so screen-reader
   *  users get unambiguous context. */
  ariaLabel?: string;
  /** Caller-supplied utility classes appended after the variant palette. */
  className?: string;
}

const PALETTE: Record<TrustClass, string> = {
  deterministic: "bg-emerald-500/10 text-emerald-700",
  advisory: "bg-amber-500/10 text-amber-700",
  hybrid: "bg-violet-500/10 text-violet-700",
  preview: "bg-blue-500/10 text-blue-700",
};

const DEFAULT_LABEL: Record<TrustClass, string> = {
  deterministic: "Deterministic",
  advisory: "Advisory",
  hybrid: "Hybrid",
  preview: "Preview",
};

export function TrustBadge({
  trustClass,
  label,
  ariaLabel,
  className,
}: TrustBadgeProps): ReactElement {
  const text = label ?? DEFAULT_LABEL[trustClass];
  const aria = ariaLabel ?? `Trust class: ${DEFAULT_LABEL[trustClass]}`;
  return (
    <span
      aria-label={aria}
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        PALETTE[trustClass],
        className,
      )}
    >
      {text}
    </span>
  );
}


// ---------------------------------------------------------------------------
// Trust-class derivation — re-exported from policy-model so there is exactly
// one helper across the customize surface. Historical name ``TrustPolicyLike``
// is aliased to ``PolicyTrustInput`` for backward-compatible imports.
// ---------------------------------------------------------------------------


/**
 * Structural input for :func:`trustClassForPolicy`. Re-exported from
 * :mod:`@/lib/policy-model` under the historical name so callers that
 * already imported ``TrustPolicyLike`` from this module keep compiling.
 *
 * See :type:`PolicyTrustInput` (canonical name) for the field-level
 * contract; both names refer to the same type.
 */
export type TrustPolicyLike = PolicyTrustInput;


/**
 * Map a unified :type:`Policy` (or any structurally similar source bag) to
 * its trust class. Re-exported from :mod:`@/lib/policy-model` so this
 * module and ``rules-table.tsx`` cannot drift apart.
 *
 * Previously a simplified copy lived here that lacked the
 * ``action === "override" → "hybrid"`` distinguisher — that simplified
 * copy was the LIVE rendering path. Re-pointing here at the canonical
 * mapping closes that gap (see PR-F5 follow-up).
 */
export const trustClassForPolicy: (policy: PolicyTrustInput) => TrustClass =
  policyTrustClassForPolicy;
