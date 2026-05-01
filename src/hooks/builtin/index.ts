/**
 * Built-in hooks shipped with core-agent. Agent.start() registers
 * these unless the bot disables them via
 * `workspace/agent.config.yaml: disable_builtin_hooks: [...]`.
 *
 * Design reference: §7.12 + §8 (the 5 AEF modules map).
 */

import type { HookRegistry } from "../HookRegistry.js";
import { selfClaimVerifierHook } from "./selfClaimVerifier.js";
import {
  makeFactGroundingVerifierHook,
  type FactGroundingAgent,
} from "./factGroundingVerifier.js";
import {
  makeResourceExistenceCheckerHook,
  type ResourceCheckAgent,
} from "./resourceExistenceChecker.js";
import { makeProviderHealthVerifierHook } from "./providerHealthVerifier.js";
import {
  makeCompletionEvidenceGateHook,
  type CompletionEvidenceAgent,
} from "./completionEvidenceGate.js";
import { makeReliabilityPromptInjectorHook } from "./reliabilityPromptInjector.js";
import { makeOutputPurityGateHook } from "./outputPurityGate.js";
import { makeSecretExposureGateHook } from "./secretExposureGate.js";
import { makeTaskContractGateHook } from "./taskContractGate.js";
import { makeTaskBoardCompletionGateHook } from "./taskBoardCompletionGate.js";
import { makeArtifactDeliveryGateHook } from "./artifactDeliveryGate.js";
import { makeCronDeliverySafetyHook } from "./cronDeliverySafety.js";
import { subSessionIdentityHook } from "./subSessionIdentity.js";
import { citationGateHook } from "./citationGate.js";
import { sessionCommitmentTrackerHook } from "./sessionCommitmentTracker.js";
import { makeHipocampusCheckpointHook } from "./hipocampusCheckpoint.js";
import { makeHipocampusCompactorHook } from "./hipocampusCompactor.js";
import { makeHipocampusFlushHook } from "./hipocampusFlush.js";
import type { CompactionEngine as CompactionEngineType } from "../../services/memory/CompactionEngine.js";
import type { QmdManager as QmdManagerType } from "../../services/memory/QmdManager.js";
import type { HipocampusService } from "../../services/memory/HipocampusService.js";
import { answerVerifierHook } from "./answerVerifier.js";
import { makeMemoryInjectorHook } from "./memoryInjector.js";
import { agentSelfModelHook } from "./agentSelfModel.js";
import { makeWorkspaceAwarenessHook } from "./workspaceAwarenessInjector.js";
import {
  makePreRefusalVerifierHook,
  type PreRefusalVerifierAgent,
} from "./preRefusalVerifier.js";
import {
  makeDeferralBlockerHook,
  type DeferralBlockerAgent,
} from "./deferralBlocker.js";
import {
  makeOutputDeliveryGateHook,
  type OutputDeliveryGateAgent,
} from "./outputDeliveryGate.js";
import {
  makeSessionResumeHook,
  type SessionResumeAgent,
} from "./sessionResume.js";
import {
  makeMidTurnInjectorHook,
  type MidTurnInjectorAgent,
} from "./midTurnInjector.js";
import {
  makeInlineTaskNotifierHook,
  type TaskNotifierAgent,
} from "./inlineTaskNotifier.js";
import { makeSealedFilesHooks } from "./sealedFiles.js";
import { makeRepeatedFailureGuardHook } from "./repeatedFailureGuard.js";
import { makeIterationStateSweeperHook } from "./iterationStateSweeper.js";
import { makeStopConditionsHook } from "./stopConditions.js";
import { makeBenchmarkVerifierHook } from "./benchmarkVerifier.js";
import { makeDangerousPatternsHook } from "./dangerousPatterns.js";
import {
  makeAutoApprovalHook,
  type AutoApprovalAgent,
} from "./autoApproval.js";
import {
  makeDisciplineBeforeToolUseHook,
  makeDisciplineAfterTurnEndHook,
  type DisciplineAgent,
  type DisciplineSessionCounter,
} from "./disciplineHook.js";
import { makeClassifyTurnModeHook } from "./classifyTurnMode.js";
import { makeDisciplinePromptBlockHook } from "./disciplinePromptBlock.js";
import {
  makePlanModeAutoTriggerHook,
  type PlanModeAutoTriggerAgent,
} from "./planModeAutoTrigger.js";
import {
  makeOnboardingNeededCheckHook,
  type OnboardingNeededCheckAgent,
} from "./onboardingNeededCheck.js";
import { makeTaskLifecycleHook } from "./taskLifecycle.js";
import type { Discipline } from "../../Session.js";
import type { PolicyKernel as PolicyKernelType } from "../../policy/PolicyKernel.js";
import { makePolicyPromptBlockHook } from "./policyPromptBlock.js";
import {
  makeUserHarnessRuleHooks,
  type UserHarnessRuleAgent,
} from "./userHarnessRules.js";
import {
  makeExecutionContractPromptHook,
  makeExecutionContractVerifierHook,
} from "./executionContract.js";
import {
  makeResourceBoundaryHooks,
  type ResourceBoundaryAgent,
} from "./resourceBoundaryGate.js";
import type { DebugWorkflow as DebugWorkflowType } from "../../debug/DebugWorkflow.js";
import { makeDebugTurnClassifierHook } from "./debugTurnClassifier.js";
import {
  fileDeliveryInterceptor,
  type FileDeliveryInterceptorOptions,
} from "./fileDeliveryInterceptor.js";
import { makeDebugInvestigationGuardHook } from "./debugInvestigationGuard.js";
import {
  makeDebugAfterToolCheckpointHook,
  makeDebugCommitCheckpointHook,
} from "./debugCheckpointRecorder.js";

export interface RegisterBuiltinsOpts {
  disabled?: string[];
  workspaceRoot: string;
  sessionsDir?: string;
  /**
   * Delegate used by the auto-approval hook (T2-08) to look up the
   * active session's permissionMode and the tool registry. Optional
   * so unit-test callers that don't exercise auto-approval don't need
   * to construct it; when omitted the hook is skipped.
   */
  autoApprovalAgent?: AutoApprovalAgent;
  /**
   * Delegate used by the Coding Discipline hooks (classifyTurnMode,
   * disciplinePromptBlock, discipline beforeToolUse / afterTurnEnd) to
   * reach Session.meta.discipline and per-session mutation counters.
   * When omitted the hooks are skipped — tests that don't exercise
   * discipline don't need to construct it.
   */
  disciplineAgent?: DisciplineAgent & {
    setSessionDiscipline(sessionKey: string, next: Discipline): void;
    getSessionCounter(sessionKey: string): DisciplineSessionCounter;
    /**
     * Kevin's A/A/A rule #1 — when returns true, the classifier hook
     * promotes `requireCommit` from soft → hard for coding-labeled
     * turns. Wired off `ToolRegistry.resolve("coding-agent")`.
     */
    isCodingAgentSkillActive?(): boolean;
  };
  /**
   * Delegate used by the mid-turn injector hook (#86) to look up the
   * Session by its key and drain its pendingInjections queue. Omitted
   * in unit tests that don't exercise injection.
   */
  midTurnInjectorAgent?: MidTurnInjectorAgent;
  /**
   * Delegate used by the inline task notification hook (#81) to drain
   * per-session TaskNotification queues from BackgroundTaskRegistry.
   * Omitted in unit tests — the hook is skipped when not wired.
   */
  taskNotifierAgent?: TaskNotifierAgent;
  /**
   * Delegate used by the session-resume seed hook (self-model Layer 4)
   * to read the prior committed transcript and append the assembled
   * `<session_resume>` block to the session's next-turn system
   * prompt. Omitted in unit tests — the hook is skipped when not
   * wired.
   */
  sessionResumeAgent?: SessionResumeAgent;
  /**
   * Delegate used by the plan-mode auto-trigger hook
   * (docs/plans/2026-04-20-superpowers-plugin-design.md design #1).
   * Read-only — the hook reads the current session permissionMode to
   * avoid nudging when the user has already explicitly entered plan
   * mode. Optional; without the delegate the hook is skipped.
   */
  planModeAutoTriggerAgent?: PlanModeAutoTriggerAgent;
  /**
   * Delegate used by the onboarding-needed-check hook
   * (docs/plans/2026-04-20-superpowers-plugin-design.md design #2).
   * Returns the live `Session` by sessionKey so the hook can read
   * session.meta.onboarded / onboardingDeclines + the budget stats
   * that gate the nudge. Optional; without the delegate the hook is
   * skipped.
   */
  onboardingNeededCheckAgent?: OnboardingNeededCheckAgent;
  /**
   * Delegate used by the fact-grounding verifier hook (priority 82) to
   * read the session's on-disk transcript and extract tool_result entries
   * for Haiku grounding comparison. Shares the same transcript-reader
   * interface as preRefusalVerifierAgent. Optional — falls back to
   * ctx.transcript.
   */
  factGroundingAgent?: FactGroundingAgent;
  /**
   * Delegate used by the resource-existence checker hook (priority 83) to
   * read the session's on-disk transcript and verify FileRead/Grep/Glob
   * calls match file references in the response. Optional — falls back
   * to ctx.transcript.
   */
  resourceCheckAgent?: ResourceCheckAgent;
  /**
   * Delegate used by the completion-evidence gate to read the current
   * turn transcript and confirm success/verification claims have
   * same-turn evidence. Optional — falls back to ctx.transcript.
   */
  completionEvidenceAgent?: CompletionEvidenceAgent;
  /**
   * Delegate used by task-contract verification enforcement. Shares
   * the completion-evidence transcript reader shape.
   */
  taskContractAgent?: CompletionEvidenceAgent;
  /**
   * Delegate used by artifact-delivery enforcement. Shares the
   * completion-evidence transcript reader shape.
   */
  artifactDeliveryAgent?: CompletionEvidenceAgent;
  /**
   * Delegate used by the resource-boundary gate to re-scan persisted
   * current-turn tool calls at beforeCommit. This catches bypass-mode
   * sessions where beforeToolUse hooks were intentionally skipped.
   */
  resourceBoundaryAgent?: ResourceBoundaryAgent;
  /**
   * Delegate used by the pre-refusal verifier hook (self-model Layer
   * 3) to read the session's on-disk transcript when checking whether
   * investigation tools fired this turn. Optional — when omitted the
   * hook falls back to the (currently empty) in-memory
   * `ctx.transcript`, which is safe because the hook no-ops under a
   * `continue` verdict when no refusal pattern matches.
   */
  preRefusalVerifierAgent?: PreRefusalVerifierAgent;
  /**
   * Delegate for the deferral blocker hook (2026-04-20). Reads
   * on-disk transcript to check whether WORK_TOOLS fired this turn
   * when assessing the severity of a deferral promise. Optional —
   * hook works against ctx.transcript fallback when omitted.
   */
  deferralBlockerAgent?: DeferralBlockerAgent;
  /**
   * Delegate for the output delivery gate (priority 87). Reads the
   * output artifact registry and blocks turn completion when the
   * current turn created user-facing files that have not yet been
   * delivered.
   */
  outputDeliveryAgent?: OutputDeliveryGateAgent;
  /** Native hipocampus compaction engine + qmd manager. Both optional —
   *  when omitted the compactor + flush hooks are skipped. */
  compactionEngine?: CompactionEngineType;
  qmdManager?: QmdManagerType;
  hipocampus?: Pick<HipocampusService, "recall">;
  /** Typed runtime policy facade. Optional in tests. */
  policyKernel?: PolicyKernelType;
  /** Reads current-turn transcript for user harness rule evaluation. */
  userHarnessRuleAgent?: UserHarnessRuleAgent;
  /** Workflow-native debugging state manager. Optional in tests. */
  debugWorkflow?: DebugWorkflowType;
  /** File delivery interceptor config. When present, enables the
   *  deterministic Haiku-classified file delivery hook. */
  fileDelivery?: FileDeliveryInterceptorOptions;
}

export function registerBuiltinHooks(
  registry: HookRegistry,
  opts: RegisterBuiltinsOpts,
): { registered: number; skipped: string[] } {
  const disabled = opts.disabled ?? [];
  const skipped: string[] = [];
  let registered = 0;

  const maybe = (name: string): boolean => {
    if (disabled.includes(name)) {
      skipped.push(name);
      return false;
    }
    return true;
  };

  // File delivery interceptor (priority 1, runs before everything).
  // Haiku-classified — deterministic file delivery without depending
  // on the main model understanding "send this file" vs "summarize."
  if (opts.fileDelivery && maybe("builtin:file-delivery-interceptor")) {
    registry.register(fileDeliveryInterceptor(opts.fileDelivery));
    registered++;
  }

  // Agent self-model (Layer 1 meta-cognitive scaffolding). Priority 0
  // — runs first so identity/memory/discipline layer on top. Hook
  // reads CORE_AGENT_SELF_MODEL env internally (default on).
  if (maybe(agentSelfModelHook.name)) {
    registry.register(agentSelfModelHook);
    registered++;
  }

  const reliabilityPromptHook = makeReliabilityPromptInjectorHook();
  if (maybe(reliabilityPromptHook.name)) {
    registry.register(reliabilityPromptHook);
    registered++;
  }

  const executionContractPromptHook = makeExecutionContractPromptHook();
  if (maybe(executionContractPromptHook.name)) {
    registry.register(executionContractPromptHook);
    registered++;
  }

  if (opts.policyKernel) {
    const policyPromptHook = makePolicyPromptBlockHook({
      policy: opts.policyKernel,
    });
    if (maybe(policyPromptHook.name)) {
      registry.register(policyPromptHook);
      registered++;
    }

    const harnessHooks = makeUserHarnessRuleHooks({
      policy: opts.policyKernel,
      agent: opts.userHarnessRuleAgent,
    });
    if (maybe(harnessHooks.beforeCommit.name)) {
      registry.register(harnessHooks.beforeCommit);
      registry.register(harnessHooks.afterToolUse);
      registered += 2;
    }
  }

  if (opts.debugWorkflow) {
    const debugTurnClassifierHook = makeDebugTurnClassifierHook({
      workflow: opts.debugWorkflow,
    });
    if (maybe(debugTurnClassifierHook.name)) {
      registry.register(debugTurnClassifierHook);
      registered++;
    }

    const debugInvestigationGuardHook = makeDebugInvestigationGuardHook({
      workflow: opts.debugWorkflow,
    });
    if (maybe(debugInvestigationGuardHook.name)) {
      registry.register(debugInvestigationGuardHook);
      registered++;
    }

    const debugAfterToolHook = makeDebugAfterToolCheckpointHook({
      workflow: opts.debugWorkflow,
    });
    if (maybe(debugAfterToolHook.name)) {
      registry.register(debugAfterToolHook);
      registered++;
    }

    const debugCommitHook = makeDebugCommitCheckpointHook({
      workflow: opts.debugWorkflow,
    });
    if (maybe(debugCommitHook.name)) {
      registry.register(debugCommitHook);
      registered++;
    }
  }

  // Workspace awareness (Layer 2 meta-cognitive scaffolding). Priority
  // 7, beforeLLMCall. Injects a `<workspace_snapshot>` block listing
  // top-level dirs + recently-modified files. Hook reads
  // CORE_AGENT_WORKSPACE_AWARENESS env internally (default on).
  const workspaceAwarenessHook = makeWorkspaceAwarenessHook({
    workspaceRoot: opts.workspaceRoot,
  });
  if (maybe(workspaceAwarenessHook.name)) {
    registry.register(workspaceAwarenessHook);
    registered++;
  }

  // Session resume seed (Layer 4 meta-cognitive scaffolding). Priority
  // 2, beforeTurnStart. Skipped entirely when no delegate is wired
  // (unit tests). Hook reads CORE_AGENT_SESSION_RESUME_SEED env
  // internally (default on).
  if (opts.sessionResumeAgent) {
    const resumeHook = makeSessionResumeHook({
      agent: opts.sessionResumeAgent,
      workspaceRoot: opts.workspaceRoot,
    });
    if (maybe(resumeHook.name)) {
      registry.register(resumeHook);
      registered++;
    }
  } else {
    skipped.push("builtin:session-resume");
  }

  // Pre-refusal verifier (Layer 3 meta-cognitive scaffolding). Priority
  // 85, beforeCommit. Blocks refusal drafts that did not investigate
  // the workspace this turn; one retry then fail-open. Delegate is
  // optional — without it the hook falls back to `ctx.transcript`
  // (empty in prod today but populated in tests).
  const preRefusalHook = makePreRefusalVerifierHook({
    agent: opts.preRefusalVerifierAgent,
  });
  if (maybe(preRefusalHook.name)) {
    registry.register(preRefusalHook);
    registered++;
  }

  // Deferral blocker (2026-04-20). Priority 86 — one notch after
  // preRefusalVerifier (85), before answerVerifier (90). Blocks
  // turn-ending responses that promise future delivery ("I'll send
  // results when done" / "완료되면 결과 보내드릴게요"). One retry then
  // fail-open. Env gate CORE_AGENT_DEFERRAL_BLOCKER (default on).
  const deferralHook = makeDeferralBlockerHook({
    agent: opts.deferralBlockerAgent,
  });
  if (maybe(deferralHook.name)) {
    registry.register(deferralHook);
    registered++;
  }

  const outputDeliveryGateHook = makeOutputDeliveryGateHook({
    agent: opts.outputDeliveryAgent,
  });
  if (maybe(outputDeliveryGateHook.name)) {
    registry.register(outputDeliveryGateHook);
    registered++;
  }

  if (opts.sessionsDir) {
    const taskBoardCompletionHook = makeTaskBoardCompletionGateHook({
      sessionsDir: opts.sessionsDir,
    });
    if (maybe(taskBoardCompletionHook.name)) {
      registry.register(taskBoardCompletionHook);
      registered++;
    }
  } else {
    skipped.push("builtin:task-board-completion-gate");
  }

  // T1-01 — qmd memory injection. Env-gated so operators can disable
  // the whole hook globally (e.g. during qmd outage) without editing
  // per-bot agent.config.yaml. Runs at priority 5 (earliest
  // beforeLLMCall) so downstream hooks see the augmented system.
  const memoryInjectionEnv = (process.env.CORE_AGENT_MEMORY_INJECTION ?? "on")
    .trim()
    .toLowerCase();
  const memoryInjectionEnabled =
    memoryInjectionEnv === "" ||
    memoryInjectionEnv === "on" ||
    memoryInjectionEnv === "true" ||
    memoryInjectionEnv === "1";
  if (memoryInjectionEnabled) {
    const memoryHook = makeMemoryInjectorHook({
      workspaceRoot: opts.workspaceRoot,
      qmdManager: opts.qmdManager,
      hipocampus: opts.hipocampus,
    });
    if (maybe(memoryHook.name)) {
      registry.register(memoryHook);
      registered++;
    }
  } else {
    skipped.push("builtin:memory-injector");
  }

  // Mid-turn injector (#86) — drains Session.pendingInjections at the
  // start of each beforeLLMCall so injected messages are absorbed into
  // the running turn (Claude Code parity). Env-gated
  // (`CORE_AGENT_MID_TURN_INJECT`, default on); skipped when no
  // delegate was wired (unit tests).
  const midTurnInjectEnv = (process.env.CORE_AGENT_MID_TURN_INJECT ?? "on")
    .trim()
    .toLowerCase();
  const midTurnInjectEnabled =
    midTurnInjectEnv === "" ||
    midTurnInjectEnv === "on" ||
    midTurnInjectEnv === "true" ||
    midTurnInjectEnv === "1";
  if (midTurnInjectEnabled && opts.midTurnInjectorAgent) {
    const injHook = makeMidTurnInjectorHook({
      agent: opts.midTurnInjectorAgent,
    });
    if (maybe(injHook.name)) {
      registry.register(injHook);
      registered++;
    }
  } else {
    skipped.push("builtin:mid-turn-injector");
  }

  // T2-08 — auto-approval hook for `permissionMode = "auto"` sessions.
  // Skipped when no delegate was wired (unit tests) or when the hook
  // is listed in `disable_builtin_hooks`. Hook no-ops for all modes
  // other than `auto`, so it is safe to register unconditionally when
  // the delegate is available.
  if (opts.autoApprovalAgent) {
    const autoHook = makeAutoApprovalHook({
      agent: opts.autoApprovalAgent,
    });
    if (maybe(autoHook.name)) {
      registry.register(autoHook);
      registered++;
    }
  } else {
    skipped.push("builtin:auto-approval");
  }

  if (maybe(subSessionIdentityHook.name)) {
    registry.register(subSessionIdentityHook);
    registered++;
  }
  if (maybe(citationGateHook.name)) {
    registry.register(citationGateHook);
    registered++;
  }
  if (maybe(sessionCommitmentTrackerHook.name)) {
    registry.register(sessionCommitmentTrackerHook);
    registered++;
  }
  if (maybe(selfClaimVerifierHook.name)) {
    registry.register(selfClaimVerifierHook);
    registered++;
  }

  const secretHook = makeSecretExposureGateHook();
  if (maybe(secretHook.name)) {
    registry.register(secretHook);
    registered++;
  }

  const providerHealthHook = makeProviderHealthVerifierHook();
  if (maybe(providerHealthHook.name)) {
    registry.register(providerHealthHook);
    registered++;
  }

  // Fact grounding verifier (priority 82) — Haiku-judged gate that
  // blocks commits where tool output is distorted or fabricated in the
  // response. Env-gated (`CORE_AGENT_FACT_GROUNDING`, default on).
  // Design: docs/plans/2026-04-21-anti-hallucination-hooks-design.md
  const factGroundingEnv = (process.env.CORE_AGENT_FACT_GROUNDING ?? "on")
    .trim()
    .toLowerCase();
  const factGroundingEnabled =
    factGroundingEnv === "" ||
    factGroundingEnv === "on" ||
    factGroundingEnv === "true" ||
    factGroundingEnv === "1";
  if (factGroundingEnabled) {
    const fgHook = makeFactGroundingVerifierHook({
      agent: opts.factGroundingAgent,
    });
    if (maybe(fgHook.name)) {
      registry.register(fgHook);
      registered++;
    }
  } else {
    skipped.push("builtin:fact-grounding-verifier");
  }

  // Resource existence checker (priority 83) — heuristic gate that
  // blocks commits claiming file contents without having read the file
  // this turn. No LLM call, pure regex. Env-gated
  // (`CORE_AGENT_RESOURCE_CHECK`, default on).
  // Design: docs/plans/2026-04-21-anti-hallucination-hooks-design.md
  const resourceCheckEnv = (process.env.CORE_AGENT_RESOURCE_CHECK ?? "on")
    .trim()
    .toLowerCase();
  const resourceCheckEnabled =
    resourceCheckEnv === "" ||
    resourceCheckEnv === "on" ||
    resourceCheckEnv === "true" ||
    resourceCheckEnv === "1";
  if (resourceCheckEnabled) {
    const rcHook = makeResourceExistenceCheckerHook({
      agent: opts.resourceCheckAgent,
    });
    if (maybe(rcHook.name)) {
      registry.register(rcHook);
      registered++;
    }
  } else {
    skipped.push("builtin:resource-existence-checker");
  }

  const outputPurityHook = makeOutputPurityGateHook();
  if (maybe(outputPurityHook.name)) {
    registry.register(outputPurityHook);
    registered++;
  }

  const completionEvidenceHook = makeCompletionEvidenceGateHook({
    agent: opts.completionEvidenceAgent,
    debugWorkflow: opts.debugWorkflow,
  });
  if (maybe(completionEvidenceHook.name)) {
    registry.register(completionEvidenceHook);
    registered++;
  }

  const taskContractGateHook = makeTaskContractGateHook({
    agent: opts.taskContractAgent,
    debugWorkflow: opts.debugWorkflow,
  });
  if (maybe(taskContractGateHook.name)) {
    registry.register(taskContractGateHook);
    registered++;
  }

  const executionContractVerifierHook = makeExecutionContractVerifierHook();
  if (maybe(executionContractVerifierHook.name)) {
    registry.register(executionContractVerifierHook);
    registered++;
  }

  const resourceBoundaryHooks = makeResourceBoundaryHooks({
    agent: opts.resourceBoundaryAgent,
  });
  if (maybe(resourceBoundaryHooks.beforeToolUse.name)) {
    registry.register(resourceBoundaryHooks.beforeToolUse);
    registry.register(resourceBoundaryHooks.beforeCommit);
    registered += 2;
  }

  const artifactDeliveryGateHook = makeArtifactDeliveryGateHook({
    agent: opts.artifactDeliveryAgent,
  });
  if (maybe(artifactDeliveryGateHook.name)) {
    registry.register(artifactDeliveryGateHook);
    registered++;
  }

  // Gated by CORE_AGENT_ANSWER_VERIFY env (default on). The hook
  // itself reads the env and no-ops when off, but skip registration
  // entirely if the operator listed it in disable_builtin_hooks.
  if (maybe(answerVerifierHook.name)) {
    registry.register(answerVerifierHook);
    registered++;
  }

  // T3-15 — benchmark-verifier (OMC Port B). Empirical counterpart to
  // answer-verifier: runs a user-configured command, compares extracted
  // metric against baseline, blocks commit on regression. Opt-in via
  // `agent.config.yaml: benchmark:` block — the hook no-ops otherwise.
  // Env-gated (`CORE_AGENT_BENCHMARK_VERIFY`, default on) and honours
  // `disable_builtin_hooks: [builtin:benchmark-verifier]`.
  const benchmarkVerifyEnv = (process.env.CORE_AGENT_BENCHMARK_VERIFY ?? "on")
    .trim()
    .toLowerCase();
  const benchmarkVerifyEnabled =
    benchmarkVerifyEnv === "" ||
    benchmarkVerifyEnv === "on" ||
    benchmarkVerifyEnv === "true" ||
    benchmarkVerifyEnv === "1";
  if (benchmarkVerifyEnabled) {
    const benchHook = makeBenchmarkVerifierHook({ workspaceRoot: opts.workspaceRoot });
    if (maybe(benchHook.name)) {
      registry.register(benchHook);
      registered++;
    }
  } else {
    skipped.push("builtin:benchmark-verifier");
  }

  const hipoHook = makeHipocampusCheckpointHook(opts.workspaceRoot);
  if (maybe(hipoHook.name)) {
    registry.register(hipoHook);
    registered++;
  }

  // Native hipocampus compactor + flush are registered directly in
  // Agent.start() after CompactionEngine is created (they require the
  // engine instance which isn't available at registerBuiltinHooks time).

  // T3-12 — sealed-files integrity (OMC Port C). Env-gated (default
  // on). Registers turn-start drift snapshotting, the beforeCommit
  // guard, and the afterCommit manifest updater under the same
  // `builtin:sealed-files` toggle in `disable_builtin_hooks`.
  const sealedFilesEnv = (process.env.CORE_AGENT_SEALED_FILES ?? "on").trim().toLowerCase();
  const sealedFilesEnabled =
    sealedFilesEnv === "" ||
    sealedFilesEnv === "on" ||
    sealedFilesEnv === "true" ||
    sealedFilesEnv === "1";
  if (sealedFilesEnabled) {
    const sealed = makeSealedFilesHooks({ workspaceRoot: opts.workspaceRoot });
    if (maybe(sealed.beforeCommit.name)) {
      registry.register(sealed.beforeTurnStart);
      registry.register(sealed.beforeCommit);
      registry.register(sealed.afterCommit);
      registered += 3;
    }
  } else {
    skipped.push("builtin:sealed-files");
  }

  // Repeated-failure circuit breaker — beforeLLMCall gate that reads
  // the persisted circuit-breaker state written by sealedFiles (and
  // any future fail-closed hook). Runs at priority 5 so it short-
  // circuits before memoryInjector / task_contract / etc. Fail-open.
  // Env-gated via `CORE_AGENT_CIRCUIT_BREAKER` (default on).
  const circuitEnv = (process.env.CORE_AGENT_CIRCUIT_BREAKER ?? "off")
    .trim()
    .toLowerCase();
  const circuitEnabled =
    circuitEnv === "" ||
    circuitEnv === "on" ||
    circuitEnv === "true" ||
    circuitEnv === "1";
  if (circuitEnabled) {
    const guardHook = makeRepeatedFailureGuardHook({
      workspaceRoot: opts.workspaceRoot,
    });
    if (maybe(guardHook.name)) {
      registry.register(guardHook);
      registered++;
    }
  } else {
    skipped.push("builtin:repeated-failure-guard");
  }

  // T3-13 — beforeTurnStart sweeper that reconciles stale
  // iterationState against filesystem reality. Non-blocking (priority
  // 10, blocking=false) so any error or slowness can't abort a turn.
  const sweeperHook = makeIterationStateSweeperHook({
    workspaceRoot: opts.workspaceRoot,
  });
  if (maybe(sweeperHook.name)) {
    registry.register(sweeperHook);
    registered++;
  }

  // T3-14 — afterTurnEnd stop-condition evaluator (OMC Port E).
  // Non-blocking, late priority (90). Reads stop_conditions block off
  // agent.config.yaml; tasks with iterationState that meet a condition
  // get `step = "stopped"` and a `session_stop` AgentEvent is emitted.
  const stopHook = makeStopConditionsHook({
    workspaceRoot: opts.workspaceRoot,
  });
  if (maybe(stopHook.name)) {
    registry.register(stopHook);
    registered++;
  }

  // T2-09 — declarative dangerous_patterns beforeToolUse hook. Reads
  // `agent.config.yaml → dangerous_patterns: [...]`, falls back to a
  // hardcoded default set when the key is absent. Env-gated
  // (`CORE_AGENT_DANGEROUS_PATTERNS`, default on); opt-out per bot via
  // `disable_builtin_hooks: [builtin:dangerous-patterns]`.
  const dangerousPatternsEnv = (process.env.CORE_AGENT_DANGEROUS_PATTERNS ?? "on")
    .trim()
    .toLowerCase();
  const dangerousPatternsEnabled =
    dangerousPatternsEnv === "" ||
    dangerousPatternsEnv === "on" ||
    dangerousPatternsEnv === "true" ||
    dangerousPatternsEnv === "1";
  if (dangerousPatternsEnabled) {
    const dpHook = makeDangerousPatternsHook({ workspaceRoot: opts.workspaceRoot });
    if (maybe(dpHook.name)) {
      registry.register(dpHook);
      registered++;
    }
  } else {
    skipped.push("builtin:dangerous-patterns");
  }

  const deliverySafetyHook = makeCronDeliverySafetyHook();
  if (maybe(deliverySafetyHook.name)) {
    registry.register(deliverySafetyHook);
    registered++;
  }

  // Coding Discipline hooks (docs/plans/2026-04-20-coding-discipline-design.md).
  // All four share the `builtin:discipline-*` prefix so operators can
  // disable individually in agent.config.yaml: disable_builtin_hooks.
  // Env-gated via `CORE_AGENT_DISCIPLINE` (default on); skipped
  // entirely when no delegate was wired.
  const disciplineEnv = (process.env.CORE_AGENT_DISCIPLINE ?? "on")
    .trim()
    .toLowerCase();
  const disciplineEnabled =
    disciplineEnv === "" ||
    disciplineEnv === "on" ||
    disciplineEnv === "true" ||
    disciplineEnv === "1";
  if (disciplineEnabled && opts.disciplineAgent) {
    const classifyHook = makeClassifyTurnModeHook({
      agent: {
        getSessionDiscipline: opts.disciplineAgent.getSessionDiscipline.bind(
          opts.disciplineAgent,
        ),
        setSessionDiscipline: opts.disciplineAgent.setSessionDiscipline.bind(
          opts.disciplineAgent,
        ),
        isCodingAgentSkillActive: opts.disciplineAgent
          .isCodingAgentSkillActive
          ? opts.disciplineAgent.isCodingAgentSkillActive.bind(
              opts.disciplineAgent,
            )
          : undefined,
      },
    });
    if (maybe(classifyHook.name)) {
      registry.register(classifyHook);
      registered++;
    }
    const promptHook = makeDisciplinePromptBlockHook({
      agent: {
        getSessionDiscipline: opts.disciplineAgent.getSessionDiscipline.bind(
          opts.disciplineAgent,
        ),
        getSessionCounter: opts.disciplineAgent.getSessionCounter.bind(
          opts.disciplineAgent,
        ),
      },
    });
    if (maybe(promptHook.name)) {
      registry.register(promptHook);
      registered++;
    }
    const beforeHook = makeDisciplineBeforeToolUseHook({
      workspaceRoot: opts.workspaceRoot,
      agent: opts.disciplineAgent,
    });
    if (maybe(beforeHook.name)) {
      registry.register(beforeHook);
      registered++;
    }
    const afterHook = makeDisciplineAfterTurnEndHook({
      workspaceRoot: opts.workspaceRoot,
      agent: opts.disciplineAgent,
    });
    if (maybe(afterHook.name)) {
      registry.register(afterHook);
      registered++;
    }
  } else {
    skipped.push("builtin:discipline");
  }

  // #81 — inline task notifier. Priority 4 (right after midTurnInjector
  // at 3). Env-gated (`CORE_AGENT_INLINE_TASK_NOTIFY`, default on);
  // skipped when no delegate was wired.
  try {
    const inlineNotifyEnv = (
      process.env.CORE_AGENT_INLINE_TASK_NOTIFY ?? "on"
    )
      .trim()
      .toLowerCase();
    const inlineNotifyEnabled =
      inlineNotifyEnv === "" ||
      inlineNotifyEnv === "on" ||
      inlineNotifyEnv === "true" ||
      inlineNotifyEnv === "1";
    if (inlineNotifyEnabled && opts.taskNotifierAgent) {
      const notifyHook = makeInlineTaskNotifierHook({
        agent: opts.taskNotifierAgent,
      });
      if (maybe(notifyHook.name)) {
        registry.register(notifyHook);
        registered++;
      }
    } else {
      skipped.push("builtin:inline-task-notifier");
    }
  } catch {
    skipped.push("builtin:inline-task-notifier");
  }

  // Superpowers plan-mode auto-trigger (beforeLLMCall, priority 8).
  // See docs/plans/2026-04-20-superpowers-plugin-design.md design #1.
  // Env-gated via CORE_AGENT_PLAN_AUTOTRIGGER (default on); skipped
  // when no delegate was wired.
  const planAutoTriggerEnv = (
    process.env.CORE_AGENT_PLAN_AUTOTRIGGER ?? "on"
  )
    .trim()
    .toLowerCase();
  const planAutoTriggerEnabled =
    planAutoTriggerEnv === "" ||
    planAutoTriggerEnv === "on" ||
    planAutoTriggerEnv === "true" ||
    planAutoTriggerEnv === "1";
  if (planAutoTriggerEnabled && opts.planModeAutoTriggerAgent) {
    const pHook = makePlanModeAutoTriggerHook({
      agent: opts.planModeAutoTriggerAgent,
    });
    if (maybe(pHook.name)) {
      registry.register(pHook);
      registered++;
    }
  } else {
    skipped.push("builtin:plan-mode-auto-trigger");
  }

  // Superpowers onboarding nudge (beforeTurnStart, priority 6).
  // See docs/plans/2026-04-20-superpowers-plugin-design.md design #2.
  // Env-gated via CORE_AGENT_ONBOARDING_STEER (default on); skipped
  // when no delegate was wired.
  const onboardingSteerEnv = (
    process.env.CORE_AGENT_ONBOARDING_STEER ?? "on"
  )
    .trim()
    .toLowerCase();
  const onboardingSteerEnabled =
    onboardingSteerEnv === "" ||
    onboardingSteerEnv === "on" ||
    onboardingSteerEnv === "true" ||
    onboardingSteerEnv === "1";
  if (onboardingSteerEnabled && opts.onboardingNeededCheckAgent) {
    const oHook = makeOnboardingNeededCheckHook({
      agent: opts.onboardingNeededCheckAgent,
    });
    if (maybe(oHook.name)) {
      registry.register(oHook);
      registered++;
    }
  } else {
    skipped.push("builtin:onboarding-needed-check");
  }

  // Task Lifecycle hooks (0.17.1) — runtime-managed
  // `TASK-QUEUE.md → WORKING.md → memory/YYYY-MM-DD.md` flow. Three
  // hooks share one env gate `CORE_AGENT_TASK_LIFECYCLE` (default on);
  // the Haiku tiebreak is gated separately by
  // `CORE_AGENT_TASK_LIFECYCLE_HAIKU` (default on). Non-blocking,
  // fail-open. Each hook can be individually disabled via
  // `disable_builtin_hooks: ["builtin:task-lifecycle-detect", ...]`.
  const taskLifecycleEnv = (
    // 2026-04-20 0.17.3: env default ON. Test-isolation handled inside
    // makeTaskLifecycleHook via `isTestWorkspace(workspaceRoot)` —
    // handlers short-circuit when the workspace lives under /tmp or
    // /var/folders, so afterEach(fs.rm) no longer races afterTurnEnd.
    // To explicitly disable lifecycle in production, set
    // `CORE_AGENT_TASK_LIFECYCLE=off`.
    process.env.CORE_AGENT_TASK_LIFECYCLE ?? "on"
  )
    .trim()
    .toLowerCase();
  const taskLifecycleEnabled =
    taskLifecycleEnv === "" ||
    taskLifecycleEnv === "on" ||
    taskLifecycleEnv === "true" ||
    taskLifecycleEnv === "1";
  if (taskLifecycleEnabled) {
    const tl = makeTaskLifecycleHook({ workspaceRoot: opts.workspaceRoot });
    if (maybe(tl.detect.name)) {
      registry.register(tl.detect);
      registered++;
    }
    if (maybe(tl.activate.name)) {
      registry.register(tl.activate);
      registered++;
    }
    if (maybe(tl.resolve.name)) {
      registry.register(tl.resolve);
      registered++;
    }
  } else {
    skipped.push("builtin:task-lifecycle");
  }

  return { registered, skipped };
}
