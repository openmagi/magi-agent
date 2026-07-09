"use client";

/**
 * U4b -- Egress Guard allowlist + mode editor (design 5.5).
 *
 * The `egress_guard` first-party security policy records outbound destinations
 * in `audit` mode (default) and, in opt-in `block` mode, DENIES a first-hop
 * destination that misses this operator-managed allowlist. The whole `~/.magi`
 * directory (this file's backing store) is agent-write-protected, so this
 * dashboard surface is the operator's sanctioned edit path; every save leaves a
 * config-change audit row on the backend.
 *
 * Presentational: the parent owns the fetch (via `onSaveAllowlist` /
 * `onSaveMode`), mirroring the BudgetsTab contract. Pattern grammar shown to the
 * user: an exact host (`api.github.com`) or a single-suffix wildcard
 * (`*.github.com`, which does NOT match the bare apex). Extraction is first-hop
 * and best-effort; only the opt-in egress proxy is authoritative.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Globe, Plus, Trash2 } from "lucide-react";

import { isValidAllowlistPattern } from "./egress-allowlist-pattern";

export { isValidAllowlistPattern };

export type EgressMode = "audit" | "block";

interface EgressAllowlistEditorProps {
  /** The persisted host-pattern allowlist (exact host or *.suffix wildcard). */
  allowlist: string[];
  /** The persisted enforcement mode ("audit" | "block"; "" = profile default). */
  mode: string;
  loading?: boolean;
  saving?: boolean;
  /** Surface error from the most recent failed load or save. */
  error?: string | null;
  /** Called with the edited allowlist when the user saves. */
  onSaveAllowlist: (next: string[]) => void;
  /** Called when the user flips the enforcement mode. */
  onSaveMode: (next: EgressMode) => void;
}

export function EgressAllowlistEditor({
  allowlist,
  mode,
  loading,
  saving,
  error,
  onSaveAllowlist,
  onSaveMode,
}: EgressAllowlistEditorProps): React.ReactElement {
  // Local edit buffer synced from props on mount / parent reload.
  const [rows, setRows] = useState<string[]>(() => [...allowlist]);
  const [draft, setDraft] = useState<string>("");

  useEffect(() => {
    setRows([...allowlist]);
  }, [allowlist]);

  const effectiveMode: EgressMode = mode === "block" ? "block" : "audit";

  const dirty = useMemo(() => {
    if (rows.length !== allowlist.length) return true;
    return rows.some((r, i) => r !== allowlist[i]);
  }, [rows, allowlist]);

  const draftValid = draft.trim() === "" || isValidAllowlistPattern(draft);

  const addDraft = useCallback(() => {
    const token = draft.trim().toLowerCase();
    if (!token || !isValidAllowlistPattern(token)) return;
    setRows((prev) => (prev.includes(token) ? prev : [...prev, token]));
    setDraft("");
  }, [draft]);

  const removeRow = useCallback((host: string) => {
    setRows((prev) => prev.filter((r) => r !== host));
  }, []);

  const handleSave = useCallback(() => {
    onSaveAllowlist(rows);
  }, [rows, onSaveAllowlist]);

  return (
    <section aria-labelledby="egress-allowlist-heading" className="space-y-4">
      <header className="flex items-center gap-2">
        <Globe className="h-4 w-4" aria-hidden />
        <h3 id="egress-allowlist-heading" className="text-sm font-medium">
          Egress Guard allowlist
        </h3>
      </header>

      <p className="text-xs text-muted-foreground">
        In <strong>block</strong> mode an outbound request to a host that is not
        in this allowlist is denied. Use an exact host (
        <code>api.github.com</code>) or a single-suffix wildcard (
        <code>*.github.com</code>, which does not match the bare apex).
        Extraction is first-hop and best-effort; only the opt-in egress proxy is
        authoritative.
      </p>

      {/* Mode selector */}
      <div className="flex items-center gap-3" role="radiogroup" aria-label="Egress mode">
        {(["audit", "block"] as EgressMode[]).map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={effectiveMode === m}
            disabled={saving || loading}
            onClick={() => onSaveMode(m)}
            className={
              effectiveMode === m
                ? "rounded border px-3 py-1 text-xs font-medium bg-foreground text-background"
                : "rounded border px-3 py-1 text-xs text-muted-foreground"
            }
          >
            {m === "audit" ? "Audit (record only)" : "Block (deny non-allowlisted)"}
          </button>
        ))}
      </div>

      {/* Allowlist rows */}
      <ul className="space-y-2" aria-label="Allowlisted hosts">
        {rows.length === 0 ? (
          <li className="text-xs text-muted-foreground">
            No hosts allowlisted. In block mode every outbound host is denied.
          </li>
        ) : (
          rows.map((host) => (
            <li key={host} className="flex items-center justify-between gap-2">
              <code className="text-xs">{host}</code>
              <button
                type="button"
                aria-label={`Remove ${host}`}
                disabled={saving || loading}
                onClick={() => removeRow(host)}
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </button>
            </li>
          ))
        )}
      </ul>

      {/* Add row */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={draft}
          placeholder="api.github.com or *.github.com"
          aria-label="Add host pattern"
          aria-invalid={!draftValid}
          disabled={saving || loading}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addDraft();
            }
          }}
          className="flex-1 rounded border px-2 py-1 text-xs"
        />
        <button
          type="button"
          aria-label="Add host"
          disabled={saving || loading || !draft.trim() || !draftValid}
          onClick={addDraft}
          className="rounded border px-2 py-1 text-xs"
        >
          <Plus className="h-4 w-4" aria-hidden />
        </button>
      </div>
      {!draftValid ? (
        <p className="text-xs text-destructive">
          Not a valid host pattern. Use an exact host or a single-suffix
          wildcard, with no port, path, or scheme.
        </p>
      ) : null}

      {error ? <p className="text-xs text-destructive">{error}</p> : null}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!dirty || saving || loading}
          onClick={handleSave}
          className="rounded border px-3 py-1 text-xs font-medium disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save allowlist"}
        </button>
      </div>
    </section>
  );
}
