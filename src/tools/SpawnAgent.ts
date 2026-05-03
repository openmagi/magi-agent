/**
 * SpawnAgent — native subagent delegation (§7.12.d).
 *
 * This file is the Tool-factory surface. Everything interesting moved
 * to `../spawn/` during R4:
 *   • ChildAgentLoop.ts   — runChildAgentLoop + selectChildTools
 *   • Tournament.ts       — runTournament + rank/merge helpers (pure)
 *   • ScoreVariant.ts     — T3-16 scoring (tool + haiku_rubric)
 *   • SpawnWorkspace.ts   — prepareSpawnDir + countFilesRecursive + randomTaskId
 *
 * This module keeps three responsibilities:
 *   1. Zod-style JSON input schema + two-layer input validation
 *      (`validate()` hook + hard guard inside `execute()`).
 *   2. `applyPersona` — T2-11 catalog expansion.
 *   3. `makeSpawnAgentTool(agent, backgroundRegistry?)` factory that
 *      glues the three deliver paths (tournament / return / background)
 *      to the spawn runtime.
 *
 * External API stability (tests + Agent.ts import these names):
 *   • makeSpawnAgentTool  — default export of the factory
 *   • runChildAgentLoop   — re-exported from ../spawn/ChildAgentLoop
 *   • selectChildTools    — re-exported from ../spawn/ChildAgentLoop
 *   • SpawnChildOptions / SpawnChildResult — ditto
 *   • MAX_SPAWN_DEPTH     — still authoritative here
 *   • TournamentResult / TournamentVariantResult — re-exported
 *   • prepareSpawnDir / countFilesRecursive / applyPersona — still
 *     exported (SpawnAgent.test.ts reaches in for isolation tests).
 */

import fs from "node:fs/promises";
import path from "node:path";
import type {
  Tool,
  ToolContext,
  ToolResult,
  ToolRegistry as IToolRegistry,
} from "../Tool.js";
import type { Agent } from "../Agent.js";
import type { BackgroundTaskRegistry } from "../tasks/BackgroundTaskRegistry.js";
import { errorResult } from "../util/toolResult.js";
import type { ArtifactMeta } from "../artifacts/ArtifactManager.js";
import type { PermissionMode } from "../Session.js";
import {
  ALLOWED_TOOLS_WILDCARD,
  loadPersonaCatalog,
  resolvePersona,
  type PersonaCatalog,
  type PersonaSpec,
} from "../personas/catalog.js";
import {
  runTournament as runTournamentCore,
  TOURNAMENT_MAX_CONCURRENCY,
  TOURNAMENT_MAX_VARIANTS,
  TOURNAMENT_MIN_VARIANTS,
  type PreparedVariant,
  type TournamentResult,
  type TournamentVariantResult,
} from "../spawn/Tournament.js";
import {
  runChildAgentLoop as runChildAgentLoopImpl,
  selectChildTools as selectChildToolsImpl,
  type SpawnChildOptions as SpawnChildOptionsImpl,
  type SpawnChildResult as SpawnChildResultImpl,
  type SpawnWorkspacePolicy,
} from "../spawn/ChildAgentLoop.js";
import {
  scoreVariant,
  type TournamentScorer as TournamentScorerImpl,
} from "../spawn/ScoreVariant.js";
import {
  countFilesRecursive as countFilesRecursiveImpl,
  prepareSpawnDir as prepareSpawnDirImpl,
  randomTaskId,
} from "../spawn/SpawnWorkspace.js";
import {
  createChildAgentHarness,
  recordChildTerminal,
} from "../spawn/ChildAgentHarness.js";
import {
  buildSpawnWorkOrderPrompt,
  type ExecutionContractStore,
} from "../execution/ExecutionContract.js";

export const MAX_SPAWN_DEPTH = 2;
const DEFAULT_TIMEOUT_MS = 120_000;
const MAX_TIMEOUT_MS = 600_000;

/**
 * Canonical models available for SpawnAgent model override.
 * Matches api-proxy PRICING dict keys (excluding local LLM models).
 * api-proxy auto-routes to the correct provider by model name.
 */
const CANONICAL_SPAWNABLE_MODELS = [
  "claude-opus-4-7",
  "claude-opus-4-6",
  "claude-sonnet-4-6",
  "claude-sonnet-4-5",
  "claude-haiku-4-5",
  "claude-haiku-4-5-20251001",
  "gpt-5-nano",
  "gpt-5-mini",
  "gpt-5.1",
  "gpt-5.4",
  "gpt-5.4-nano",
  "gpt-5.4-mini",
  "gpt-5.5",
  "gpt-5.5-pro",
  "kimi-k2p6",
  "minimax-m2p7",
  "gemini-2.5-flash",
  "gemini-2.5-pro",
  "gemini-3.1-flash-lite-preview",
  "gemini-3.1-pro-preview",
] as const;

const SPAWN_MODEL_ALIASES: Readonly<Record<string, (typeof CANONICAL_SPAWNABLE_MODELS)[number]>> = {
  "anthropic/claude-opus-4-7": "claude-opus-4-7",
  "anthropic/claude-opus-4-6": "claude-opus-4-6",
  "anthropic/claude-sonnet-4-6": "claude-sonnet-4-6",
  "anthropic/claude-sonnet-4-5": "claude-sonnet-4-5",
  "anthropic/claude-haiku-4-5": "claude-haiku-4-5",
  "anthropic/claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
  "openai/gpt-5-nano": "gpt-5-nano",
  "openai/gpt-5-mini": "gpt-5-mini",
  "openai/gpt-5.1": "gpt-5.1",
  "openai/gpt-5.4": "gpt-5.4",
  "openai/gpt-5.4-nano": "gpt-5.4-nano",
  "openai/gpt-5.4-mini": "gpt-5.4-mini",
  "openai/gpt-5.5": "gpt-5.5",
  "openai/gpt-5.5-pro": "gpt-5.5-pro",
  "fireworks/kimi-k2p6": "kimi-k2p6",
  "fireworks/minimax-m2p7": "minimax-m2p7",
  "google/gemini-2.5-flash": "gemini-2.5-flash",
  "google/gemini-2.5-pro": "gemini-2.5-pro",
  "google/gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite-preview",
  "google/gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
};

/**
 * Models accepted in the SpawnAgent input schema. Includes provider-prefixed
 * aliases because workspace prompts and dynamic model docs use those ids.
 */
export const SPAWNABLE_MODELS: readonly string[] = [
  ...CANONICAL_SPAWNABLE_MODELS,
  ...Object.keys(SPAWN_MODEL_ALIASES),
] as const;

function normalizeSpawnModelOverride(model: string | undefined): string | undefined | null {
  if (model === undefined) return undefined;
  if ((CANONICAL_SPAWNABLE_MODELS as readonly string[]).includes(model)) return model;
  return SPAWN_MODEL_ALIASES[model] ?? null;
}

// Re-exports for API stability — Agent.ts + tests import these from
// `tools/SpawnAgent.js`.
export const runChildAgentLoop = runChildAgentLoopImpl;
export const selectChildTools = selectChildToolsImpl;
export const prepareSpawnDir = prepareSpawnDirImpl;
export const countFilesRecursive = countFilesRecursiveImpl;
export type SpawnChildOptions = SpawnChildOptionsImpl;
export type SpawnChildResult = SpawnChildResultImpl;
export type TournamentScorer = TournamentScorerImpl;
export type { TournamentResult, TournamentVariantResult };

export interface SpawnAgentInput {
  persona: string;
  prompt: string;
  allowed_tools?: string[];
  allowed_skills?: string[];
  deliver: "return" | "background";
  timeout_ms?: number;
  completion_contract?: SpawnCompletionContract;
  /**
   * Trusted children share the parent workspace authority. Isolated is the
   * legacy `.spawn/{taskId}` sandbox and must be requested explicitly.
   */
  workspace_policy?: SpawnWorkspacePolicy;
  metadata?: Record<string, unknown>;
  /** Override the LLM model for this child. Must be in SPAWNABLE_MODELS. */
  model?: string;
  /** T3-16 OMC Port A — tournament mode inputs. */
  mode?: "single" | "tournament";
  variants?: number;
  scorer?: TournamentScorer;
  concurrency?: number;
  cleanup_losers?: boolean;
}

export type SpawnEvidenceRequirement = "tool_call" | "files" | "artifact" | "text" | "none";

export interface SpawnCompletionContract {
  /**
   * `tool_call` means a child result is only successful if the child
   * actually used at least one tool. Use `none` only for answer-only
   * delegation where no tool/file/artifact evidence is expected.
   * Default is `tool_call` because SpawnAgent is for concrete delegated work.
   */
  required_evidence?: SpawnEvidenceRequirement;
  /** Workspace-relative files that must exist before the child is accepted. */
  required_files?: string[];
  /** Additional guard for modes that still need a visible child summary. */
  require_non_empty_result?: boolean;
  reason?: string;
}

/**
 * Minimal projection of an imported artifact surfaced on the spawn
 * tool's output. Parent LLMs see `{artifactId, kind, title, l1Preview}`
 * so they can decide whether to readL0 for the full content.
 */
export interface SpawnHandoffArtifact {
  artifactId: string;
  kind: string;
  title: string;
  slug: string;
  /** 2-line overview (L1) if readable; empty string on missing sidecar. */
  l1Preview: string;
  /** Present when re-keyed on collision during import. */
  importedFromArtifactId?: string;
}

export interface SpawnArtifacts {
  /** Absolute path to the child's ephemeral workspace subdirectory. */
  spawnDir: string;
  /** Number of files present in spawnDir after child completion. */
  fileCount: number;
  /**
   * Artifacts produced by the child that were imported into the
   * parent's workspace via `ArtifactManager.importFromDir`. Parent LLM
   * should call `ArtifactRead` (or `ArtifactManager.readL0`) on any
   * `artifactId` here to pull full content. Empty list when the child
   * produced no artifacts.
   */
  handedOffArtifacts: SpawnHandoffArtifact[];
}

export interface SpawnAgentOutput {
  taskId: string;
  status: "ok" | "error" | "aborted" | "pending";
  finalText?: string;
  toolCallCount?: number;
  attempts?: number;
  errorMessage?: string;
  /**
   * Narrow handoff API for parent access to child artifacts (PRE-01).
   * Parent can `FileRead` via absolute path or `kubectl cp` if needed.
   * Omitted in `deliver=background` immediate return (pending status).
   */
  artifacts?: SpawnArtifacts;
  /** T3-16 — present only when `mode === "tournament"`. */
  mode?: "tournament";
  winnerIndex?: number;
  variants?: TournamentVariantResult[];
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    persona: {
      type: "string",
      description:
        "Sub-agent identity (e.g. 'legal-researcher'). Prepended as a system-prompt role.",
    },
    prompt: {
      type: "string",
      description: "Task description for the sub-agent.",
    },
    allowed_tools: {
      type: "array",
      items: { type: "string" },
      description:
        "Allowlist of tool names; intersected with the parent's registry. Omit to inherit all.",
    },
    allowed_skills: {
      type: "array",
      items: { type: "string" },
      description: "Allowlist of skill tags to expose in addition to allowed_tools.",
    },
    deliver: {
      type: "string",
      enum: ["return", "background"],
      description:
        "'return' blocks the parent until the child finishes. 'background' returns a taskId immediately; child completion emits a spawn_result AgentEvent.",
    },
    timeout_ms: {
      type: "integer",
      minimum: 1000,
      description: "Per-child timeout in ms (default 120000, max 600000).",
    },
    workspace_policy: {
      type: "string",
      enum: ["trusted", "isolated"],
      description:
        "Workspace authority for the child. Default 'trusted' lets the child read/write the parent workspace. 'isolated' confines file tools and Bash cwd to .spawn/{taskId}.",
    },
    completion_contract: {
      type: "object",
      description:
        "Completion evidence contract. Default requires at least one child tool call; set required_evidence='none' only for answer-only delegation.",
      properties: {
        required_evidence: {
          type: "string",
          enum: ["tool_call", "files", "artifact", "text", "none"],
          description:
            "Evidence required before the child can be accepted as successful. Use files with required_files for durable workspace deliverables.",
        },
        required_files: {
          type: "array",
          items: { type: "string" },
          description:
            "Workspace-relative files that must exist when required_evidence='files'. Checked in parent workspace for trusted children and spawnDir for isolated children.",
        },
        require_non_empty_result: {
          type: "boolean",
          description: "When true, finalText must contain visible non-whitespace text.",
        },
        reason: {
          type: "string",
          description: "Short reason for any opt-out from tool evidence.",
        },
      },
    },
    metadata: { type: "object", description: "Opaque metadata passed to the child." },
    model: {
      type: "string",
      enum: SPAWNABLE_MODELS,
      description:
        "Override the LLM model for this child agent. Omit to use the bot's default model.",
    },
    mode: {
      type: "string",
      enum: ["single", "tournament"],
      description:
        "T3-16 — 'single' (default) runs one child. 'tournament' spawns `variants` children in parallel and ranks them by `scorer`.",
    },
    variants: {
      type: "integer",
      minimum: TOURNAMENT_MIN_VARIANTS,
      maximum: TOURNAMENT_MAX_VARIANTS,
      description: "Tournament: number of variants (2..5).",
    },
    scorer: {
      type: "object",
      description:
        "Tournament scorer — either a tool reference or a Haiku rubric string.",
    },
    concurrency: {
      type: "integer",
      minimum: 1,
      maximum: TOURNAMENT_MAX_CONCURRENCY,
      description: "Tournament: max concurrent children (default = variants, max 5).",
    },
    cleanup_losers: {
      type: "boolean",
      description:
        "Tournament: when true, remove non-winning spawnDirs after ranking (default false).",
    },
  },
  required: ["persona", "prompt", "deliver"],
} as const;

// ── Persona expansion (T2-11) ──────────────────────────────────────

export interface PersonaExpansion {
  /** Effective allowed_tools to pass to the child loop. `undefined` = inherit all parent tools. */
  allowedTools: string[] | undefined;
  /** Effective allowed_skills allowlist. `undefined` = no skill-tag filter. */
  allowedSkills: string[] | undefined;
  /** Task prompt — preset system_prompt prepended to the caller's prompt when applicable. */
  prompt: string;
  /** True when the persona name matched a catalog entry. */
  matched: boolean;
}

/**
 * Apply a persona catalog entry to a spawn call. Caller's explicit
 * allowed_tools / allowed_skills always win over the preset. Wildcard
 * `"*"` in the preset expands to `undefined` (inherit parent's full
 * registry). Unmatched persona names pass through unchanged.
 */
export function applyPersona(
  input: {
    persona: string;
    prompt: string;
    allowed_tools?: string[];
    allowed_skills?: string[];
  },
  catalog: PersonaCatalog,
): PersonaExpansion {
  const spec: PersonaSpec | null = resolvePersona(input.persona, catalog);
  if (spec === null) {
    return {
      allowedTools: input.allowed_tools,
      allowedSkills: input.allowed_skills,
      prompt: input.prompt,
      matched: false,
    };
  }
  let allowedTools: string[] | undefined;
  if (input.allowed_tools !== undefined) {
    allowedTools = input.allowed_tools;
  } else if (spec.allowed_tools === ALLOWED_TOOLS_WILDCARD) {
    allowedTools = undefined;
  } else {
    allowedTools = spec.allowed_tools.slice();
  }
  const allowedSkills =
    input.allowed_skills !== undefined
      ? input.allowed_skills
      : spec.allowed_skills
        ? spec.allowed_skills.slice()
        : undefined;
  return {
    allowedTools,
    allowedSkills,
    prompt: `${spec.system_prompt}\n\n${input.prompt}`,
    matched: true,
  };
}

// ── Tournament adapter ─────────────────────────────────────────────
// Wires ctx/agent/scorer into the generic spawn/Tournament core.

async function runTournamentAdapter(
  input: SpawnAgentInput,
  ctx: ToolContext,
  agent: Agent,
  baseChildOptions: Omit<SpawnChildOptions, "spawnDir" | "spawnWorkspace" | "taskId">,
): Promise<TournamentResult> {
  const variants = input.variants as number;
  const scorer = input.scorer as TournamentScorer;
  return runTournamentCore({
    variants,
    concurrency: input.concurrency,
    cleanup_losers: input.cleanup_losers,
    ctx: {
      workspaceRoot: ctx.workspaceRoot,
      turnId: ctx.turnId,
      stageAuditEvent: ctx.staging.stageAuditEvent,
      emitAgentEvent: ctx.emitAgentEvent,
    },
    prepareSpawnDir,
    async runChild(prep: PreparedVariant) {
      const lifecycle = createChildAgentHarness({
        taskId: prep.taskId,
        parentTurnId: ctx.turnId,
        prompt: input.prompt,
        emitControlEvent: ctx.emitControlEvent,
        emitAgentEvent: ctx.emitAgentEvent,
      });
      await lifecycle.started();
      const childOptions: SpawnChildOptions = {
        ...baseChildOptions,
        taskId: prep.taskId,
        spawnDir: prep.spawnDir,
        spawnWorkspace: prep.spawnWorkspace,
        lifecycle,
      };
      const childResult = await agent.spawnChildTurn(childOptions);
      await recordChildTerminal(lifecycle, childResult);
      return { finalText: childResult.finalText };
    },
    async scoreChild(_prep, finalText) {
      return scoreVariant(finalText, scorer, ctx, agent);
    },
  });
}

/** Legacy name preserved for any external importers / tests. */
export const runTournament = runTournamentAdapter;

/**
 * Spawn artifact handoff — PRE-01 fix for "4/5 리포트 유실".
 *
 * After a child finishes, any artifacts it produced under
 * `{spawnDir}/artifacts/` are copied into the parent workspace via
 * `ArtifactManager.importFromDir`. Returns the projection the parent
 * LLM receives in `artifacts.handedOffArtifacts`. Import failures are
 * absorbed so a flaky disk doesn't fail the whole spawn — the child's
 * `finalText` still reaches the caller.
 *
 * When the child didn't write any artifacts (the common case today
 * since ArtifactCreate binds to the parent's ArtifactManager at
 * registration time) this is a no-op and returns `[]`.
 */
async function handOffChildArtifacts(
  agent: Agent,
  spawnDir: string,
  taskId: string,
  emit?: (event: unknown) => void,
): Promise<SpawnHandoffArtifact[]> {
  // Duck-type guard so test fakes without `.artifacts` don't crash.
  const mgr = (agent as { artifacts?: Agent["artifacts"] }).artifacts;
  if (!mgr) return [];
  let imported: ArtifactMeta[] = [];
  try {
    imported = await mgr.importFromDir(spawnDir, { spawnTaskId: taskId });
  } catch (err) {
    emit?.({
      type: "spawn_artifact_handoff_error",
      taskId,
      error: err instanceof Error ? err.message : String(err),
    });
    return [];
  }
  if (imported.length === 0) return [];

  const projection: SpawnHandoffArtifact[] = [];
  for (const meta of imported) {
    let l1Preview = "";
    try {
      l1Preview = await mgr.readL1(meta.artifactId);
    } catch {
      /* sidecar missing — preview stays empty */
    }
    projection.push({
      artifactId: meta.artifactId,
      kind: meta.kind,
      title: meta.title,
      slug: meta.slug,
      l1Preview,
      ...(meta.importedFromArtifactId
        ? { importedFromArtifactId: meta.importedFromArtifactId }
        : {}),
    });
  }
  emit?.({
    type: "spawn_artifacts_imported",
    taskId,
    count: projection.length,
    artifactIds: projection.map((a) => a.artifactId),
  });
  return projection;
}

// ── Child retry helpers ────────────────────────────────────────────

interface ChildRunWithRetries {
  result: SpawnChildResult;
  attempts: number;
}

interface RunChildWithRetriesArgs {
  agent: Agent;
  childOptions: SpawnChildOptions;
  taskId: string;
  deliver: SpawnAgentInput["deliver"];
  completionContract: SpawnCompletionContract;
  emit?: (event: unknown) => void;
  stageAuditEvent?: (event: string, data?: Record<string, unknown>) => void;
}

function readBoundedIntegerEnv(
  names: string[],
  defaultValue: number,
  min: number,
  max: number,
): number {
  for (const name of names) {
    const raw = process.env[name];
    if (raw === undefined) continue;
    const parsed = Number.parseInt(raw, 10);
    if (Number.isFinite(parsed)) {
      return Math.min(max, Math.max(min, parsed));
    }
  }
  return defaultValue;
}

function spawnMaxAttempts(): number {
  return readBoundedIntegerEnv(
    ["CORE_AGENT_SPAWN_MAX_ATTEMPTS", "CORE_AGENT_RETURN_SPAWN_MAX_ATTEMPTS"],
    3,
    1,
    5,
  );
}

function spawnRetryBaseDelayMs(): number {
  return readBoundedIntegerEnv(["CORE_AGENT_SPAWN_RETRY_BASE_DELAY_MS"], 250, 0, 5_000);
}

function spawnRetryDelayMs(attempt: number): number {
  const base = spawnRetryBaseDelayMs();
  if (base <= 0) return 0;
  return Math.min(5_000, base * 2 ** Math.max(0, attempt - 1));
}

function normalizeCompletionContract(input: SpawnAgentInput): SpawnCompletionContract & {
  required_evidence: SpawnEvidenceRequirement;
} {
  const requiredEvidence = input.completion_contract?.required_evidence ?? "tool_call";
  return {
    required_evidence: requiredEvidence,
    ...(input.completion_contract?.required_files
      ? { required_files: input.completion_contract.required_files }
      : {}),
    ...(input.completion_contract?.require_non_empty_result !== undefined
      ? { require_non_empty_result: input.completion_contract.require_non_empty_result }
      : {}),
    ...(input.completion_contract?.reason
      ? { reason: input.completion_contract.reason }
      : {}),
  };
}

function spawnErrorMessage(result: SpawnChildResult): string {
  return result.errorMessage || result.finalText || result.status;
}

function completionContractError(
  result: SpawnChildResult,
  errorMessage: string,
): SpawnChildResult {
  return {
    status: "error",
    finalText: result.finalText,
    toolCallCount: result.toolCallCount,
    errorMessage,
  };
}

function contractWorkspaceRoot(childOptions: SpawnChildOptionsImpl): string {
  return childOptions.workspacePolicy === "isolated"
    ? childOptions.spawnDir
    : childOptions.workspaceRoot;
}

function resolveContractFile(root: string, relPath: string): string {
  const normalised = path.normalize(relPath).replace(/^\/+/, "");
  const full = path.resolve(root, normalised);
  const resolvedRoot = path.resolve(root);
  if (!full.startsWith(resolvedRoot + path.sep) && full !== resolvedRoot) {
    throw new Error(`required file path escapes workspace: ${relPath}`);
  }
  return full;
}

async function missingRequiredFiles(
  childOptions: SpawnChildOptionsImpl,
  requiredFiles: string[],
): Promise<string[]> {
  const root = contractWorkspaceRoot(childOptions);
  const missing: string[] = [];
  for (const relPath of requiredFiles) {
    try {
      await fs.access(resolveContractFile(root, relPath));
    } catch {
      missing.push(relPath);
    }
  }
  return missing;
}

async function enforceChildCompletionContract(
  result: SpawnChildResult,
  contract: SpawnCompletionContract,
  childOptions: SpawnChildOptionsImpl,
): Promise<SpawnChildResult> {
  if (result.status !== "ok") {
    return result;
  }
  if (
    contract.require_non_empty_result === true &&
    result.finalText.trim().length === 0
  ) {
    return completionContractError(
      result,
      "child returned empty final text but SpawnAgent completion_contract requires non-empty final text",
    );
  }

  switch (contract.required_evidence) {
    case "none":
      return result;
    case "tool_call":
      return result.toolCallCount > 0
        ? result
        : completionContractError(
            result,
            "child returned no tool-call evidence required by SpawnAgent completion_contract (0 tool calls)",
          );
    case "text":
      return result.finalText.trim().length > 0
        ? result
        : completionContractError(
            result,
            "child returned empty final text but SpawnAgent completion_contract requires non-empty final text",
          );
    case "files": {
      const requiredFiles = contract.required_files ?? [];
      const missing = await missingRequiredFiles(childOptions, requiredFiles);
      return missing.length === 0
        ? result
        : completionContractError(
            result,
            `child missing required files for SpawnAgent completion_contract: ${missing.join(", ")}`,
          );
    }
    case "artifact": {
      const artifactFileCount = await countFilesRecursive(
        path.join(childOptions.spawnDir, "artifacts"),
      );
      return artifactFileCount > 0
        ? result
        : completionContractError(
            result,
            "child produced no artifact evidence required by SpawnAgent completion_contract",
          );
    }
    default:
      return result;
  }
}

function buildSpawnFailureMessage(
  taskId: string,
  result: SpawnChildResult,
  attempts: number,
): string {
  const verb = result.status === "aborted" ? "aborted" : "failed";
  return `SpawnAgent task ${taskId} ${verb} after ${attempts} attempt${
    attempts === 1 ? "" : "s"
  }: ${spawnErrorMessage(result)}. Do not switch to direct execution or answer from parent memory. Retry SpawnAgent if duplicate side effects are safe, otherwise ask the user before doing the work directly.`;
}

function spawnFailureResult(
  taskId: string,
  result: SpawnChildResult,
  attempts: number,
  start: number,
  artifacts?: SpawnArtifacts,
): ToolResult<SpawnAgentOutput> {
  const errorMessage = buildSpawnFailureMessage(taskId, result, attempts);
  return {
    status: result.status === "aborted" ? "aborted" : "error",
    errorCode: result.status === "aborted" ? "spawn_aborted" : "spawn_failed",
    errorMessage,
    output: {
      taskId,
      status: result.status === "aborted" ? "aborted" : "error",
      finalText: result.finalText,
      toolCallCount: result.toolCallCount,
      attempts,
      errorMessage,
      ...(artifacts ? { artifacts } : {}),
    },
    durationMs: Date.now() - start,
  };
}

function partialChildResultFromError(err: unknown): SpawnChildResultImpl | null {
  if (!err || typeof err !== "object" || !("partialResult" in err)) {
    return null;
  }
  const partial = (err as { partialResult?: unknown }).partialResult;
  if (!partial || typeof partial !== "object") return null;
  const candidate = partial as Partial<SpawnChildResultImpl>;
  if (
    (candidate.status === "ok" ||
      candidate.status === "error" ||
      candidate.status === "aborted") &&
    typeof candidate.finalText === "string" &&
    typeof candidate.toolCallCount === "number"
  ) {
    return {
      status: candidate.status,
      finalText: candidate.finalText,
      toolCallCount: candidate.toolCallCount,
      ...(typeof candidate.errorMessage === "string"
        ? { errorMessage: candidate.errorMessage }
        : {}),
    };
  }
  return null;
}

async function sleepForRetry(ms: number, signal: AbortSignal): Promise<void> {
  if (ms <= 0 || signal.aborted) return;
  await new Promise<void>((resolve) => {
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const done = (): void => {
      if (timeout) clearTimeout(timeout);
      signal.removeEventListener("abort", done);
      resolve();
    };
    timeout = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

async function runChildWithRetries(
  args: RunChildWithRetriesArgs,
): Promise<ChildRunWithRetries> {
  const {
    agent,
    childOptions,
    taskId,
    deliver,
    completionContract,
    emit,
    stageAuditEvent,
  } = args;
  const maxAttempts = spawnMaxAttempts();
  let attempts = 0;

  for (;;) {
    if (childOptions.abortSignal.aborted) {
      return {
        attempts: Math.max(1, attempts),
        result: {
          status: "aborted",
          finalText: "",
          toolCallCount: 0,
          errorMessage: "parent aborted before child spawn could complete",
        },
      };
    }

    attempts++;
    let result: SpawnChildResult;
    try {
      result = await agent.spawnChildTurn(childOptions);
    } catch (err) {
      result =
        partialChildResultFromError(err) ?? {
          status: childOptions.abortSignal.aborted ? "aborted" : "error",
          finalText: "",
          toolCallCount: 0,
          errorMessage: err instanceof Error ? err.message : String(err),
        };
    }
    result = await enforceChildCompletionContract(
      result,
      completionContract,
      childOptions,
    );

    const canRetry =
      result.status === "error" &&
      result.toolCallCount === 0 &&
      attempts < maxAttempts &&
      !childOptions.abortSignal.aborted;
    if (!canRetry) {
      return { result, attempts };
    }

    const delayMs = spawnRetryDelayMs(attempts);
    const retryEvent = {
      type: "spawn_retry",
      taskId,
      deliver,
      attempt: attempts,
      nextAttempt: attempts + 1,
      maxAttempts,
      delayMs,
      errorMessage: spawnErrorMessage(result),
    };
    emit?.(retryEvent);
    stageAuditEvent?.("spawn_retry", {
      taskId,
      deliver,
      attempt: attempts,
      nextAttempt: attempts + 1,
      maxAttempts,
      delayMs,
      errorMessage: spawnErrorMessage(result),
    });
    await sleepForRetry(delayMs, childOptions.abortSignal);
  }
}

// ── Validation helpers ─────────────────────────────────────────────

function validateTournamentInput(input: SpawnAgentInput): string | null {
  if (
    typeof input.variants !== "number" ||
    !Number.isInteger(input.variants) ||
    input.variants < TOURNAMENT_MIN_VARIANTS ||
    input.variants > TOURNAMENT_MAX_VARIANTS
  ) {
    return `tournament mode requires integer \`variants\` in [${TOURNAMENT_MIN_VARIANTS}..${TOURNAMENT_MAX_VARIANTS}]`;
  }
  if (!input.scorer || typeof input.scorer !== "object") {
    return "tournament mode requires `scorer`";
  }
  const s = input.scorer;
  if (s.kind !== "tool" && s.kind !== "haiku_rubric") {
    return "`scorer.kind` must be 'tool' or 'haiku_rubric'";
  }
  if (s.kind === "tool" && (typeof s.toolName !== "string" || !s.toolName)) {
    return "tool scorer requires `toolName`";
  }
  if (s.kind === "haiku_rubric" && (typeof s.rubric !== "string" || !s.rubric)) {
    return "haiku_rubric scorer requires `rubric`";
  }
  if (
    input.concurrency !== undefined &&
    (typeof input.concurrency !== "number" ||
      !Number.isInteger(input.concurrency) ||
      input.concurrency < 1 ||
      input.concurrency > input.variants)
  ) {
    return `\`concurrency\` must be integer in [1..${input.variants}]`;
  }
  return null;
}

function validateInput(input: SpawnAgentInput): string | null {
  if (!input || typeof input.persona !== "string" || input.persona.length === 0) {
    return "`persona` is required";
  }
  if (typeof input.prompt !== "string" || input.prompt.length === 0) {
    return "`prompt` is required";
  }
  if (input.deliver !== "return" && input.deliver !== "background") {
    return "`deliver` must be 'return' or 'background'";
  }
  if (
    input.workspace_policy !== undefined &&
    input.workspace_policy !== "trusted" &&
    input.workspace_policy !== "isolated"
  ) {
    return "`workspace_policy` must be 'trusted' or 'isolated'";
  }
  const requiredEvidence = input.completion_contract?.required_evidence;
  if (
    requiredEvidence !== undefined &&
    requiredEvidence !== "tool_call" &&
    requiredEvidence !== "files" &&
    requiredEvidence !== "artifact" &&
    requiredEvidence !== "text" &&
    requiredEvidence !== "none"
  ) {
    return "`completion_contract.required_evidence` must be 'tool_call', 'files', 'artifact', 'text', or 'none'";
  }
  if (
    requiredEvidence === "files" &&
    (!Array.isArray(input.completion_contract?.required_files) ||
      input.completion_contract.required_files.length === 0)
  ) {
    return "`completion_contract.required_files` must be a non-empty array when required_evidence is 'files'";
  }
  if (normalizeSpawnModelOverride(input.model) === null) {
    return `\`model\` must be one of: ${SPAWNABLE_MODELS.join(", ")}`;
  }
  if (input.mode === "tournament") {
    return validateTournamentInput(input);
  }
  return null;
}

function resolveParentPermissionMode(agent: Agent, sessionKey: string): PermissionMode {
  const getSession = (agent as {
    getSession?: (key: string) => { getPermissionMode?: () => PermissionMode } | undefined;
  }).getSession;
  if (!getSession) return "default";
  const session = getSession.call(agent, sessionKey);
  return session?.getPermissionMode ? session.getPermissionMode() : "default";
}

// ── Tool factory ───────────────────────────────────────────────────

/**
 * Build the SpawnAgent tool, bound to a specific Agent. The Agent
 * reference gives us access to the shared LLMClient + ToolRegistry.
 */
export function makeSpawnAgentTool(
  agent: Agent,
  backgroundRegistry?: BackgroundTaskRegistry,
): Tool<SpawnAgentInput, SpawnAgentOutput> {
  return {
    name: "SpawnAgent",
    description:
      "Delegate a focused sub-task to a child agent with a custom persona and filtered toolset. Children are trusted workers by default: they can read/write the parent workspace, and `.spawn/{taskId}` is scratch/audit storage. Set `workspace_policy:\"isolated\"` only when the task should be sandboxed to `.spawn/{taskId}`. The child does not inherit full conversation context unless you include it in `prompt`, so provide concrete inputs, required files, expected outputs, allowed tools, completion criteria, and retry/idempotency guidance. `deliver:\"return\"` blocks until the child finishes and returns final text. `deliver:\"background\"` returns a taskId immediately; completion surfaces as a spawn_result event and TaskGet record. Spawn depth is capped at 2. Use `completion_contract.required_evidence:\"files\"` plus `required_files` for durable file deliverables, `\"text\"` for answer-only work, `\"artifact\"` for artifact handoff, `\"tool_call\"` for concrete tool work, or `\"none\"` only when no evidence is expected. Use `model` only when deliberately selecting a child model; copy an exact value from this schema enum or omit `model` to inherit the bot's runtime model. Never invent provider/model ids. IMPORTANT: child output longer than ~500 chars should go into ArtifactCreate rather than finalText; child-produced artifacts are imported into the parent workspace and returned on `artifacts.handedOffArtifacts`.",
    inputSchema: INPUT_SCHEMA,
    permission: "meta",
    kind: "core",
    validate(input) {
      return validateInput(input);
    },
    async execute(
      input: SpawnAgentInput,
      ctx: ToolContext,
    ): Promise<ToolResult<SpawnAgentOutput>> {
      const start = Date.now();
      const parentDepth = ctx.spawnDepth ?? 0;

      if (parentDepth >= MAX_SPAWN_DEPTH) {
        return {
          status: "error",
          errorCode: "max_depth",
          errorMessage: `spawn depth ${parentDepth} is at the MAX_SPAWN_DEPTH=${MAX_SPAWN_DEPTH} limit; this child cannot spawn further`,
          durationMs: Date.now() - start,
        };
      }

      // Hard guard (`validate()` is advisory; some runtimes skip it).
      const modelOverride = normalizeSpawnModelOverride(input.model);
      if (modelOverride === null) {
        return {
          status: "error",
          errorCode: "bad_input",
          errorMessage: `\`model\` must be one of: ${SPAWNABLE_MODELS.join(", ")}`,
          durationMs: Date.now() - start,
        };
      }
      if (input.mode === "tournament") {
        const err = validateTournamentInput(input);
        if (err) {
          return {
            status: "error",
            errorCode: "bad_input",
            errorMessage: err,
            durationMs: Date.now() - start,
          };
        }
      }

      const taskId = randomTaskId();
      const timeoutMs = Math.min(
        MAX_TIMEOUT_MS,
        Math.max(1_000, input.timeout_ms ?? DEFAULT_TIMEOUT_MS),
      );
      const completionContract = normalizeCompletionContract(input);

      // T2-11 persona catalog expansion. Loaded per-call so workspace
      // overrides are picked up live; falls back to BUILTIN_PERSONAS on
      // ENOENT / parse error (catalog loader swallows read errors).
      const catalog = await loadPersonaCatalog(ctx.workspaceRoot);
      const expanded = applyPersona(
        {
          persona: input.persona,
          prompt: input.prompt,
          allowed_tools: input.allowed_tools,
          allowed_skills: input.allowed_skills,
        },
        catalog,
      );
      const getSession = (agent as {
        getSession?: (sessionKey: string) => { executionContract?: ExecutionContractStore } | undefined;
      }).getSession;
      const parentSession = typeof getSession === "function"
        ? getSession.call(agent, ctx.sessionKey)
        : undefined;
      const parentExecutionContract = parentSession?.executionContract;
      const childPrompt = parentExecutionContract
        ? buildSpawnWorkOrderPrompt({
            parent: parentExecutionContract.snapshot(),
            childPrompt: expanded.prompt,
            persona: input.persona,
            allowedTools: expanded.allowedTools,
          })
        : expanded.prompt;
      if (parentExecutionContract) {
        const snapshot = parentExecutionContract.snapshot();
        parentExecutionContract.recordWorkOrder({
          persona: input.persona,
          goal: snapshot.taskState.goal ?? input.prompt,
          constraints: snapshot.taskState.constraints,
          acceptanceCriteria: snapshot.taskState.acceptanceCriteria,
          criteria: snapshot.taskState.criteria,
          resourceBindings: snapshot.taskState.resourceBindings,
          allowedTools: expanded.allowedTools ?? [],
          childPrompt: input.prompt,
        });
      }

      const baseChildOptions: Omit<
        SpawnChildOptions,
        "spawnDir" | "spawnWorkspace" | "taskId"
      > = {
        parentSessionKey: ctx.sessionKey,
        parentTurnId: ctx.turnId,
        parentSpawnDepth: parentDepth,
        persona: input.persona,
        prompt: childPrompt,
        allowedTools: expanded.allowedTools,
        allowedSkills: expanded.allowedSkills,
        timeoutMs,
        abortSignal: ctx.abortSignal,
        botId: ctx.botId,
        workspaceRoot: ctx.workspaceRoot,
        workspacePolicy: input.workspace_policy ?? "trusted",
        onAgentEvent: ctx.emitAgentEvent,
        askUser: ctx.askUser,
        permissionMode: resolveParentPermissionMode(agent, ctx.sessionKey),
        ...(modelOverride ? { modelOverride } : {}),
      };

      // T3-16 — tournament mode branches here. The tournament runner
      // prepares its own per-variant spawnDirs; no single-spawn
      // allocation on this path.
      if (input.mode === "tournament") {
        try {
          const tourney = await runTournamentAdapter(input, ctx, agent, baseChildOptions);
          // 2026-04-20 — hand off EVERY variant's artifacts (winner and
          // losers alike) so partial work is never lost when
          // `cleanup_losers` is true. Run sequentially to keep a stable
          // parent-index append order.
          const allHandedOff: SpawnHandoffArtifact[] = [];
          for (const v of tourney.variants) {
            const variantTaskId = `${taskId}.tournament-${v.variantIndex}`;
            const variantArtifacts = await handOffChildArtifacts(
              agent,
              v.spawnDir,
              variantTaskId,
              ctx.emitAgentEvent,
            );
            allHandedOff.push(...variantArtifacts);
          }
          const winner = tourney.variants.find(
            (v) => v.variantIndex === tourney.winnerIndex,
          );
          return {
            status: "ok",
            output: {
              taskId,
              status: "ok",
              finalText: winner?.finalText ?? "",
              mode: "tournament",
              winnerIndex: tourney.winnerIndex,
              variants: tourney.variants,
              ...(allHandedOff.length > 0
                ? {
                    artifacts: {
                      spawnDir: path.join(ctx.workspaceRoot, ".spawn"),
                      fileCount: 0,
                      handedOffArtifacts: allHandedOff,
                    },
                  }
                : {}),
            },
            durationMs: Date.now() - start,
          };
        } catch (err) {
          return errorResult(err, start);
        }
      }

      // Single spawn — allocate ephemeral subdir before launching.
      const prepared = await prepareSpawnDir(ctx.workspaceRoot, taskId).catch(
        (err) => err,
      );
      if (prepared instanceof Error) {
        return errorResult(prepared, start);
      }
      const { spawnDir, spawnWorkspace } = prepared;

      ctx.emitAgentEvent?.({
        type: "spawn_started",
        taskId,
        persona: input.persona,
        prompt: input.prompt,
        deliver: input.deliver,
        ...(modelOverride ? { model: modelOverride } : {}),
      });
      ctx.emitAgentEvent?.({ type: "spawn_dir_created", taskId, spawnDir });
      ctx.staging.stageAuditEvent("spawn_dir_created", { taskId, spawnDir });
      const lifecycle = createChildAgentHarness({
        taskId,
        parentTurnId: ctx.turnId,
        prompt: input.prompt,
        emitControlEvent: ctx.emitControlEvent,
        emitAgentEvent: ctx.emitAgentEvent,
      });
      await lifecycle.started();

      const childOptions: SpawnChildOptions = {
        ...baseChildOptions,
        spawnDir,
        spawnWorkspace,
        taskId,
        lifecycle,
      };

      if (input.deliver === "return") {
        try {
          const childRun = await runChildWithRetries({
            agent,
            childOptions,
            taskId,
            deliver: input.deliver,
            completionContract,
            emit: ctx.emitAgentEvent,
            stageAuditEvent: ctx.staging.stageAuditEvent,
          });
          const result = childRun.result;
          await recordChildTerminal(lifecycle, result);
          // 2026-04-20 — import child-produced artifacts BEFORE counting
          // files / cleaning up so the child's artifacts/ subtree is
          // scanned while still on disk.
          const handedOffArtifacts = await handOffChildArtifacts(
            agent,
            spawnDir,
            taskId,
            ctx.emitAgentEvent,
          );
          const fileCount = await countFilesRecursive(spawnDir);
          // Retention policy (2026-04-20): always retain spawnDir even
          // when empty. Loss of the dir hides "child promised a file
          // but didn't write it" cases — parent post-mortem needs the
          // dir to exist to inspect. Cleanup is handled out-of-band
          // (cron or manual). Prior behavior: rm -rf if fileCount===0,
          // which silently hid the bug observed on admin bot
          // 2026-04-20 where toolCallCount=24 but fileCount=0.
          ctx.emitAgentEvent?.({
            type: "spawn_dir_retained",
            taskId,
            spawnDir,
            fileCount,
          });
          const artifacts = { spawnDir, fileCount, handedOffArtifacts };
          if (result.status !== "ok") {
            return spawnFailureResult(
              taskId,
              result,
              childRun.attempts,
              start,
              artifacts,
            );
          }
          return {
            status: "ok",
            output: {
              taskId,
              status: result.status,
              finalText: result.finalText,
              toolCallCount: result.toolCallCount,
              attempts: childRun.attempts,
              artifacts,
            },
            durationMs: Date.now() - start,
          };
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          await lifecycle.failed(msg);
          return errorResult(err, start);
        }
      }

      // Background mode — fire-and-forget; emit spawn_result on completion.
      await runBackgroundChild({
        agent,
        ctx,
        taskId,
        spawnDir,
        childOptions,
        backgroundRegistry,
        input,
        completionContract,
      });
      return {
        status: "ok",
        output: { taskId, status: "pending" },
        durationMs: Date.now() - start,
      };
    },
  };
}

// ── Background fire-and-forget runner ──────────────────────────────

interface BackgroundRunArgs {
  agent: Agent;
  ctx: ToolContext;
  taskId: string;
  spawnDir: string;
  childOptions: SpawnChildOptions;
  backgroundRegistry?: BackgroundTaskRegistry;
  input: SpawnAgentInput;
  completionContract: SpawnCompletionContract;
}

/**
 * Background-mode runner. Owns lifecycle of a dedicated AbortController
 * (so TaskStop can abort independently of the parent turn), registers
 * with the BackgroundTaskRegistry, and emits the final spawn_result
 * AgentEvent onto the parent SSE channel when the child finishes.
 *
 * Parent turn may have ended by the time the child finishes —
 * SseWriter.ended guards against post-end writes.
 */
async function runBackgroundChild(args: BackgroundRunArgs): Promise<void> {
  const {
    agent,
    ctx,
    taskId,
    spawnDir,
    childOptions,
    backgroundRegistry,
    input,
    completionContract,
  } = args;
  const emit = ctx.emitAgentEvent;

  const bgController = new AbortController();
  const onParentAbortBg = (): void => bgController.abort();
  ctx.abortSignal.addEventListener("abort", onParentAbortBg, { once: true });
  const bgChildOptions: SpawnChildOptions = {
    ...childOptions,
    abortSignal: bgController.signal,
  };

  if (backgroundRegistry) {
    // Registry is best-effort; never fail the spawn on a persist error.
    // Must await `create` BEFORE kicking off the child turn so later
    // `attachResult` calls find the row (T2-10 invariant).
    try {
      await backgroundRegistry.create({
        taskId,
        parentTurnId: ctx.turnId,
        sessionKey: ctx.sessionKey,
        persona: input.persona,
        prompt: input.prompt,
        spawnDir,
        abortController: bgController,
      });
    } catch {
      /* ignore — registry is best-effort */
    }
  }

  void runChildWithRetries({
    agent,
    childOptions: bgChildOptions,
    taskId,
    deliver: input.deliver,
    completionContract,
    emit,
    stageAuditEvent: ctx.staging.stageAuditEvent,
  })
    .then(async (childRun) => {
      const result = childRun.result;
      await recordChildTerminal(childOptions.lifecycle, result);
      // 2026-04-20 — hand off child-produced artifacts to the parent
      // before we emit the final spawn_result so the parent sees them
      // in the event payload (not just post-hoc on `list()`).
      const handedOffArtifacts = await handOffChildArtifacts(
        agent,
        spawnDir,
        taskId,
        emit,
      );
      const fileCount = await countFilesRecursive(spawnDir);
      emit?.({
        type: "spawn_result",
        taskId,
        status: result.status,
        finalText: result.finalText,
        toolCallCount: result.toolCallCount,
        attempts: childRun.attempts,
        artifacts: { spawnDir, fileCount, handedOffArtifacts },
        ...(result.status === "ok"
          ? result.errorMessage
            ? { errorMessage: result.errorMessage }
            : {}
          : {
              errorMessage: buildSpawnFailureMessage(
                taskId,
                result,
                childRun.attempts,
              ),
            }),
      });
      if (backgroundRegistry) {
        const mappedStatus =
          result.status === "ok"
            ? "completed"
            : result.status === "aborted"
              ? "aborted"
              : "failed";
        const errorMessage =
          result.status === "ok"
            ? result.errorMessage
            : buildSpawnFailureMessage(taskId, result, childRun.attempts);
        await backgroundRegistry
          .attachResult(taskId, {
            status: mappedStatus,
            resultText: result.finalText,
            toolCallCount: result.toolCallCount,
            attempts: childRun.attempts,
            artifacts: { spawnDir, fileCount, handedOffArtifacts },
            ...(errorMessage ? { error: errorMessage } : {}),
          })
          .catch(() => {
            /* ignore persist errors */
          });
      }
    })
    .catch(async (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      await childOptions.lifecycle?.failed(msg);
      emit?.({
        type: "spawn_result",
        taskId,
        status: "error",
        finalText: "",
        toolCallCount: 0,
        errorMessage: msg,
      });
      if (backgroundRegistry) {
        await backgroundRegistry
          .attachResult(taskId, { status: "failed", error: msg })
          .catch(() => {
            /* ignore */
          });
      }
    })
    .finally(() => {
      ctx.abortSignal.removeEventListener("abort", onParentAbortBg);
    });
}

/** Re-export for tests. */
export { IToolRegistry };
