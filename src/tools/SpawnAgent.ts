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
 *   3. `makeSpawnAgentTool(agent, backgroundRegistry?, deps?)` factory that
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
import type { MissionClient } from "../missions/MissionClient.js";
import type { MissionChannelType, MissionKind } from "../missions/types.js";
import { errorResult, summariseDelegatedPrompt } from "../util/toolResult.js";
import type { ArtifactMeta } from "../artifacts/ArtifactManager.js";
import type { PermissionMode } from "../Session.js";
import {
  ALLOWED_TOOLS_WILDCARD,
  loadPersonaCatalog,
  resolvePersona,
  type PersonaCatalog,
  type PersonaCompletionContract,
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
  canPrepareGitWorktreeSpawnDir,
  countFilesRecursive as countFilesRecursiveImpl,
  prepareGitWorktreeSpawnDir,
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
import type { ChannelRef, MessageAttachment, UserMessage } from "../util/types.js";

export const MAX_SPAWN_DEPTH = 2;
const DEFAULT_RETURN_TIMEOUT_MS = 120_000;
const DEFAULT_BACKGROUND_TIMEOUT_MS = 3 * 60 * 60 * 1000;
const MAX_RETURN_TIMEOUT_MS = 600_000;
const MAX_BACKGROUND_TIMEOUT_MS = 6 * 60 * 60 * 1000;
const PARENT_TURN_CONTEXT_TEXT_LIMIT = 24_000;
const PARENT_TURN_CONTEXT_SYSTEM_LIMIT = 64_000;
const SPAWN_RESULT_SOURCE_SNIPPET_MAX = 500;
const SPAWN_PARENT_SUMMARY_LIMIT = 1_800;
const CODING_PERSONA_PATTERN =
  /(?:^|[-_\s])(coder|coding|code|developer|engineer|implementer)(?:$|[-_\s])/i;
const CODING_TOOL_HINTS = new Set([
  "PatchApply",
  "FileEdit",
  "CodeDiagnostics",
  "ProjectVerificationPlanner",
]);
const MISSION_KINDS: ReadonlySet<MissionKind> = new Set([
  "manual",
  "goal",
  "spawn",
  "cron",
  "script_cron",
  "pipeline",
  "browser_qa",
  "document",
  "research",
]);
const MISSION_ARTIFACT_CATEGORIES: ReadonlySet<string> = new Set([
  "child_result",
  "parallel_research_evidence",
  "parallel_synthesis",
] as const);

/**
 * Canonical models available for SpawnAgent model override.
 * Matches api-proxy PRICING dict keys (excluding local LLM models).
 * api-proxy auto-routes to the correct provider by model name.
 */
const CANONICAL_SPAWNABLE_MODELS = [
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
  "gemini-3.1-flash-lite-preview",
  "gemini-3.1-pro-preview",
] as const;

const SPAWN_MODEL_ALIASES: Readonly<Record<string, (typeof CANONICAL_SPAWNABLE_MODELS)[number]>> = {
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
  "google/gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite-preview",
  "google/gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
};

const DEPRECATED_SPAWN_MODEL_ALIASES: Readonly<Record<string, (typeof CANONICAL_SPAWNABLE_MODELS)[number]>> = {
  "claude-opus-4-7": "claude-opus-4-6",
  "anthropic/claude-opus-4-7": "claude-opus-4-6",
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
  return SPAWN_MODEL_ALIASES[model] ?? DEPRECATED_SPAWN_MODEL_ALIASES[model] ?? null;
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
   * When omitted, coding-oriented children use a detached git worktree when
   * the parent workspace can create one; other children use trusted mode.
   */
  workspace_policy?: SpawnWorkspacePolicy;
  /** Optional child read scope, workspace-relative files/directories. */
  allowed_files?: string[];
  /** Optional child write scope, workspace-relative files/directories. */
  write_set?: string[];
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
  childEvidence?: SpawnChildResult["evidence"];
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
  winnerWorktreeApply?: {
    action: "preview";
    spawnDir: string;
  };
}

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    persona: {
      type: "string",
      description:
        "Sub-agent identity or builtin preset. Builtins include research, explore, scout, synthesis, planner, coder, reviewer, and conflict_resolver.",
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
      description:
        "Per-child timeout in ms. Default is 120000 for deliver='return' and 10800000 for deliver='background'. Max is 600000 for return and 21600000 for background.",
    },
    workspace_policy: {
      type: "string",
      enum: ["trusted", "isolated", "git_worktree"],
      description:
        "Workspace authority for the child. Omit for automatic policy: coding-oriented children (for example persona='coder' or non-empty write_set) use 'git_worktree' when the parent git repo has HEAD; otherwise the child uses 'trusted'. 'trusted' lets the child read/write the parent workspace. 'isolated' confines file tools and Bash cwd to .spawn/{taskId}. 'git_worktree' runs the child in .spawn/{taskId}/worktree as a detached git worktree.",
    },
    allowed_files: {
      type: "array",
      items: { type: "string" },
      description:
        "Optional workspace-relative files/directories the child may read. Omit to inherit normal workspace read scope.",
    },
    write_set: {
      type: "array",
      items: { type: "string" },
      description:
        "Optional workspace-relative files/directories the child may write. Use for deterministic child ownership and merge safety.",
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

const DEFAULT_CHILD_BROWSER_TOOLS = ["Browser", "SocialBrowser"] as const;

function withDefaultChildBrowserTools(tools: string[]): string[] {
  if (tools.length === 0) return tools;
  const out = tools.slice();
  for (const tool of DEFAULT_CHILD_BROWSER_TOOLS) {
    if (!out.includes(tool)) out.push(tool);
  }
  return out;
}

export interface PersonaExpansion {
  /** Effective allowed_tools to pass to the child loop. `undefined` = inherit all parent tools. */
  allowedTools: string[] | undefined;
  /** Effective allowed_skills allowlist. `undefined` = no skill-tag filter. */
  allowedSkills: string[] | undefined;
  /** Task prompt — preset system_prompt prepended to the caller's prompt when applicable. */
  prompt: string;
  /** True when the persona name matched a catalog entry. */
  matched: boolean;
  /** Persona-level default completion contract. Caller input wins when present. */
  defaultCompletionContract?: PersonaCompletionContract;
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
    allowedTools = withDefaultChildBrowserTools(spec.allowed_tools);
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
    ...(spec.completion_contract
      ? { defaultCompletionContract: { ...spec.completion_contract } }
      : {}),
  };
}

// ── Tournament adapter ─────────────────────────────────────────────
// Wires ctx/agent/scorer into the generic spawn/Tournament core.

async function runTournamentAdapter(
  input: SpawnAgentInput,
  ctx: ToolContext,
  agent: Agent,
  baseChildOptions: Omit<SpawnChildOptions, "spawnDir" | "spawnWorkspace" | "taskId">,
): Promise<TournamentResult & { winnerWorktreeApply?: SpawnAgentOutput["winnerWorktreeApply"] }> {
  const variants = input.variants as number;
  const scorer = input.scorer as TournamentScorer;
  const useGitWorktree = baseChildOptions.workspacePolicy === "git_worktree";
  return runTournamentCore({
    variants,
    concurrency: input.concurrency,
    cleanup_losers: input.cleanup_losers,
    exposeWinnerWorktreeApply: useGitWorktree,
    ctx: {
      workspaceRoot: ctx.workspaceRoot,
      turnId: ctx.turnId,
      stageAuditEvent: ctx.staging.stageAuditEvent,
      emitAgentEvent: ctx.emitAgentEvent,
    },
    prepareSpawnDir: useGitWorktree ? prepareGitWorktreeSpawnDir : prepareSpawnDir,
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

function isMissingRequiredFilesContractFailure(result: SpawnChildResult): boolean {
  return (
    result.status === "error" &&
    typeof result.errorMessage === "string" &&
    result.errorMessage.startsWith(
      "child missing required files for SpawnAgent completion_contract:",
    )
  );
}

function normalizeCompletionContract(
  input: SpawnAgentInput,
  defaultContract?: PersonaCompletionContract,
): SpawnCompletionContract & {
  required_evidence: SpawnEvidenceRequirement;
} {
  const source = input.completion_contract ?? defaultContract;
  const requiredEvidence = source?.required_evidence ?? "tool_call";
  return {
    required_evidence: requiredEvidence,
    ...(source?.required_files
      ? { required_files: source.required_files }
      : {}),
    ...(source?.require_non_empty_result !== undefined
      ? { require_non_empty_result: source.require_non_empty_result }
      : {}),
    ...(source?.reason
      ? { reason: source.reason }
      : {}),
  };
}

function spawnErrorMessage(result: SpawnChildResult): string {
  const raw = result.errorMessage || result.finalText || result.status;
  return compactTextForParentReplay(raw, 1_200).summary;
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

function activeDeterministicRequirements(
  childOptions: SpawnChildOptionsImpl,
): string[] {
  const snapshot = childOptions.executionContract?.snapshot();
  if (!snapshot) return [];
  return snapshot.taskState.deterministicRequirements
    .filter((requirement) => requirement.status === "active")
    .map((requirement) => requirement.requirementId);
}

function hasParentManagedArtifactEvidence(result: SpawnChildResult): boolean {
  return result.evidence?.toolNames.includes("ArtifactCreate") === true;
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
  const missingDeterministic = activeDeterministicRequirements(childOptions);
  if (missingDeterministic.length > 0) {
    return completionContractError(
      result,
      [
        "child returned without satisfying deterministic evidence required by the parent execution contract",
        `active deterministic requirement(s): ${missingDeterministic.join(", ")}`,
      ].join("; "),
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
      if (artifactFileCount > 0 || hasParentManagedArtifactEvidence(result)) {
        return result;
      }
      const calledArtifactCreate =
        result.evidence?.toolNames.includes("ArtifactCreate") === true;
      const diagnostic = calledArtifactCreate
        ? "child called ArtifactCreate, but no child-local artifact files were found under spawnDir/artifacts; the artifact may have been parent-managed or the child returned text only"
        : "child did not call ArtifactCreate";
      return completionContractError(
        result,
        [
          "child produced no artifact evidence required by SpawnAgent completion_contract",
          diagnostic,
          'completion_contract required artifact, but child returned text only. Use required_evidence:"text" for direct memo return, or instruct child to call ArtifactCreate.',
        ].join("; "),
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

function compactTextForParentReplay(text: string, limit = SPAWN_PARENT_SUMMARY_LIMIT): {
  summary: string;
  omitted: boolean;
} {
  if (text.length <= limit) return { summary: text, omitted: false };
  return {
    summary: `${text.slice(0, limit).trimEnd()}\n[truncated: ${text.length - limit} chars omitted]`,
    omitted: true,
  };
}

function compactArtifactsForParentReplay(artifacts?: SpawnArtifacts): {
  handedOffArtifacts: Array<{
    artifactId: string;
    kind: string;
    title: string;
    slug: string;
  }>;
} | undefined {
  if (!artifacts || artifacts.handedOffArtifacts.length === 0) return undefined;
  return {
    handedOffArtifacts: artifacts.handedOffArtifacts.map((artifact) => ({
      artifactId: artifact.artifactId,
      kind: artifact.kind,
      title: artifact.title,
      slug: artifact.slug,
    })),
  };
}

function compactSpawnResultForParent(
  output: SpawnAgentOutput,
  durationMs: number,
): string {
  const compact = compactTextForParentReplay(
    output.errorMessage ?? output.finalText ?? "",
  );
  return JSON.stringify({
    taskId: output.taskId,
    status: output.status,
    ...(compact.summary ? { summary: compact.summary } : {}),
    ...(output.errorMessage ? { errorMessage: output.errorMessage } : {}),
    ...(typeof output.toolCallCount === "number"
      ? { toolCallCount: output.toolCallCount }
      : {}),
    ...(typeof output.attempts === "number" ? { attempts: output.attempts } : {}),
    durationMs,
    ...(output.artifacts
      ? {
          artifacts: compactArtifactsForParentReplay(output.artifacts) ?? {
            handedOffArtifacts: [],
          },
        }
      : {}),
    fullTextOmitted: compact.omitted,
  });
}

function spawnFailureResult(
  taskId: string,
  result: SpawnChildResult,
  attempts: number,
  start: number,
  artifacts?: SpawnArtifacts,
): ToolResult<SpawnAgentOutput> {
  const errorMessage = buildSpawnFailureMessage(taskId, result, attempts);
  const durationMs = Date.now() - start;
  const output: SpawnAgentOutput = {
    taskId,
    status: result.status === "aborted" ? "aborted" : "error",
    finalText: result.finalText,
    toolCallCount: result.toolCallCount,
    childEvidence: result.evidence,
    attempts,
    errorMessage,
    ...(artifacts ? { artifacts } : {}),
  };
  return {
    status: result.status === "aborted" ? "aborted" : "error",
    errorCode: result.status === "aborted" ? "spawn_aborted" : "spawn_failed",
    errorMessage,
    output,
    llmOutput: compactSpawnResultForParent(output, durationMs),
    durationMs,
  };
}

function subagentResultTurnId(parentTurnId: string, taskId: string): string {
  return `${parentTurnId}::spawn::${taskId}`;
}

function sourceSnippet(value: string, maxLength = SPAWN_RESULT_SOURCE_SNIPPET_MAX): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, Math.max(0, maxLength - 3)).trimEnd()}...`;
}

function recordSubagentResultSource(args: {
  ctx: ToolContext;
  input: SpawnAgentInput;
  taskId: string;
  result: SpawnChildResult;
  attempts: number;
}): void {
  const { ctx, input, taskId, result, attempts } = args;
  if (result.status !== "ok") return;
  if (!ctx.sourceLedger) return;

  const snippet = sourceSnippet(result.finalText);
  const source = ctx.sourceLedger.recordSource({
    turnId: subagentResultTurnId(ctx.turnId, taskId),
    toolName: "SpawnAgent",
    kind: "subagent_result",
    uri: `spawn://${taskId}`,
    title: `${input.persona} subagent result`,
    ...(snippet ? { snippets: [snippet] } : {}),
    metadata: {
      taskId,
      persona: input.persona,
      deliver: input.deliver,
      toolCallCount: result.toolCallCount,
      attempts,
    },
  });
  ctx.emitAgentEvent?.({ type: "source_inspected", source });
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
      ...("evidence" in candidate ? { evidence: candidate.evidence } : {}),
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
      (result.toolCallCount === 0 || isMissingRequiredFilesContractFailure(result)) &&
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
    input.workspace_policy !== "isolated" &&
    input.workspace_policy !== "git_worktree"
  ) {
    return "`workspace_policy` must be 'trusted', 'isolated', or 'git_worktree'";
  }
  if (
    input.allowed_files !== undefined &&
    (!Array.isArray(input.allowed_files) ||
      input.allowed_files.some((x) => typeof x !== "string" || x.length === 0))
  ) {
    return "`allowed_files` must be an array of non-empty strings";
  }
  if (
    input.write_set !== undefined &&
    (!Array.isArray(input.write_set) ||
      input.write_set.some((x) => typeof x !== "string" || x.length === 0))
  ) {
    return "`write_set` must be an array of non-empty strings";
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

function normalizeSpawnTimeoutMs(input: SpawnAgentInput): number {
  const isBackground = input.deliver === "background";
  const defaultTimeoutMs = isBackground ? DEFAULT_BACKGROUND_TIMEOUT_MS : DEFAULT_RETURN_TIMEOUT_MS;
  const maxTimeoutMs = isBackground ? MAX_BACKGROUND_TIMEOUT_MS : MAX_RETURN_TIMEOUT_MS;
  return Math.min(
    maxTimeoutMs,
    Math.max(1_000, input.timeout_ms ?? defaultTimeoutMs),
  );
}

function shouldDefaultCodingSpawnToGitWorktree(
  input: SpawnAgentInput,
  expanded: PersonaExpansion,
): boolean {
  if (input.workspace_policy !== undefined || input.mode === "tournament") {
    return false;
  }
  if (input.write_set !== undefined && input.write_set.length > 0) {
    return true;
  }
  if (CODING_PERSONA_PATTERN.test(input.persona)) {
    return true;
  }
  const allowedTools = expanded.allowedTools ?? input.allowed_tools ?? [];
  return allowedTools.some((tool) => CODING_TOOL_HINTS.has(tool));
}

async function resolveSpawnWorkspacePolicy(
  input: SpawnAgentInput,
  expanded: PersonaExpansion,
  workspaceRoot: string,
): Promise<SpawnWorkspacePolicy> {
  if (input.workspace_policy !== undefined) {
    return input.workspace_policy;
  }
  if (!shouldDefaultCodingSpawnToGitWorktree(input, expanded)) {
    return "trusted";
  }
  return (await canPrepareGitWorktreeSpawnDir(workspaceRoot))
    ? "git_worktree"
    : "trusted";
}

function resolveParentPermissionMode(agent: Agent, sessionKey: string): PermissionMode {
  const getSession = (agent as {
    getSession?: (key: string) => { getPermissionMode?: () => PermissionMode } | undefined;
  }).getSession;
  if (!getSession) return "default";
  const session = getSession.call(agent, sessionKey);
  return session?.getPermissionMode ? session.getPermissionMode() : "default";
}

function truncateParentTurnContext(value: string, limit: number): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit).trimEnd()}\n[truncated: ${value.length - limit} chars omitted]`;
}

function renderAttachmentForChild(attachment: MessageAttachment): string {
  const parts = [
    attachment.kind,
    attachment.name ? `name=${attachment.name}` : "",
    attachment.mimeType ? `mime=${attachment.mimeType}` : "",
    typeof attachment.sizeBytes === "number" ? `bytes=${attachment.sizeBytes}` : "",
    attachment.localPath ? `workspace_path=${attachment.localPath}` : "",
    attachment.url ? `url=${attachment.url}` : "",
  ].filter((part) => part.length > 0);
  return `- ${parts.join(" ")}`;
}

function buildParentTurnContextBlock(message?: UserMessage): string {
  if (!message) return "";
  const blocks: string[] = [];
  const text = message.text.trim();
  if (text.length > 0) {
    blocks.push([
      "<current_user_message>",
      truncateParentTurnContext(text, PARENT_TURN_CONTEXT_TEXT_LIMIT),
      "</current_user_message>",
    ].join("\n"));
  }

  const systemPromptAddendum =
    typeof message.metadata?.systemPromptAddendum === "string"
      ? message.metadata.systemPromptAddendum.trim()
      : "";
  if (systemPromptAddendum.length > 0) {
    blocks.push([
      "<system_context_addendum>",
      truncateParentTurnContext(
        systemPromptAddendum,
        PARENT_TURN_CONTEXT_SYSTEM_LIMIT,
      ),
      "</system_context_addendum>",
    ].join("\n"));
  }

  if (message.attachments && message.attachments.length > 0) {
    blocks.push([
      "<attachments>",
      ...message.attachments.map((attachment) => renderAttachmentForChild(attachment)),
      "</attachments>",
    ].join("\n"));
  }

  if (message.imageBlocks && message.imageBlocks.length > 0) {
    blocks.push(`<image_blocks count="${message.imageBlocks.length}" inherited="false" />`);
  }

  if (blocks.length === 0) return "";
  return [
    '<parent_turn_context source="runtime-current-turn">',
    "The parent turn included this context. Use it when the delegated prompt refers to selected files, attachments, KB context, this document, or the current request.",
    ...blocks,
    "</parent_turn_context>",
  ].join("\n");
}

export interface SpawnAgentMissionDeps {
  missionClient?: Pick<MissionClient, "createMission" | "createRun" | "appendEvent"> &
    Partial<Pick<MissionClient, "createArtifact">>;
  getSourceChannel?: (ctx: ToolContext) => ChannelRef | null;
  missionsEnabled?: () => boolean;
}

interface SpawnMissionLink {
  missionId?: string;
  missionRunId?: string;
}

function isMissionsEnabled(deps: SpawnAgentMissionDeps | undefined): boolean {
  return deps?.missionsEnabled?.() ?? process.env.CORE_AGENT_MISSIONS === "1";
}

function metadataString(
  metadata: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const value = metadata?.[key];
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function truncateMissionText(value: string, limit: number): string {
  return value.length <= limit ? value : value.slice(0, limit - 1).trimEnd();
}

function resolveMissionKind(metadata: Record<string, unknown> | undefined): MissionKind {
  const raw = metadataString(metadata, "missionKind");
  return raw && MISSION_KINDS.has(raw as MissionKind) ? (raw as MissionKind) : "spawn";
}

function resolveMissionTitle(input: SpawnAgentInput): string {
  return truncateMissionText(
    metadataString(input.metadata, "missionTitle") ?? `${input.persona} handoff`,
    240,
  );
}

function resolveMissionArtifactCategory(
  metadata: Record<string, unknown> | undefined,
): string | undefined {
  const raw = metadataString(metadata, "evidenceCategory") ?? metadataString(metadata, "category");
  if (raw && MISSION_ARTIFACT_CATEGORIES.has(raw)) return raw;
  if (metadataString(metadata, "synthesisId")) return "parallel_synthesis";
  if (metadataString(metadata, "parallelGroup")) return "parallel_research_evidence";
  return undefined;
}

function missionArtifactMetadata(args: {
  input: SpawnAgentInput;
  taskId: string;
  result: SpawnChildResult;
  attempts: number;
  fileCount: number;
  handedOffArtifacts: SpawnHandoffArtifact[];
}): Record<string, unknown> {
  const { input, taskId, result, attempts, fileCount, handedOffArtifacts } = args;
  const category = resolveMissionArtifactCategory(input.metadata);
  const sourceId = metadataString(input.metadata, "sourceId") ?? `spawn:${taskId}`;
  const parallelGroup = metadataString(input.metadata, "parallelGroup");
  const synthesisId = metadataString(input.metadata, "synthesisId");
  return {
    taskId,
    persona: input.persona,
    status: result.status,
    toolCallCount: result.toolCallCount,
    attempts,
    fileCount,
    artifactCount: handedOffArtifacts.length,
    sourceId,
    ...(category ? { category } : {}),
    ...(parallelGroup ? { parallelGroup } : {}),
    ...(synthesisId ? { synthesisId } : {}),
  };
}

function missionRunId(run: Record<string, unknown>): string | undefined {
  return typeof run.id === "string" ? run.id : undefined;
}

async function createBackgroundSpawnMission(args: {
  deps?: SpawnAgentMissionDeps;
  input: SpawnAgentInput;
  ctx: ToolContext;
  taskId: string;
}): Promise<SpawnMissionLink> {
  const { deps, input, ctx, taskId } = args;
  if (!deps?.missionClient || !isMissionsEnabled(deps)) return {};

  let missionId = metadataString(input.metadata, "missionId");
  let missionRunIdValue = metadataString(input.metadata, "missionRunId");

  if (!missionId) {
    const channel = deps.getSourceChannel?.(ctx) ?? null;
    if (!channel) return {};
    try {
      const mission = await deps.missionClient.createMission({
        channelType: channel.type as MissionChannelType,
        channelId: channel.channelId,
        kind: resolveMissionKind(input.metadata),
        title: resolveMissionTitle(input),
        summary: truncateMissionText(input.prompt, 500),
        status: "running",
        createdBy: "agent",
        metadata: {
          ...(input.metadata ?? {}),
          spawnTaskId: taskId,
          persona: input.persona,
        },
      });
      missionId = mission.id;
      ctx.emitAgentEvent?.({ type: "mission_created", mission });
    } catch (err) {
      console.warn(
        `[core-agent] SpawnAgent mission create failed taskId=${taskId}: ${(err as Error).message}`,
      );
      return {};
    }
  }

  if (missionId && !missionRunIdValue) {
    try {
      const run = await deps.missionClient.createRun(missionId, {
        triggerType: "handoff",
        status: "running",
        sessionKey: ctx.sessionKey,
        turnId: ctx.turnId,
        spawnTaskId: taskId,
        metadata: {
          persona: input.persona,
          missionKind: resolveMissionKind(input.metadata),
        },
      });
      missionRunIdValue = missionRunId(run);
    } catch (err) {
      console.warn(
        `[core-agent] SpawnAgent mission run create failed taskId=${taskId} missionId=${missionId}: ${(err as Error).message}`,
      );
    }
  }

  if (missionId) {
    try {
      await deps.missionClient.appendEvent(missionId, {
        ...(missionRunIdValue ? { runId: missionRunIdValue } : {}),
        actorType: "agent",
        eventType: "evidence",
        message: "Child agent spawned",
        payload: {
          category: "child_spawned",
          taskId,
          spawnTaskId: taskId,
          persona: input.persona,
          missionKind: resolveMissionKind(input.metadata),
        },
      });
      ctx.emitAgentEvent?.({
        type: "mission_event",
        missionId,
        eventType: "evidence",
        message: "Child agent spawned",
      });
    } catch (err) {
      console.warn(
        `[core-agent] SpawnAgent child-spawn mission event failed taskId=${taskId} missionId=${missionId}: ${(err as Error).message}`,
      );
    }
  }

  return {
    ...(missionId ? { missionId } : {}),
    ...(missionRunIdValue ? { missionRunId: missionRunIdValue } : {}),
  };
}

async function createBackgroundSpawnMissionArtifact(args: {
  deps?: SpawnAgentMissionDeps;
  link: SpawnMissionLink;
  input: SpawnAgentInput;
  taskId: string;
  result: SpawnChildResult;
  attempts: number;
  fileCount: number;
  handedOffArtifacts: SpawnHandoffArtifact[];
}): Promise<void> {
  const {
    deps,
    link,
    input,
    taskId,
    result,
    attempts,
    fileCount,
    handedOffArtifacts,
  } = args;
  if (!deps?.missionClient?.createArtifact || !link.missionId) return;
  const finalText = result.finalText.trim();
  if (!finalText) return;
  try {
    await deps.missionClient.createArtifact(link.missionId, {
      ...(link.missionRunId ? { runId: link.missionRunId } : {}),
      kind: "subagent_output",
      title: truncateMissionText(`${input.persona} result`, 240),
      preview: truncateMissionText(finalText, 2000),
      metadata: missionArtifactMetadata({
        input,
        taskId,
        result,
        attempts,
        fileCount,
        handedOffArtifacts,
      }),
    });
  } catch (err) {
    console.warn(
      `[core-agent] SpawnAgent mission artifact create failed missionId=${link.missionId}: ${(err as Error).message}`,
    );
  }
}

async function appendBackgroundSpawnMissionEvent(args: {
  deps?: SpawnAgentMissionDeps;
  ctx: ToolContext;
  link: SpawnMissionLink;
  eventType: "completed" | "failed" | "cancelled";
  message?: string;
  payload?: Record<string, unknown>;
}): Promise<void> {
  const { deps, ctx, link, eventType, message, payload } = args;
  if (!deps?.missionClient || !link.missionId) return;
  try {
    await deps.missionClient.appendEvent(link.missionId, {
      ...(link.missionRunId ? { runId: link.missionRunId } : {}),
      actorType: "agent",
      eventType,
      ...(message ? { message } : {}),
      payload: payload ?? {},
    });
    ctx.emitAgentEvent?.({
      type: "mission_event",
      missionId: link.missionId,
      eventType,
      ...(message ? { message } : {}),
    });
  } catch (err) {
    console.warn(
      `[core-agent] SpawnAgent mission event append failed missionId=${link.missionId}: ${(err as Error).message}`,
    );
  }
}

// ── Tool factory ───────────────────────────────────────────────────

/**
 * Build the SpawnAgent tool, bound to a specific Agent. The Agent
 * reference gives us access to the shared LLMClient + ToolRegistry.
 */
export function makeSpawnAgentTool(
  agent: Agent,
  backgroundRegistry?: BackgroundTaskRegistry,
  missionDeps?: SpawnAgentMissionDeps,
): Tool<SpawnAgentInput, SpawnAgentOutput> {
  return {
    name: "SpawnAgent",
    description:
      "Delegate a focused sub-task to a child agent with a custom persona and filtered toolset. Omit `workspace_policy` for automatic policy: coding-oriented children (for example persona `coder`, coding persona names, `PatchApply`/`FileEdit`/diagnostic tool hints, or a non-empty `write_set`) default to a detached git worktree when the parent workspace can create one; other children remain trusted workers that can read/write the parent workspace and use `.spawn/{taskId}` for scratch/audit storage. Set `workspace_policy:\"trusted\"` only when a coding child must operate directly on the current parent checkout, `workspace_policy:\"git_worktree\"` when detached worktree isolation is required, or `workspace_policy:\"isolated\"` when the task should be sandboxed to `.spawn/{taskId}`. After a git_worktree child finishes, use SpawnWorktreeApply to preview, apply, or reject the child worktree changes; do not copy files manually. In tournament mode, explicit `workspace_policy:\"git_worktree\"` runs each variant in its own detached git worktree and returns `winnerWorktreeApply` as the selected variant's SpawnWorktreeApply preview handoff. If SpawnWorktreeApply returns `conflictReview.resolverSpawn`, pass that recipe to SpawnAgent unchanged so persona `conflict_resolver` can repair only the conflicted `write_set` in the parent checkout before GitDiff/TestRun review. The runtime automatically includes the current turn's selected KB/system addendum and attachment manifest in the child work order; still provide concrete task instructions, required files, expected outputs, allowed tools, completion criteria, and retry/idempotency guidance. `deliver:\"return\"` blocks until the child finishes and returns final text. `deliver:\"background\"` returns a taskId immediately; completion surfaces as a spawn_result event and TaskGet record. Use background delivery for long browser QA, research, or artifact generation; it defaults to a 3 hour timeout and accepts `timeout_ms` up to 6 hours. Spawn depth is capped at 2. Use `completion_contract.required_evidence:\"files\"` plus `required_files` for durable file deliverables, `\"text\"` for answer-only work, `\"artifact\"` for artifact handoff, `\"tool_call\"` for concrete tool work, or `\"none\"` only when no evidence is expected. Use `model` only when deliberately selecting a child model; copy an exact value from this schema enum or omit `model` to inherit the bot's runtime model. Never invent provider/model ids. IMPORTANT: child output longer than ~500 chars should go into ArtifactCreate rather than finalText; child-produced artifacts are imported into the parent workspace and returned on `artifacts.handedOffArtifacts`.",
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
      const timeoutMs = normalizeSpawnTimeoutMs(input);

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
      const completionContract = normalizeCompletionContract(
        input,
        expanded.defaultCompletionContract,
      );
      const getSession = (agent as {
        getSession?: (sessionKey: string) => { executionContract?: ExecutionContractStore } | undefined;
      }).getSession;
      const parentSession = typeof getSession === "function"
        ? getSession.call(agent, ctx.sessionKey)
        : undefined;
      const parentExecutionContract = ctx.executionContract ?? parentSession?.executionContract;
      const parentTurnContext = buildParentTurnContextBlock(ctx.currentUserMessage);
      const expandedPromptWithContext = parentTurnContext
        ? `${parentTurnContext}\n\n${expanded.prompt}`
        : expanded.prompt;
      const workspacePolicy = await resolveSpawnWorkspacePolicy(
        input,
        expanded,
        ctx.workspaceRoot,
      );
      const childPrompt = parentExecutionContract
        ? buildSpawnWorkOrderPrompt({
            parent: parentExecutionContract.snapshot(),
            childPrompt: expandedPromptWithContext,
            persona: input.persona,
            allowedTools: expanded.allowedTools,
          })
        : expandedPromptWithContext;
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
        workspacePolicy,
        deferralCheck:
          input.mode === "tournament"
            ? false
            : completionContract.required_evidence !== "none",
        memoryMode: ctx.memoryMode,
        allowedFiles: input.allowed_files,
        writeSet: input.write_set,
        onAgentEvent: ctx.emitAgentEvent,
        askUser: ctx.askUser,
        permissionMode: resolveParentPermissionMode(agent, ctx.sessionKey),
        executionContract: parentExecutionContract,
        sourceLedger: ctx.sourceLedger,
        ...(modelOverride ? { modelOverride } : {}),
        ...(ctx.traceId ? { traceId: ctx.traceId } : {}),
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
          const durationMs = Date.now() - start;
          const output: SpawnAgentOutput = {
            taskId,
            status: "ok",
            finalText: winner?.finalText ?? "",
            mode: "tournament",
            winnerIndex: tourney.winnerIndex,
            variants: tourney.variants,
            ...(tourney.winnerWorktreeApply ? { winnerWorktreeApply: tourney.winnerWorktreeApply } : {}),
            ...(allHandedOff.length > 0
              ? {
                  artifacts: {
                    spawnDir: path.join(ctx.workspaceRoot, ".spawn"),
                    fileCount: 0,
                    handedOffArtifacts: allHandedOff,
                  },
                }
              : {}),
          };
          return {
            status: "ok",
            output,
            llmOutput: compactSpawnResultForParent(output, durationMs),
            durationMs,
          };
        } catch (err) {
          return errorResult(err, start);
        }
      }

      // Single spawn — allocate ephemeral subdir before launching.
      const prepared = await (
        workspacePolicy === "git_worktree"
          ? prepareGitWorktreeSpawnDir(ctx.workspaceRoot, taskId)
          : prepareSpawnDir(ctx.workspaceRoot, taskId)
      ).catch((err) => err);
      if (prepared instanceof Error) {
        return errorResult(prepared, start);
      }
      const { spawnDir, spawnWorkspace } = prepared;
      const delegatedDetail = summariseDelegatedPrompt(input.prompt);

      ctx.emitAgentEvent?.({
        type: "spawn_started",
        taskId,
        parentTurnId: ctx.turnId,
        persona: input.persona,
        prompt: input.prompt,
        ...(delegatedDetail ? { detail: delegatedDetail } : {}),
        deliver: input.deliver,
        ...(modelOverride ? { model: modelOverride } : {}),
        timeoutMs,
        completionContract,
      });
      ctx.emitAgentEvent?.({ type: "spawn_dir_created", taskId, spawnDir });
      ctx.staging.stageAuditEvent("spawn_dir_created", { taskId, spawnDir });
      const lifecycle = createChildAgentHarness({
        taskId,
        parentTurnId: ctx.turnId,
        prompt: input.prompt,
        ...(delegatedDetail ? { detail: delegatedDetail } : {}),
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
            ctx.emitAgentEvent?.({
              type: result.status === "aborted" ? "spawn_aborted" : "spawn_failed",
              taskId,
              parentTurnId: ctx.turnId,
              deliver: input.deliver,
              attempts: childRun.attempts,
              durationMs: Date.now() - start,
              completionContract,
              errorMessage: spawnErrorMessage(result),
            });
            return spawnFailureResult(
              taskId,
              result,
              childRun.attempts,
              start,
              artifacts,
            );
          }
          recordSubagentResultSource({
            ctx,
            input,
            taskId,
            result,
            attempts: childRun.attempts,
          });
          const durationMs = Date.now() - start;
          const output: SpawnAgentOutput = {
            taskId,
            status: result.status,
            finalText: result.finalText,
            toolCallCount: result.toolCallCount,
            childEvidence: result.evidence,
            attempts: childRun.attempts,
            artifacts,
          };
          ctx.emitAgentEvent?.({
            type: "spawn_completed",
            taskId,
            parentTurnId: ctx.turnId,
            deliver: input.deliver,
            ...(modelOverride ? { model: modelOverride } : {}),
            timeoutMs,
            attempts: childRun.attempts,
            durationMs,
            completionContract,
            toolCallCount: result.toolCallCount,
          });
          return {
            status: "ok",
            output,
            llmOutput: compactSpawnResultForParent(output, durationMs),
            durationMs,
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
        missionDeps,
        input,
        completionContract,
      });
      const durationMs = Date.now() - start;
      const output: SpawnAgentOutput = { taskId, status: "pending" };
      return {
        status: "ok",
        output,
        llmOutput: compactSpawnResultForParent(output, durationMs),
        durationMs,
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
  missionDeps?: SpawnAgentMissionDeps;
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
    missionDeps,
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
  const missionLink = await createBackgroundSpawnMission({
    deps: missionDeps,
    input,
    ctx,
    taskId,
  });

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
        ...(missionLink.missionId ? { missionId: missionLink.missionId } : {}),
        ...(missionLink.missionRunId
          ? { missionRunId: missionLink.missionRunId }
          : {}),
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
      recordSubagentResultSource({
        ctx,
        input,
        taskId,
        result,
        attempts: childRun.attempts,
      });
      emit?.({
        type: "spawn_result",
        taskId,
        status: result.status,
        finalText: result.finalText,
        toolCallCount: result.toolCallCount,
        childEvidence: result.evidence,
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
      if (backgroundRegistry) {
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
      await createBackgroundSpawnMissionArtifact({
        deps: missionDeps,
        link: missionLink,
        input,
        taskId,
        result,
        attempts: childRun.attempts,
        fileCount,
        handedOffArtifacts,
      });
      const eventType =
        result.status === "ok"
          ? "completed"
          : result.status === "aborted"
            ? "cancelled"
            : "failed";
      await appendBackgroundSpawnMissionEvent({
        deps: missionDeps,
        ctx,
        link: missionLink,
        eventType,
        ...(errorMessage ? { message: errorMessage } : {}),
        payload: {
          category: "child_result",
          taskId,
          status: result.status,
          toolCallCount: result.toolCallCount,
          attempts: childRun.attempts,
          artifactCount: handedOffArtifacts.length,
          fileCount,
        },
      });
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
      await appendBackgroundSpawnMissionEvent({
        deps: missionDeps,
        ctx,
        link: missionLink,
        eventType: "failed",
        message: msg,
        payload: { category: "child_result", taskId, status: "error" },
      });
    })
    .finally(() => {
      ctx.abortSignal.removeEventListener("abort", onParentAbortBg);
    });
}

/** Re-export for tests. */
export { IToolRegistry };
