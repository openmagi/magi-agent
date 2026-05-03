export type VerificationMode = "none" | "sample" | "full";
export type ExecutionControlMode = "light" | "heavy";
export type AcceptanceCriterionStatus = "pending" | "passed" | "failed" | "waived";
export type ResourceBindingMode = "audit" | "enforce";
export type DeterministicRequirementKind =
  | "clock"
  | "date_range"
  | "calculation"
  | "counting"
  | "data_query"
  | "comparison";
export type DeterministicRequirementSource =
  | "llm_classifier"
  | "user_harness"
  | "task_contract"
  | "manual";
export type DeterministicRequirementStatus =
  | "active"
  | "satisfied"
  | "failed"
  | "waived";
export type DeterministicEvidenceKind =
  | "clock"
  | "date_range"
  | "calculation"
  | "data_query"
  | "verification";
export type DeterministicEvidenceStatus =
  | "passed"
  | "failed"
  | "partial"
  | "unknown";
export type MetaClassificationSource = "llm_classifier" | "manual";
export type UsedResourceKind =
  | "workspace_path"
  | "source_path"
  | "artifact"
  | "resource"
  | "db_handle"
  | "external_url";

export interface ExecutionControlState {
  mode: ExecutionControlMode;
  reason: string;
}

export interface AcceptanceCriterion {
  id: string;
  text: string;
  required: boolean;
  status: AcceptanceCriterionStatus;
  evidenceIds: string[];
  updatedAt: number;
}

export interface ResourceBindings {
  mode: ResourceBindingMode;
  allowedWorkspacePaths: string[];
  allowedSourcePaths: string[];
  artifactIds: string[];
  resourceIds: string[];
  dbHandles: string[];
}

export interface DeterministicRequirement {
  requirementId: string;
  turnId?: string;
  source: DeterministicRequirementSource;
  status: DeterministicRequirementStatus;
  kinds: DeterministicRequirementKind[];
  reason: string;
  suggestedTools: string[];
  acceptanceCriteria: string[];
  evidenceIds: string[];
  createdAt: number;
  updatedAt: number;
}

export interface DeterministicEvidenceRecord {
  evidenceId: string;
  turnId?: string;
  requirementIds: string[];
  toolName: string;
  toolUseId?: string;
  kind: DeterministicEvidenceKind;
  status: DeterministicEvidenceStatus;
  inputSummary: string;
  output: unknown;
  assertions: string[];
  resources: string[];
  recordedAt: number;
}

export interface UsedResourceRecord {
  kind: UsedResourceKind;
  value: string;
  toolName: string;
  toolUseId?: string;
  recordedAt: number;
}

export interface VerificationEvidenceRecord {
  evidenceId?: string;
  source: "beforeCommit" | "tool" | "hook" | "manual";
  status: "passed" | "failed" | "partial" | "unknown";
  recordedAt: number;
  command?: string;
  detail?: string;
  criterionIds?: string[];
  toolUseId?: string;
  toolName?: string;
  exitCode?: number | null;
  assertions?: string[];
  resourceIds?: string[];
  artifactIds?: string[];
}

export interface RequestMetaClassificationResult {
  turnMode: {
    label: "coding" | "exploratory" | "other";
    confidence: number;
  };
  skipTdd: boolean;
  implementationIntent: boolean;
  documentOrFileOperation: boolean;
  deterministic: {
    requiresDeterministic: boolean;
    kinds: DeterministicRequirementKind[];
    reason: string;
    suggestedTools: string[];
    acceptanceCriteria: string[];
  };
  fileDelivery: {
    intent: "deliver_existing" | "none";
    path: string | null;
    wantsChatDelivery: boolean;
    wantsKbDelivery: boolean;
    wantsFileOutput: boolean;
  };
}

export interface RequestMetaClassificationRecord {
  turnId: string;
  inputHash: string;
  source: MetaClassificationSource;
  result: RequestMetaClassificationResult;
  classifiedAt: number;
}

export interface FinalAnswerMetaClassificationResult {
  internalReasoningLeak: boolean;
  lazyRefusal: boolean;
  selfClaim: boolean;
  deferralPromise: boolean;
  assistantClaimsFileCreated: boolean;
  assistantClaimsChatDelivery: boolean;
  assistantClaimsKbDelivery: boolean;
  assistantReportsDeliveryFailure: boolean;
  reason: string;
}

export interface FinalAnswerMetaClassificationRecord {
  turnId: string;
  inputHash: string;
  source: MetaClassificationSource;
  result: FinalAnswerMetaClassificationResult;
  classifiedAt: number;
}

export interface ExecutionTaskState {
  goal: string | null;
  constraints: string[];
  currentPlan: string[];
  completedSteps: string[];
  blockers: string[];
  criteria: AcceptanceCriterion[];
  /** Compatibility view for existing prompt rendering and hooks. */
  acceptanceCriteria: string[];
  resourceBindings: ResourceBindings;
  usedResources: UsedResourceRecord[];
  deterministicRequirements: DeterministicRequirement[];
  deterministicEvidence: DeterministicEvidenceRecord[];
  verificationMode: VerificationMode;
  verificationEvidence: VerificationEvidenceRecord[];
  requestMetaClassifications: RequestMetaClassificationRecord[];
  finalAnswerMetaClassifications: FinalAnswerMetaClassificationRecord[];
  artifacts: string[];
  updatedAt: number;
}

export interface WorkOrder {
  persona: string;
  goal: string;
  constraints: string[];
  acceptanceCriteria: string[];
  criteria?: AcceptanceCriterion[];
  resourceBindings?: ResourceBindings;
  allowedTools: string[];
  childPrompt: string;
}

export interface ExecutionContractSnapshot {
  taskState: ExecutionTaskState;
  workOrders: WorkOrder[];
  control: ExecutionControlState;
}

export interface ExecutionContractStoreOptions {
  now?: () => number;
}

const COMPLETION_CLAIM_RE =
  /(?:완료|끝났|반영|구현|처리|해결|고쳤|통과|verified|completed|done|implemented|fixed|resolved|passed)/i;

const TAG_LIST_RE = /<(constraints|acceptance_criteria|current_plan|completed_steps|blockers|artifacts)>\s*([\s\S]*?)\s*<\/\1>/gi;
const ITEM_RE = /<item>\s*([\s\S]*?)\s*<\/item>/gi;
const CONTRACT_TRIGGER_RE = /<task_contract\b|verification_mode|acceptance_criteria|검증\s*모드|수락\s*기준/i;
const CREATE_OR_EXPORT_RE =
  /(?:create|generate|write|draft|render|export|convert|make|build|작성|생성|만들|써줘|문서화|렌더|변환|내보내|저장|docx|hwpx|xlsx|pptx|pdf|html)/i;
const HEAVY_ACTION_RE =
  /(?:create|generate|write|draft|render|export|convert|edit|modify|delete|remove|deploy|push|commit|merge|schedule|background|subagent|spawn|send\s+(?:email|message)|작성|생성|만들|써줘|수정|편집|삭제|배포|커밋|머지|예약|백그라운드|서브에이전트|하위\s*에이전트|전송|이메일|문자|KB에\s*저장|지식\s*베이스에\s*저장)/i;
const SIMPLE_FILE_UNDERSTANDING_RE =
  /(?:(?:파일|문서|파이프라인|pipeline|file|document).{0,40}(?:뭐|무엇|설명|알려|요약|읽어|분석|what|explain|summari[sz]e|read)|(?:뭐|무엇|설명|알려|요약|읽어|what|explain|summari[sz]e|read).{0,40}(?:파일|문서|파이프라인|pipeline|file|document))/i;
const EXISTING_FILE_DELIVERY_RE =
  /(?:(?:여기서|이거|기존|방금|that|this|existing).{0,30}(?:파일로|첨부|다운로드|보내|전달|채팅(?:으로)?|send|attach|download)|(?:파일로|첨부|다운로드|보내|전달|채팅(?:으로)?|send|attach|download).{0,30}(?:여기|이거|기존|방금|that|this|existing))/i;
const CONTINUE_RE = /(?:continue|keep going|resume|finish|마저|계속|이어|끝까지|진행)/i;

export class ExecutionContractStore {
  private readonly now: () => number;
  private snapshotValue: ExecutionContractSnapshot;

  constructor(opts: ExecutionContractStoreOptions = {}) {
    this.now = opts.now ?? Date.now;
    this.snapshotValue = {
      taskState: {
        goal: null,
        constraints: [],
        currentPlan: [],
        completedSteps: [],
        blockers: [],
        criteria: [],
        acceptanceCriteria: [],
        resourceBindings: defaultResourceBindings(),
        usedResources: [],
        deterministicRequirements: [],
        deterministicEvidence: [],
        verificationMode: "none",
        verificationEvidence: [],
        requestMetaClassifications: [],
        finalAnswerMetaClassifications: [],
        artifacts: [],
        updatedAt: this.now(),
      },
      workOrders: [],
      control: {
        mode: "light",
        reason: "initial",
      },
    };
  }

  startTurn(input: { userMessage: string }): void {
    const parsed = parseTaskContract(input.userMessage);
    const goal = firstNonContractLine(input.userMessage);
    const control = classifyExecutionControl(input.userMessage, parsed, this.snapshotValue);
    const acceptanceCriteria = mergeUnique(
      this.snapshotValue.taskState.acceptanceCriteria,
      parsed.acceptanceCriteria,
    );
    this.patchTaskState({
      goal: parsed.goal ?? this.snapshotValue.taskState.goal ?? goal,
      constraints: mergeUnique(this.snapshotValue.taskState.constraints, parsed.constraints),
      currentPlan: mergeUnique(this.snapshotValue.taskState.currentPlan, parsed.currentPlan),
      completedSteps: mergeUnique(this.snapshotValue.taskState.completedSteps, parsed.completedSteps),
      blockers: mergeUnique(this.snapshotValue.taskState.blockers, parsed.blockers),
      criteria: mergeCriteria(
        this.snapshotValue.taskState.criteria,
        parsed.acceptanceCriteria,
        this.now(),
      ),
      acceptanceCriteria,
      resourceBindings: parsed.resourceBindings
        ? mergeResourceBindings(
            this.snapshotValue.taskState.resourceBindings,
            parsed.resourceBindings,
          )
        : this.snapshotValue.taskState.resourceBindings,
      artifacts: mergeUnique(this.snapshotValue.taskState.artifacts, parsed.artifacts),
      verificationMode: parsed.verificationMode ?? this.snapshotValue.taskState.verificationMode,
    });
    this.snapshotValue = {
      ...this.snapshotValue,
      control,
    };
  }

  patchTaskState(patch: Partial<Omit<ExecutionTaskState, "updatedAt">>): void {
    this.snapshotValue = {
      ...this.snapshotValue,
      taskState: {
        ...this.snapshotValue.taskState,
        ...patch,
        updatedAt: this.now(),
      },
    };
  }

  recordVerificationEvidence(
    evidence: Omit<VerificationEvidenceRecord, "recordedAt">,
  ): void {
    const now = this.now();
    const evidenceId =
      evidence.evidenceId ??
      `ev_${this.snapshotValue.taskState.verificationEvidence.length + 1}_${now.toString(36)}`;
    const record: VerificationEvidenceRecord = {
      ...evidence,
      evidenceId,
      recordedAt: now,
    };
    const nextCriteria = evidence.criterionIds?.length
      ? updateCriteriaStatus(
          this.snapshotValue.taskState.criteria,
          evidence.criterionIds,
          evidence.status === "passed"
            ? "passed"
            : evidence.status === "failed"
              ? "failed"
              : "pending",
          evidenceId,
          now,
        )
      : this.snapshotValue.taskState.criteria;
    this.patchTaskState({
      verificationEvidence: [
        ...this.snapshotValue.taskState.verificationEvidence,
        record,
      ],
      criteria: nextCriteria,
    });
  }

  markCriteria(input: {
    criterionIds: string[];
    status: AcceptanceCriterionStatus;
    evidenceId?: string;
  }): void {
    if (input.criterionIds.length === 0) return;
    this.patchTaskState({
      criteria: updateCriteriaStatus(
        this.snapshotValue.taskState.criteria,
        input.criterionIds,
        input.status,
        input.evidenceId,
        this.now(),
      ),
    });
  }

  recordUsedResource(input: Omit<UsedResourceRecord, "recordedAt">): void {
    const existing = this.snapshotValue.taskState.usedResources;
    const duplicate = existing.some(
      (record) =>
        record.kind === input.kind &&
        record.value === input.value &&
        record.toolName === input.toolName &&
        record.toolUseId === input.toolUseId,
    );
    if (duplicate) return;
    this.patchTaskState({
      usedResources: [...existing, { ...input, recordedAt: this.now() }],
    });
  }

  recordDeterministicRequirement(
    input: Omit<
      DeterministicRequirement,
      "createdAt" | "updatedAt" | "evidenceIds"
    > & { evidenceIds?: string[] },
  ): void {
    const now = this.now();
    const next: DeterministicRequirement = {
      ...input,
      evidenceIds: input.evidenceIds ?? [],
      createdAt: now,
      updatedAt: now,
    };
    const existing = this.snapshotValue.taskState.deterministicRequirements;
    const index = existing.findIndex(
      (record) => record.requirementId === input.requirementId,
    );
    const deterministicRequirements =
      index === -1
        ? [...existing, next]
        : existing.map((record, i) =>
            i === index
              ? {
                  ...record,
                  ...input,
                  evidenceIds: input.evidenceIds ?? record.evidenceIds,
                  updatedAt: now,
                }
              : record,
          );
    this.patchTaskState({ deterministicRequirements });
  }

  recordDeterministicEvidence(
    input: Omit<DeterministicEvidenceRecord, "recordedAt">,
  ): void {
    const now = this.now();
    const existing = this.snapshotValue.taskState.deterministicEvidence;
    const record: DeterministicEvidenceRecord = {
      ...input,
      recordedAt: now,
    };
    const duplicate = existing.some(
      (item) => item.evidenceId === record.evidenceId,
    );
    const deterministicEvidence = duplicate ? existing : [...existing, record];
    const passedRequirementIds =
      record.status === "passed" ? new Set(record.requirementIds) : new Set<string>();
    const deterministicRequirements =
      passedRequirementIds.size === 0
        ? this.snapshotValue.taskState.deterministicRequirements
        : this.snapshotValue.taskState.deterministicRequirements.map((requirement) => {
            if (!passedRequirementIds.has(requirement.requirementId)) {
              return requirement;
            }
            const evidenceIds = requirement.evidenceIds.includes(record.evidenceId)
              ? requirement.evidenceIds
              : [...requirement.evidenceIds, record.evidenceId];
            return {
              ...requirement,
              status: "satisfied" as const,
              evidenceIds,
              updatedAt: now,
            };
          });
    this.patchTaskState({
      deterministicEvidence,
      deterministicRequirements,
    });
  }

  recordRequestMetaClassification(
    input: Omit<RequestMetaClassificationRecord, "classifiedAt">,
  ): void {
    const now = this.now();
    const record: RequestMetaClassificationRecord = {
      ...input,
      classifiedAt: now,
    };
    const existing = this.snapshotValue.taskState.requestMetaClassifications;
    const index = existing.findIndex(
      (item) =>
        item.turnId === input.turnId &&
        item.inputHash === input.inputHash,
    );
    const requestMetaClassifications =
      index === -1
        ? [...existing, record]
        : existing.map((item, i) => (i === index ? record : item));
    this.patchTaskState({ requestMetaClassifications });
  }

  getRequestMetaClassification(
    turnId: string,
    inputHash: string,
  ): RequestMetaClassificationResult | null {
    const record = this.snapshotValue.taskState.requestMetaClassifications.find(
      (item) => item.turnId === turnId && item.inputHash === inputHash,
    );
    return record
      ? (JSON.parse(JSON.stringify(record.result)) as RequestMetaClassificationResult)
      : null;
  }

  recordFinalAnswerClassification(
    input: Omit<FinalAnswerMetaClassificationRecord, "classifiedAt">,
  ): void {
    const now = this.now();
    const record: FinalAnswerMetaClassificationRecord = {
      ...input,
      classifiedAt: now,
    };
    const existing = this.snapshotValue.taskState.finalAnswerMetaClassifications;
    const index = existing.findIndex(
      (item) =>
        item.turnId === input.turnId &&
        item.inputHash === input.inputHash,
    );
    const finalAnswerMetaClassifications =
      index === -1
        ? [...existing, record]
        : existing.map((item, i) => (i === index ? record : item));
    this.patchTaskState({ finalAnswerMetaClassifications });
  }

  getFinalAnswerClassification(
    turnId: string,
    inputHash: string,
  ): FinalAnswerMetaClassificationResult | null {
    const record = this.snapshotValue.taskState.finalAnswerMetaClassifications.find(
      (item) => item.turnId === turnId && item.inputHash === inputHash,
    );
    return record
      ? (JSON.parse(JSON.stringify(record.result)) as FinalAnswerMetaClassificationResult)
      : null;
  }

  unmetRequiredCriteria(): AcceptanceCriterion[] {
    return this.snapshotValue.taskState.criteria.filter(
      (criterion) =>
        criterion.required &&
        criterion.status !== "passed" &&
        criterion.status !== "waived",
    );
  }

  recordWorkOrder(order: WorkOrder): void {
    this.snapshotValue = {
      ...this.snapshotValue,
      workOrders: [...this.snapshotValue.workOrders, order],
    };
  }

  snapshot(): ExecutionContractSnapshot {
    return JSON.parse(JSON.stringify(this.snapshotValue)) as ExecutionContractSnapshot;
  }

  renderPromptBlock(): string {
    return renderExecutionContractBlock(this.snapshotValue);
  }
}

export function renderExecutionContractBlock(
  snapshot: ExecutionContractSnapshot,
): string {
  const task = snapshot.taskState;
  const lines = [
    `<execution_contract source="runtime">`,
    `goal: ${task.goal ?? "(unset)"}`,
    `verification_mode: ${task.verificationMode}`,
    renderList("constraints", task.constraints),
    renderList("current_plan", task.currentPlan),
    renderList("completed_steps", task.completedSteps),
    renderList("blockers", task.blockers),
    renderList("acceptance_criteria", task.acceptanceCriteria),
    renderResourceBindingsBlock(task.resourceBindings),
    renderList(
      "deterministic_requirements",
      task.deterministicRequirements.map((requirement) =>
        [
          requirement.requirementId,
          requirement.turnId,
          requirement.status,
          requirement.kinds.join(","),
          requirement.reason,
          requirement.suggestedTools.length > 0
            ? `tools=${requirement.suggestedTools.join(",")}`
            : "",
        ].filter(Boolean).join(" | "),
      ),
    ),
    renderList(
      "deterministic_evidence",
      task.deterministicEvidence.map((evidence) =>
        [
          evidence.evidenceId,
          evidence.turnId,
          evidence.status,
          evidence.kind,
          evidence.toolName,
          evidence.inputSummary,
        ].filter(Boolean).join(" | "),
      ),
    ),
    renderList(
      "verification_evidence",
      task.verificationEvidence.map((e) =>
        [e.status, e.command, e.detail].filter(Boolean).join(" | "),
      ),
    ),
    renderList("artifacts", task.artifacts),
    `</execution_contract>`,
  ];
  return lines.filter((line) => line.length > 0).join("\n");
}

export function completionClaimNeedsContractVerification(
  snapshot: ExecutionContractSnapshot,
  assistantText: string,
): boolean {
  if (snapshot.control.mode !== "heavy") return false;
  const task = snapshot.taskState;
  if (
    task.criteria.length === 0 &&
    task.acceptanceCriteria.length === 0 &&
    task.verificationMode !== "full"
  ) {
    return false;
  }
  if (!COMPLETION_CLAIM_RE.test(assistantText)) return false;
  if (task.criteria.length > 0) {
    return completionClaimMissingCriteria(snapshot, assistantText).length > 0;
  }
  return !task.verificationEvidence.some((e) => e.status === "passed");
}

export function completionClaimMissingCriteria(
  snapshot: ExecutionContractSnapshot,
  assistantText: string,
): AcceptanceCriterion[] {
  if (snapshot.control.mode !== "heavy") return [];
  if (!COMPLETION_CLAIM_RE.test(assistantText)) return [];
  return snapshot.taskState.criteria.filter(
    (criterion) =>
      criterion.required &&
      criterion.status !== "passed" &&
      criterion.status !== "waived",
  );
}

export function shouldInjectExecutionContract(
  snapshot: ExecutionContractSnapshot,
): boolean {
  return (
    snapshot.control.mode === "heavy" ||
    snapshot.taskState.deterministicRequirements.some(
      (requirement) =>
        requirement.status === "active" || requirement.status === "satisfied",
    )
  );
}

export function classifyExecutionControl(
  userText: string,
  parsed: Partial<Omit<ExecutionTaskState, "updatedAt">> = {},
  current?: ExecutionContractSnapshot,
): ExecutionControlState {
  const text = normalizeWhitespace(userText);
  if (!text) return { mode: "light", reason: "empty" };

  if (CONTRACT_TRIGGER_RE.test(userText)) {
    return { mode: "heavy", reason: "explicit_contract" };
  }
  if (parsed.verificationMode === "full" || parsed.acceptanceCriteria?.length) {
    return { mode: "heavy", reason: "explicit_acceptance_or_full_verification" };
  }
  if (HEAVY_ACTION_RE.test(text)) {
    return { mode: "heavy", reason: "state_changing_or_risky_action" };
  }
  if (CONTINUE_RE.test(text) && hasActiveHeavyContract(current)) {
    return { mode: "heavy", reason: "continue_active_contract" };
  }
  if (SIMPLE_FILE_UNDERSTANDING_RE.test(text)) {
    return { mode: "light", reason: "simple_file_understanding" };
  }
  if (EXISTING_FILE_DELIVERY_RE.test(text) && !CREATE_OR_EXPORT_RE.test(text)) {
    return { mode: "light", reason: "deliver_existing_file" };
  }
  return { mode: "light", reason: "default" };
}

export function buildSpawnWorkOrderPrompt(input: {
  parent: ExecutionContractSnapshot;
  childPrompt: string;
  persona: string;
  allowedTools?: string[];
}): string {
  const task = input.parent.taskState;
  const order: WorkOrder = {
    persona: input.persona,
    goal: task.goal ?? input.childPrompt,
    constraints: task.constraints,
    acceptanceCriteria: task.acceptanceCriteria,
    criteria: task.criteria,
    resourceBindings: task.resourceBindings,
    allowedTools: input.allowedTools ?? [],
    childPrompt: input.childPrompt,
  };

  return [
    "<work_order>",
    `persona: ${order.persona}`,
    `parent_goal: ${order.goal}`,
    renderList("constraints", order.constraints),
    "<acceptance_criteria>",
    ...(order.criteria && order.criteria.length > 0
      ? order.criteria.map(
          (criterion) =>
            `<item id="${criterion.id}" status="${criterion.status}" required="${criterion.required}">${criterion.text}</item>`,
        )
      : order.acceptanceCriteria.map((value) => `<item>${value}</item>`)),
    "</acceptance_criteria>",
    renderResourceBindingsXml(order.resourceBindings ?? defaultResourceBindings()),
    renderList("allowed_tools", order.allowedTools),
    "rules:",
    "- Do not modify files outside your assigned scope.",
    "- Return the evidence needed to judge the acceptance criteria.",
    "- Report blockers explicitly instead of silently skipping them.",
    "</work_order>",
    "",
    input.childPrompt,
  ].join("\n");
}

function parseTaskContract(text: string): Partial<Omit<ExecutionTaskState, "updatedAt">> {
  const out: Partial<Omit<ExecutionTaskState, "updatedAt">> = {};
  const goal = text.match(/<goal>\s*([\s\S]*?)\s*<\/goal>/i)?.[1]?.trim();
  if (goal) out.goal = normalizeWhitespace(goal);

  const verificationMode =
    text.match(/<verification_mode>\s*([^<\s]+)\s*<\/verification_mode>/i)?.[1] ??
    text.match(/verification_mode\s*[:=]\s*["']?([a-z]+)/i)?.[1];
  if (verificationMode) {
    const normalized = verificationMode.toLowerCase();
    if (normalized === "full" || normalized === "sample" || normalized === "none") {
      out.verificationMode = normalized;
    }
  }

  for (const match of text.matchAll(TAG_LIST_RE)) {
    const tag = match[1];
    const values = extractItems(match[2] ?? "");
    if (values.length === 0) continue;
    if (tag === "constraints") out.constraints = values;
    if (tag === "acceptance_criteria") out.acceptanceCriteria = values;
    if (tag === "current_plan") out.currentPlan = values;
    if (tag === "completed_steps") out.completedSteps = values;
    if (tag === "blockers") out.blockers = values;
    if (tag === "artifacts") out.artifacts = values;
  }
  const resourceBindings = parseResourceBindings(text);
  if (resourceBindings) out.resourceBindings = resourceBindings;
  return out;
}

function parseResourceBindings(text: string): ResourceBindings | null {
  const match = text.match(/<resource_bindings\b([^>]*)>([\s\S]*?)<\/resource_bindings>/i);
  if (!match) return null;
  const attrs = match[1] ?? "";
  const body = match[2] ?? "";
  const modeRaw = attrs.match(/\bmode\s*=\s*["']?(audit|enforce)["']?/i)?.[1];
  const mode: ResourceBindingMode =
    modeRaw?.toLowerCase() === "audit" ? "audit" : "enforce";
  return {
    mode,
    allowedWorkspacePaths: extractTaggedItems(body, "allowed_workspace_paths"),
    allowedSourcePaths: extractTaggedItems(body, "allowed_source_paths"),
    artifactIds: extractTaggedItems(body, "artifact_ids"),
    resourceIds: extractTaggedItems(body, "resource_ids"),
    dbHandles: extractTaggedItems(body, "db_handles"),
  };
}

function extractTaggedItems(raw: string, tag: string): string[] {
  const match = raw.match(new RegExp(`<${tag}>\\s*([\\s\\S]*?)\\s*<\\/${tag}>`, "i"));
  if (!match) return [];
  return [...new Set(extractItems(match[1] ?? ""))];
}

function extractItems(raw: string): string[] {
  const items = [...raw.matchAll(ITEM_RE)]
    .map((match) => normalizeWhitespace(match[1] ?? ""))
    .filter((item) => item.length > 0);
  if (items.length > 0) return items;
  return raw
    .split("\n")
    .map((line) => normalizeWhitespace(line.replace(/^\s*(?:[-*+]|\d+[.)])\s*/, "")))
    .filter((line) => line.length > 0);
}

function firstNonContractLine(text: string): string | null {
  const stripped = text.replace(/<task_contract>[\s\S]*?<\/task_contract>/gi, "");
  const first = stripped
    .split("\n")
    .map((line) => normalizeWhitespace(line))
    .find((line) => line.length > 0);
  return first ?? null;
}

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function mergeUnique(existing: string[], next?: string[]): string[] {
  if (!next || next.length === 0) return existing;
  return [...new Set([...existing, ...next])];
}

function defaultResourceBindings(): ResourceBindings {
  return {
    mode: "audit",
    allowedWorkspacePaths: [],
    allowedSourcePaths: [],
    artifactIds: [],
    resourceIds: [],
    dbHandles: [],
  };
}

function mergeResourceBindings(
  existing: ResourceBindings,
  next: ResourceBindings,
): ResourceBindings {
  return {
    mode: next.mode,
    allowedWorkspacePaths: mergeUnique(existing.allowedWorkspacePaths, next.allowedWorkspacePaths),
    allowedSourcePaths: mergeUnique(existing.allowedSourcePaths, next.allowedSourcePaths),
    artifactIds: mergeUnique(existing.artifactIds, next.artifactIds),
    resourceIds: mergeUnique(existing.resourceIds, next.resourceIds),
    dbHandles: mergeUnique(existing.dbHandles, next.dbHandles),
  };
}

function criterionIdForText(text: string): string {
  let hash = 2166136261;
  for (const ch of normalizeWhitespace(text)) {
    hash ^= ch.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `ac_${(hash >>> 0).toString(36)}`;
}

function mergeCriteria(
  existing: AcceptanceCriterion[],
  nextTexts: string[] | undefined,
  now: number,
): AcceptanceCriterion[] {
  if (!nextTexts || nextTexts.length === 0) return existing;
  const byId = new Map(existing.map((criterion) => [criterion.id, criterion]));
  for (const text of nextTexts) {
    const normalized = normalizeWhitespace(text);
    if (!normalized) continue;
    const id = criterionIdForText(normalized);
    if (byId.has(id)) continue;
    byId.set(id, {
      id,
      text: normalized,
      required: true,
      status: "pending",
      evidenceIds: [],
      updatedAt: now,
    });
  }
  return [...byId.values()];
}

function updateCriteriaStatus(
  criteria: AcceptanceCriterion[],
  criterionIds: string[],
  status: AcceptanceCriterionStatus,
  evidenceId: string | undefined,
  now: number,
): AcceptanceCriterion[] {
  const ids = new Set(criterionIds);
  return criteria.map((criterion) => {
    if (!ids.has(criterion.id)) return criterion;
    const evidenceIds =
      evidenceId && !criterion.evidenceIds.includes(evidenceId)
        ? [...criterion.evidenceIds, evidenceId]
        : criterion.evidenceIds;
    return {
      ...criterion,
      status,
      evidenceIds,
      updatedAt: now,
    };
  });
}

function hasActiveHeavyContract(snapshot?: ExecutionContractSnapshot): boolean {
  if (!snapshot) return false;
  return (
    snapshot.control.mode === "heavy" ||
    snapshot.taskState.verificationMode === "full" ||
    snapshot.taskState.acceptanceCriteria.length > 0
  );
}

function renderList(label: string, values: string[]): string {
  if (values.length === 0) return `${label}: []`;
  return [`${label}:`, ...values.map((value) => `- ${value}`)].join("\n");
}

function renderResourceBindingsBlock(bindings: ResourceBindings): string {
  if (!resourceBindingsAreActive(bindings)) return "resource_bindings: []";
  return [
    "resource_bindings:",
    `- mode: ${bindings.mode}`,
    ...bindings.allowedWorkspacePaths.map((value) => `- allowed_workspace_path: ${value}`),
    ...bindings.allowedSourcePaths.map((value) => `- allowed_source_path: ${value}`),
    ...bindings.artifactIds.map((value) => `- artifact_id: ${value}`),
    ...bindings.resourceIds.map((value) => `- resource_id: ${value}`),
    ...bindings.dbHandles.map((value) => `- db_handle: ${value}`),
  ].join("\n");
}

function renderResourceBindingsXml(bindings: ResourceBindings): string {
  if (!resourceBindingsAreActive(bindings)) return "<resource_bindings mode=\"audit\" />";
  return [
    `<resource_bindings mode="${bindings.mode}">`,
    renderXmlItems("allowed_workspace_paths", bindings.allowedWorkspacePaths),
    renderXmlItems("allowed_source_paths", bindings.allowedSourcePaths),
    renderXmlItems("artifact_ids", bindings.artifactIds),
    renderXmlItems("resource_ids", bindings.resourceIds),
    renderXmlItems("db_handles", bindings.dbHandles),
    "</resource_bindings>",
  ].filter((line) => line.length > 0).join("\n");
}

function renderXmlItems(label: string, values: string[]): string {
  if (values.length === 0) return "";
  return [
    `<${label}>`,
    ...values.map((value) => `<item>${value}</item>`),
    `</${label}>`,
  ].join("\n");
}

function resourceBindingsAreActive(bindings: ResourceBindings): boolean {
  return (
    bindings.allowedWorkspacePaths.length > 0 ||
    bindings.allowedSourcePaths.length > 0 ||
    bindings.artifactIds.length > 0 ||
    bindings.resourceIds.length > 0 ||
    bindings.dbHandles.length > 0
  );
}
