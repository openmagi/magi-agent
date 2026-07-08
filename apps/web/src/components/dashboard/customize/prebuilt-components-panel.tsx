"use client";

/**
 * PrebuiltComponentsPanel (PR-P4): read-only list of always-on kernel
 * components (read-before-write, path safety, receipts, ...) that gate every
 * turn but had no dashboard surface. Round-2 gap 7b: these prebuilt behaviors
 * were invisible. They are enforced by the runtime and not togglable here, so
 * this is a descriptive, collapsed reference under the Rules tab.
 */

import React, { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { useAgentFetch } from "@/lib/local-api";
import {
  getPrebuiltComponents,
  type PrebuiltComponent,
} from "@/lib/prebuilt-components-api";

export function PrebuiltComponentsPanel(): React.ReactElement | null {
  const agentFetch = useAgentFetch();
  const [components, setComponents] = useState<PrebuiltComponent[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getPrebuiltComponents(agentFetch)
      .then((resp) => setComponents(resp.components))
      .catch(() => setComponents([])) // unavailable → hide the section
      .finally(() => setLoaded(true));
  }, [agentFetch]);

  if (!loaded || components.length === 0) return null;

  return (
    <details className="mt-4 rounded-xl border border-black/[0.06] bg-[var(--color-surface-raised)]">
      <summary className="flex cursor-pointer items-center gap-2 px-4 py-3">
        <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-600" />
        <span className="text-sm font-semibold text-foreground">
          Prebuilt · always-on ({components.length})
        </span>
        <span className="ml-auto text-[11px] text-secondary/70">
          built into the runtime
        </span>
      </summary>
      <div className="border-t border-black/[0.04] px-4 py-2">
        <p className="py-2 text-xs leading-relaxed text-secondary">
          These behaviors gate every turn and are enforced by the runtime. They
          are not togglable from the dashboard.
        </p>
        <div className="divide-y divide-black/[0.04]">
          {components.map((c) => (
            <div key={c.key} className="flex items-start gap-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-medium text-foreground">{c.name}</p>
                  <span className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                    always-on
                  </span>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-secondary">{c.description}</p>
                <p className="mt-1 text-[11px] text-secondary/70">Enforced by: {c.where}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}
