/**
 * Repeated-failure circuit breaker — kills retry cascades that bypass
 * per-task stopConditions because each retry spawns a NEW turnId.
 *
 * Real-world trigger (2026-04-19, Ponzi Kim admin bot, core-agent
 * 0.11.0): `/pipeline` skill invocation tried to edit 5 sealed equity
 * skill files; `sealedFiles.beforeCommit` correctly blocked every turn,
 * but each failure spawned a fresh turnId (stopConditions per-task
 * plateau metric never saw a shared signal → infinite loop until the
 * pod was killed by the operator).
 *
 * Design:
 *   • sealedFiles writes a state entry keyed by sha1(hookName + ":" +
 *     sorted(violationPaths)) before returning `{ action: "block" }`.
 *   • If the same signature trips ≥3 times within 5 minutes, we set
 *     `trippedUntil = now + 10min` and augment the block reason so the
 *     LLM sees the cooldown signal.
 *   • `beforeLLMCall` runs early (priority 5) and refuses to let a new
 *     turn reach the LLM while any signature is tripped.
 *
 * Fail-open: any I/O error reading the state file lets the call
 * through. The breaker is a safety net, not a correctness gate — if
 * its own substrate misbehaves the turn proceeds and the operator is
 * no worse off than pre-breaker.
 *
 * State file: `{workspaceRoot}/core-agent/.circuit-breaker-state.json`.
 * Written atomically via `atomicWriteJson`.
 */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { atomicWriteJson } from "../../storage/atomicWrite.js";
import type { RegisteredHook, HookContext } from "../types.js";

/** Rolling window in which repeated signatures count toward the trip. */
export const CIRCUIT_WINDOW_MS = 5 * 60 * 1000;

/** Cooldown applied after the breaker trips. */
export const CIRCUIT_COOLDOWN_MS = 10 * 60 * 1000;

/** Number of repeated failures within the window that trips the breaker. */
export const CIRCUIT_THRESHOLD = 3;

const STATE_REL = "core-agent/.circuit-breaker-state.json";

export interface CircuitEntry {
  count: number;
  firstAt: number;
  lastAt: number;
  trippedUntil?: number;
}

export type CircuitState = Record<string, CircuitEntry>;

export interface RepeatedFailureGuardOptions {
  workspaceRoot: string;
  /** Test seam — overrides `Date.now()`. */
  now?: () => number;
}

function stateFilePath(workspaceRoot: string): string {
  return path.join(workspaceRoot, STATE_REL);
}

/** Remove the circuit-breaker state file (e.g. on session reset). */
export async function clearCircuitBreakerState(workspaceRoot: string): Promise<void> {
  try {
    await fs.unlink(stateFilePath(workspaceRoot));
  } catch {
    // File may not exist — that's fine.
  }
}

/**
 * Build the signature key. Sorting the paths means
 * `[A,B]` and `[B,A]` collapse to the same entry, so we actually count
 * repeated violations of the same file set regardless of iteration
 * order.
 */
export function signatureFor(hookName: string, paths: readonly string[]): string {
  const sorted = [...paths].map((p) => p.trim()).filter((p) => p.length > 0).sort();
  const raw = `${hookName}:${sorted.join(",")}`;
  return crypto.createHash("sha1").update(raw).digest("hex");
}

export async function readCircuitState(workspaceRoot: string): Promise<CircuitState> {
  let raw: string;
  try {
    raw = await fs.readFile(stateFilePath(workspaceRoot), "utf8");
  } catch {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const out: CircuitState = {};
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (!v || typeof v !== "object") continue;
        const e = v as Record<string, unknown>;
        const count = typeof e["count"] === "number" ? (e["count"] as number) : null;
        const firstAt = typeof e["firstAt"] === "number" ? (e["firstAt"] as number) : null;
        const lastAt = typeof e["lastAt"] === "number" ? (e["lastAt"] as number) : null;
        if (count === null || firstAt === null || lastAt === null) continue;
        const entry: CircuitEntry = { count, firstAt, lastAt };
        const trippedUntil =
          typeof e["trippedUntil"] === "number" ? (e["trippedUntil"] as number) : undefined;
        if (trippedUntil !== undefined) entry.trippedUntil = trippedUntil;
        out[k] = entry;
      }
      return out;
    }
  } catch {
    return {};
  }
  return {};
}

async function writeCircuitState(
  workspaceRoot: string,
  state: CircuitState,
): Promise<void> {
  await atomicWriteJson(stateFilePath(workspaceRoot), state);
}

/**
 * Record a failure for `signature`. If the accumulated count within
 * `CIRCUIT_WINDOW_MS` reaches `CIRCUIT_THRESHOLD`, sets
 * `trippedUntil = now + CIRCUIT_COOLDOWN_MS`. Returns the updated
 * entry so the caller (sealedFiles hook) can compose the block reason.
 *
 * Fail-open: I/O errors are swallowed — caller proceeds with whatever
 * block it would have emitted anyway.
 */
export async function recordFailure(
  opts: RepeatedFailureGuardOptions,
  signature: string,
): Promise<{ entry: CircuitEntry; tripped: boolean }> {
  const now = (opts.now ?? Date.now)();
  try {
    const state = await readCircuitState(opts.workspaceRoot);
    const prev = state[signature];
    let entry: CircuitEntry;
    if (prev && now - prev.firstAt <= CIRCUIT_WINDOW_MS) {
      entry = {
        count: prev.count + 1,
        firstAt: prev.firstAt,
        lastAt: now,
      };
      if (prev.trippedUntil !== undefined) entry.trippedUntil = prev.trippedUntil;
    } else {
      // New window (stale or absent prior entry).
      entry = { count: 1, firstAt: now, lastAt: now };
    }
    let tripped = false;
    if (entry.count >= CIRCUIT_THRESHOLD && entry.trippedUntil === undefined) {
      entry.trippedUntil = now + CIRCUIT_COOLDOWN_MS;
      tripped = true;
    } else if (
      entry.trippedUntil !== undefined &&
      entry.trippedUntil > now &&
      entry.count >= CIRCUIT_THRESHOLD
    ) {
      tripped = true;
    }
    state[signature] = entry;
    await writeCircuitState(opts.workspaceRoot, state);
    return { entry, tripped };
  } catch {
    return {
      entry: { count: 1, firstAt: now, lastAt: now },
      tripped: false,
    };
  }
}

/**
 * Scan `state` for any active trip (trippedUntil > now) and return the
 * signature + remaining ms, or `null` if nothing is tripped. Ties are
 * broken by latest `trippedUntil` first — in practice at most one
 * signature trips at a time.
 */
export function findActiveTrip(
  state: CircuitState,
  now: number,
): { signature: string; entry: CircuitEntry; remainingMs: number } | null {
  let best: { signature: string; entry: CircuitEntry; remainingMs: number } | null = null;
  for (const [signature, entry] of Object.entries(state)) {
    if (entry.trippedUntil === undefined) continue;
    if (entry.trippedUntil <= now) continue;
    const remainingMs = entry.trippedUntil - now;
    if (!best || remainingMs > best.remainingMs) {
      best = { signature, entry, remainingMs };
    }
  }
  return best;
}

/**
 * beforeLLMCall hook — refuses to call the LLM while any signature is
 * tripped. Runs at priority 5 (early) so it short-circuits before any
 * other pre-LLM work. Fail-open on I/O error.
 */
export function makeRepeatedFailureGuardHook(
  opts: RepeatedFailureGuardOptions,
): RegisteredHook<"beforeLLMCall"> {
  const now = opts.now ?? Date.now;
  return {
    name: "builtin:repeated-failure-guard",
    point: "beforeLLMCall",
    priority: 5,
    blocking: true,
    timeoutMs: 2_000,
    handler: async (_args, ctx: HookContext) => {
      let state: CircuitState;
      try {
        state = await readCircuitState(opts.workspaceRoot);
      } catch {
        return { action: "continue" };
      }
      const trip = findActiveTrip(state, now());
      if (!trip) return { action: "continue" };
      const remainingMin = Math.ceil(trip.remainingMs / 60_000);
      const reason = `Circuit breaker active: repeated failure signature ${trip.signature.slice(0, 8)} (count=${trip.entry.count}) — cooldown expires in ${remainingMin}m. Start a new prompt once the window passes.`;
      ctx.log("warn", "[repeatedFailureGuard] blocking beforeLLMCall", {
        turnId: ctx.turnId,
        signature: trip.signature,
        remainingMs: trip.remainingMs,
      });
      return { action: "block", reason };
    },
  };
}

/** Test helpers — NOT public API. */
export const __testing = {
  statePath: stateFilePath,
  STATE_REL,
};
