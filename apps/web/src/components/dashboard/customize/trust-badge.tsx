/**
 * Shared trust-class badge for the Customize surface.
 *
 * Encodes the honesty taxonomy that the rest of the customize tab speaks:
 *
 *  * deterministic    — runtime gate; the model cannot opt out
 *  * advisory         — prompt-injected guidance; the model may ignore it
 *  * hybrid           — a check that both records evidence AND may rewrite the
 *                       tool output (e.g. dashboard_check action="override" /
 *                       strip). Currently no built-in action takes this
 *                       branch, but the badge is forward-compatible so adding
 *                       such an action later lights it up automatically.
 *  * preview          — visible but inert; not feeding the runtime
 *  * mutator          — actively rewrites or injects traffic (PR-F-MUT1 +
 *                       PR-F-MUT2). Distinct amber-yellow palette so an
 *                       operator never mistakes a mutator for a passive
 *                       advisory critic. Carries an explicit "modifies
 *                       traffic" tooltip (PR-F-MUT3).
 *  * operator_defined — external operator-authored shell script (PR-F-EXEC1 +
 *                       PR-F-EXEC2 — shell_command / shell_check). Dedicated
 *                       amber-red palette + Terminal icon so an operator can
 *                       see, before activating, that magi does NOT verify the
 *                       script body and the verdict is whatever the operator
 *                       wrote. Carries an explicit "external script" tooltip
 *                       (PR-F-EXEC3).
 *
 * The component intentionally lives under `customize/` (not the generic
 * `ui/_ds/Badge`) because the trust-class semantics belong to this domain,
 * not to the design-system token sheet. Visually it converges on the F1
 * inline Advisory pill (amber, uppercase, rounded-full) so the swap is
 * byte-equivalent for the advisory variant.
 */

import { Terminal } from "lucide-react";
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
  /** Override the hover tooltip (rendered as ``title``). Defaults to the
   *  variant-specific :const:`DEFAULT_TOOLTIP` — empty string for variants
   *  that have no warning to surface, the explicit "modifies traffic"
   *  warning for ``mutator``. */
  tooltip?: string;
  /** Caller-supplied utility classes appended after the variant palette. */
  className?: string;
}

const PALETTE: Record<TrustClass, string> = {
  deterministic: "bg-emerald-500/10 text-emerald-700",
  advisory: "bg-amber-500/10 text-amber-700",
  hybrid: "bg-violet-500/10 text-violet-700",
  preview: "bg-blue-500/10 text-blue-700",
  // F-MUT3 — distinct amber-yellow ramp (yellow-400 tint + yellow-900 ink)
  // so the badge reads as "warmer / more alarming than advisory" without
  // colliding with the destructive red palette used elsewhere in the
  // dashboard. Carries an explicit "modifies traffic" title (tooltip) so an
  // operator hovering the badge sees the honest mutation warning before
  // activating the policy.
  mutator: "bg-yellow-400/15 text-yellow-900 ring-1 ring-inset ring-yellow-500/30",
  // F-EXEC3 — dedicated amber-red (#dc8f3d-ish) ramp. Distinct from the
  // mutator yellow-400 hue and the destructive red palette: amber-600
  // background tint + amber-900 ink + amber-700 inset ring reads as
  // "warmer, more alarming than advisory" without colliding with either
  // sibling. Paired with the Terminal icon below so the operator sees
  // both the colour and the affordance before activating the rule.
  operator_defined:
    "bg-amber-600/15 text-amber-900 ring-1 ring-inset ring-amber-700/40",
};

const DEFAULT_LABEL: Record<TrustClass, string> = {
  deterministic: "Deterministic",
  advisory: "Advisory",
  hybrid: "Hybrid",
  preview: "Preview",
  mutator: "Mutator",
  operator_defined: "Operator-defined",
};


/**
 * Tooltip text shown on hover. Today only the ``mutator`` variant ships a
 * non-empty tooltip because it is the only variant that REWRITES traffic the
 * model sees — the operator needs an explicit "modifies traffic" warning
 * before activating. The other four variants are self-describing via the
 * existing aria-label so the badge stays visually clean.
 */
const DEFAULT_TOOLTIP: Record<TrustClass, string> = {
  deterministic: "",
  advisory: "",
  hybrid: "",
  preview: "",
  mutator:
    "Modifies inbound or outbound traffic. Verify the mutation does not break downstream tools or the model reasoning.",
  // F-EXEC1 — explicit "magi does NOT verify the script" tooltip so the
  // operator sees the honest trust framing before activating.
  operator_defined:
    "External script authored by the operator. magi does NOT verify the script. Confirm the command does what you expect before activating.",
};

export function TrustBadge({
  trustClass,
  label,
  ariaLabel,
  tooltip,
  className,
}: TrustBadgeProps): ReactElement {
  const text = label ?? DEFAULT_LABEL[trustClass];
  const aria = ariaLabel ?? `Trust class: ${DEFAULT_LABEL[trustClass]}`;
  // F-MUT3 — empty default tooltip for non-mutator variants is OMITTED from
  // the DOM (``title={undefined}``) so the existing four variants render
  // byte-equivalently to their pre-F-MUT3 markup; only ``mutator`` carries
  // the explicit "modifies traffic" hover warning. F-EXEC3 adds the
  // ``operator_defined`` variant which ALSO carries a tooltip (external
  // script warning) and an inline Terminal icon — the icon is the only
  // glyph in any variant, so non-operator_defined badges still render
  // byte-equivalently.
  const resolvedTooltip = tooltip ?? DEFAULT_TOOLTIP[trustClass];
  const titleAttr =
    resolvedTooltip && resolvedTooltip.length > 0 ? resolvedTooltip : undefined;
  return (
    <span
      aria-label={aria}
      title={titleAttr}
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        PALETTE[trustClass],
        className,
      )}
    >
      {trustClass === "operator_defined" ? (
        <Terminal
          aria-hidden="true"
          className="mr-1 h-3 w-3 shrink-0"
          data-testid="trust-badge-icon-operator-defined"
        />
      ) : null}
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
