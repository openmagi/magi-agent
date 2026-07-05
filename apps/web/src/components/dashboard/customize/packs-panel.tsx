"use client";

/**
 * PacksPanel: installed-pack inventory with install/remove management.
 *
 * A pack is an INSTALL unit (availability), not a per-turn on/off. Framing it
 * as "enabled/disabled" implied liveness it does not have: a pack being present
 * only means its refs are contributed to the catalog. Whether a contributed
 * rule/behavior actually fires is decided elsewhere (globally in Rules, or per
 * turn in Modes). So the control here is Remove / Install, not a toggle.
 *
 * Remove is reversible (it writes a dashboard override, never the operator's
 * config.toml), so first-party packs are always recoverable via Install. Each
 * pack still expands to show what it contributes (rules / behaviors / tools).
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Layers, Check } from "lucide-react";

import { useAgentFetch } from "@/lib/local-api";
import { getPacks, setPackState, type PackInfo, type PackProvide } from "@/lib/packs-api";

/** Map the 12 ProvidesType kinds to the operator-facing category vocabulary. */
const PROVIDE_CATEGORY: Record<string, string> = {
  tool: "Tools",
  validator: "Rules",
  harness: "Rules",
  control_plane: "Behaviors",
  callback: "Behaviors",
  loop_policy: "Behaviors",
  schedule_policy: "Behaviors",
  memory_strategy: "Behaviors",
  evidence_producer: "Evidence",
  recipe: "Recipes",
  connector: "Connectors",
  role: "Roles",
};

function categoryOf(type: string): string {
  return PROVIDE_CATEGORY[type] ?? "Other";
}

function groupProvides(provides: PackProvide[]): [string, PackProvide[]][] {
  const groups = new Map<string, PackProvide[]>();
  for (const p of provides) {
    const cat = categoryOf(p.type);
    const bucket = groups.get(cat);
    if (bucket) bucket.push(p);
    else groups.set(cat, [p]);
  }
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

export function PacksPanel(): React.ReactElement {
  const agentFetch = useAgentFetch();
  const [packs, setPacks] = useState<PackInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** packId whose install/remove request is in flight (disables its button). */
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    getPacks(agentFetch)
      .then((resp) => setPacks(resp.packs))
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Failed to load packs"),
      )
      .finally(() => setLoading(false));
  }, [agentFetch]);

  const handleSetState = useCallback(
    (packId: string, enabled: boolean) => {
      setBusyId(packId);
      setActionError(null);
      setPackState(agentFetch, packId, enabled)
        .then((resp) => setPacks(resp.packs))
        .catch((err: unknown) =>
          setActionError(
            err instanceof Error ? err.message : "Failed to update pack",
          ),
        )
        .finally(() => setBusyId(null));
    },
    [agentFetch],
  );

  const firstParty = useMemo(() => packs.filter((p) => p.origin === "first_party"), [packs]);
  const user = useMemo(() => packs.filter((p) => p.origin === "user"), [packs]);

  if (loading) {
    return (
      <div className="flex h-24 items-center justify-center text-sm text-secondary">
        Loading packs…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-600">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <p className="text-xs leading-relaxed text-secondary">
        Installed packs and what each contributes. <strong>Remove</strong> a pack
        to drop everything it contributes; <strong>Install</strong> restores it
        (first-party packs are always recoverable). Installing makes a pack&apos;s
        rules, behaviors, and tools <em>available</em>; whether a rule actually
        runs is set globally in Rules or per turn in Modes.
      </p>

      {actionError ? (
        <div className="rounded-lg border border-red-500/25 bg-red-500/[0.06] px-3 py-2 text-xs text-red-600">
          {actionError}
        </div>
      ) : null}

      {user.length > 0 ? (
        <PackGroup title="Your packs" packs={user} busyId={busyId} onSetState={handleSetState} />
      ) : null}
      <PackGroup title="First-party" packs={firstParty} busyId={busyId} onSetState={handleSetState} />

      {packs.length === 0 ? (
        <p className="rounded-xl border border-dashed border-black/[0.10] bg-gray-50/80 px-4 py-6 text-center text-xs text-secondary">
          No packs installed.
        </p>
      ) : null}
    </div>
  );
}

function PackGroup({
  title,
  packs,
  busyId,
  onSetState,
}: {
  title: string;
  packs: PackInfo[];
  busyId: string | null;
  onSetState: (packId: string, enabled: boolean) => void;
}): React.ReactElement | null {
  if (packs.length === 0) return null;
  return (
    <div className="space-y-2">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/70">
        {title} ({packs.length})
      </p>
      {packs.map((pack) => (
        <PackCard
          key={pack.packId}
          pack={pack}
          busy={busyId === pack.packId}
          onSetState={onSetState}
        />
      ))}
    </div>
  );
}

function PackCard({
  pack,
  busy,
  onSetState,
}: {
  pack: PackInfo;
  busy: boolean;
  onSetState: (packId: string, enabled: boolean) => void;
}): React.ReactElement {
  const groups = groupProvides(pack.provides);
  return (
    <details className="rounded-xl border border-black/[0.06] bg-white px-4 py-3">
      <summary className="flex cursor-pointer items-center gap-2">
        <Layers className="h-4 w-4 shrink-0 text-primary" />
        <span className="truncate text-sm font-semibold text-foreground">{pack.displayName}</span>
        {pack.enabled ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
            <Check className="h-3 w-3" /> installed
          </span>
        ) : (
          <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-medium text-secondary">
            removed
          </span>
        )}
        <span className="ml-auto shrink-0 text-[11px] text-secondary/70">
          {pack.provides.length} item{pack.provides.length === 1 ? "" : "s"}
        </span>
        {pack.enabled ? (
          <button
            type="button"
            disabled={busy}
            data-testid={`pack-remove-${pack.packId}`}
            onClick={(e) => {
              // Inside <summary>: don't toggle the disclosure on click.
              e.preventDefault();
              e.stopPropagation();
              onSetState(pack.packId, false);
            }}
            className="shrink-0 rounded-lg border border-red-500/30 bg-white px-2.5 py-1 text-[11px] font-medium text-red-600 hover:bg-red-500/[0.06] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Removing…" : "Remove"}
          </button>
        ) : (
          <button
            type="button"
            disabled={busy}
            data-testid={`pack-install-${pack.packId}`}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onSetState(pack.packId, true);
            }}
            className="shrink-0 rounded-lg bg-primary px-2.5 py-1 text-[11px] font-medium text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Installing…" : "Install"}
          </button>
        )}
      </summary>
      <p className="mt-1 font-mono text-[10px] text-secondary/70">{pack.packId}</p>
      {pack.description ? (
        <p className="mt-1 text-xs leading-relaxed text-secondary">{pack.description}</p>
      ) : null}
      {groups.length > 0 ? (
        <div className="mt-3 space-y-2">
          {groups.map(([cat, entries]) => (
            <div key={cat}>
              <p className="text-[11px] font-semibold text-foreground">
                {cat} <span className="text-secondary/60">({entries.length})</span>
              </p>
              <div className="mt-1 flex flex-wrap gap-1">
                {entries.map((e) => (
                  <span
                    key={`${e.type}:${e.ref}`}
                    title={e.type}
                    className="rounded border border-secondary/15 bg-secondary/[0.04] px-1.5 py-0.5 font-mono text-[10px] text-secondary/80"
                  >
                    {e.ref}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs text-secondary/70">This pack contributes nothing directly.</p>
      )}
    </details>
  );
}
