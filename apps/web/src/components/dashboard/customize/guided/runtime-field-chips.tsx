"use client";

/**
 * RuntimeFieldChips — variable picker shown above wizard text inputs that
 * accept runtime variable references (regex pattern, contentMatch pattern,
 * llm_criterion criterion, SHACL TTL).
 *
 * PR-F-UX2 (F8 core). Per the design doc Gap C: the wizard should surface
 * the exact set of fields the runtime delivers to the gate so the operator
 * never authors against a name the runtime cannot honor. The chip list
 * comes from `GET /v1/app/customize/runtime-fields` (read-only, fail-open).
 *
 * Click behavior:
 *   - The chip's canonical token (e.g. `tool_input.url`, `{{session_id}}`)
 *     is handed to ``onInsert`` which the parent splices into the input at
 *     the current caret position. The parent owns the input ref + cursor
 *     restoration logic — this component is presentation-only.
 *
 * Default-degrade:
 *   - When the backend returns an empty chip list (flag OFF, unknown
 *     tuple, or fetch error) the component renders nothing. The host input
 *     still works as before; no visual "no chips" placeholder is shown so
 *     the wizard stays clean.
 *
 * Token-shape policy:
 *   - The chip's displayed label is the canonical variable name.
 *   - The inserted token is the SAME bare name — runtime regex / contentMatch
 *     don't apply variable substitution today, so the chip serves as a
 *     "what's available?" reference. Authors who want a literal Jinja-style
 *     reference can wrap as ``{{name}}`` themselves; this component does NOT
 *     guess at substitution syntax that the runtime gate does not implement.
 */

import { useEffect, useMemo, useState } from "react";

import { getRuntimeFields, type RuntimeFieldChip } from "@/lib/customize-api";
import { useAgentFetch } from "@/lib/local-api";


export interface RuntimeFieldChipsProps {
  /** Wizard lifecycle (e.g. ``after_tool_use``). */
  lifecycle: string;
  /** Wizard conditionKind (e.g. ``regex``, ``llm_criterion``, ``shacl``). */
  condition: string;
  /** Optional tool name; expands ``tool_input.*`` to manifest properties. */
  tool?: string | null;
  /** Callback fired with the chip token when the operator clicks a chip. */
  onInsert: (token: string) => void;
  /** Optional label shown above the chip row (default: "Available runtime variables"). */
  label?: string;
}


export function RuntimeFieldChips({
  lifecycle,
  condition,
  tool,
  onInsert,
  label = "Available runtime variables",
}: RuntimeFieldChipsProps): React.ReactElement | null {
  const agentFetch = useAgentFetch();
  const [chips, setChips] = useState<RuntimeFieldChip[]>([]);
  const [loading, setLoading] = useState(false);

  // Coerce ``tool`` to a stable key for the effect dep array so changing
  // from undefined -> "" -> "FileRead" refetches correctly.
  const toolKey = useMemo(
    () => (typeof tool === "string" && tool.length > 0 ? tool : ""),
    [tool],
  );

  useEffect(() => {
    let cancelled = false;
    if (!lifecycle || !condition) {
      setChips([]);
      return () => {
        cancelled = true;
      };
    }
    setLoading(true);
    getRuntimeFields(agentFetch, {
      lifecycle,
      condition,
      tool: toolKey || null,
    })
      .then((res) => {
        if (cancelled) return;
        setChips(res.fields);
      })
      .catch(() => {
        if (cancelled) return;
        setChips([]);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentFetch, lifecycle, condition, toolKey]);

  if (chips.length === 0) {
    return null;
  }

  return (
    <div className="space-y-1.5" data-testid="runtime-field-chips">
      <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
        {label}
        {loading ? <span className="ml-1 text-secondary/50">(loading)</span> : null}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((chip) => (
          <button
            key={chip.name}
            type="button"
            onClick={() => onInsert(chip.name)}
            title={
              chip.description
                ? `${chip.type} — ${chip.description}`
                : chip.type
            }
            aria-label={`Insert ${chip.name}`}
            className="inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/[0.04] px-2 py-0.5 font-mono text-[11px] text-primary/90 hover:border-primary/60 hover:bg-primary/[0.10] focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            {chip.name}
            <span className="text-[10px] uppercase tracking-wide text-secondary/60">
              {chip.type}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}


export default RuntimeFieldChips;
