import { describe, expect, it } from "vitest";
import {
  ExecutionContractStore,
  buildSpawnWorkOrderPrompt,
  classifyExecutionControl,
  completionClaimNeedsContractVerification,
  shouldInjectExecutionContract,
} from "./ExecutionContract.js";

describe("ExecutionContractStore", () => {
  it("extracts task state as a first-class object instead of relying on transcript prose", () => {
    const store = new ExecutionContractStore({ now: () => 123 });

    store.startTurn({
      userMessage: [
        "월간 리포트를 만들어줘.",
        "<task_contract>",
        "<constraints><item>한국어로 작성</item><item>표 포함</item></constraints>",
        "<acceptance_criteria><item>요약 섹션 포함</item><item>xlsx 파일 생성</item></acceptance_criteria>",
        "<verification_mode>full</verification_mode>",
        "</task_contract>",
      ].join("\n"),
    });

    expect(store.snapshot()).toMatchObject({
      taskState: {
        goal: "월간 리포트를 만들어줘.",
        constraints: ["한국어로 작성", "표 포함"],
        acceptanceCriteria: ["요약 섹션 포함", "xlsx 파일 생성"],
        verificationMode: "full",
        currentPlan: [],
        completedSteps: [],
        blockers: [],
      },
    });
  });

  it("records deterministic verification evidence on the contract", () => {
    const store = new ExecutionContractStore({ now: () => 456 });

    store.recordVerificationEvidence({
      source: "beforeCommit",
      command: "npm test",
      status: "passed",
      detail: "12 tests passed",
    });

    expect(store.snapshot().taskState.verificationEvidence).toEqual([
      {
        evidenceId: "ev_1_co",
        source: "beforeCommit",
        command: "npm test",
        status: "passed",
        detail: "12 tests passed",
        recordedAt: 456,
      },
    ]);
  });

  it("tracks acceptance criteria as executable first-class state", () => {
    const store = new ExecutionContractStore({ now: () => 1000 });

    store.startTurn({
      userMessage: [
        "<task_contract>",
        "<acceptance_criteria>",
        "<item>unit tests pass</item>",
        "<item>resource stays under reports/</item>",
        "</acceptance_criteria>",
        "<verification_mode>full</verification_mode>",
        "</task_contract>",
      ].join("\n"),
    });

    const criteria = store.snapshot().taskState.criteria;
    expect(criteria).toHaveLength(2);
    expect(criteria.map((c) => c.text)).toEqual([
      "unit tests pass",
      "resource stays under reports/",
    ]);
    expect(criteria.every((c) => c.status === "pending")).toBe(true);
    expect(store.snapshot().taskState.acceptanceCriteria).toEqual([
      "unit tests pass",
      "resource stays under reports/",
    ]);
  });

  it("marks linked criteria passed when verification evidence is recorded", () => {
    const store = new ExecutionContractStore({ now: () => 1000 });
    store.startTurn({
      userMessage:
        "<task_contract><acceptance_criteria><item>unit tests pass</item></acceptance_criteria></task_contract>",
    });
    const criterionId = store.snapshot().taskState.criteria[0]!.id;

    store.recordVerificationEvidence({
      source: "beforeCommit",
      status: "passed",
      command: "npm test",
      criterionIds: [criterionId],
      detail: "1 test passed",
    });

    expect(store.snapshot().taskState.criteria[0]).toMatchObject({
      id: criterionId,
      status: "passed",
    });
    expect(store.snapshot().taskState.criteria[0]!.evidenceIds).toHaveLength(1);
  });

  it("reports unmet required criteria separately from generic evidence", () => {
    const store = new ExecutionContractStore({ now: () => 1000 });
    store.startTurn({
      userMessage: [
        "<task_contract>",
        "<acceptance_criteria>",
        "<item>unit tests pass</item>",
        "<item>artifact delivered</item>",
        "</acceptance_criteria>",
        "</task_contract>",
      ].join("\n"),
    });
    const firstId = store.snapshot().taskState.criteria[0]!.id;

    store.recordVerificationEvidence({
      source: "beforeCommit",
      status: "passed",
      command: "npm test",
      criterionIds: [firstId],
      detail: "tests passed",
    });

    expect(store.unmetRequiredCriteria().map((c) => c.text)).toEqual([
      "artifact delivered",
    ]);
  });

  it("parses structured resource bindings from task_contract", () => {
    const store = new ExecutionContractStore({ now: () => 1 });
    store.startTurn({
      userMessage: [
        "<task_contract>",
        "<resource_bindings mode=\"enforce\">",
        "<allowed_workspace_paths><item>reports/</item><item>data/source.csv</item></allowed_workspace_paths>",
        "<allowed_source_paths><item>kb://collection/source-a</item></allowed_source_paths>",
        "<artifact_ids><item>artifact_123</item></artifact_ids>",
        "<db_handles><item>primary_customer_db</item></db_handles>",
        "</resource_bindings>",
        "</task_contract>",
      ].join("\n"),
    });

    expect(store.snapshot().taskState.resourceBindings).toEqual({
      mode: "enforce",
      allowedWorkspacePaths: ["reports/", "data/source.csv"],
      allowedSourcePaths: ["kb://collection/source-a"],
      artifactIds: ["artifact_123"],
      resourceIds: [],
      dbHandles: ["primary_customer_db"],
    });
  });

  it("records used resource provenance on the contract", () => {
    const store = new ExecutionContractStore({ now: () => 1 });
    store.recordUsedResource({
      kind: "workspace_path",
      value: "reports/final.md",
      toolName: "FileRead",
      toolUseId: "tu_1",
    });

    expect(store.snapshot().taskState.usedResources).toEqual([
      {
        kind: "workspace_path",
        value: "reports/final.md",
        toolName: "FileRead",
        toolUseId: "tu_1",
        recordedAt: 1,
      },
    ]);
  });

  it("records memory recall metadata for the current turn", () => {
    const store = new ExecutionContractStore({ now: () => 123 });
    store.recordMemoryRecall({
      turnId: "turn-1",
      source: "qmd",
      path: "memory/old.md",
      continuity: "background",
      distinctivePhrases: ["한국식 vs 일본식 이름 선택"],
    });

    expect(store.memoryRecallForTurn("turn-1")).toEqual([
      expect.objectContaining({
        path: "memory/old.md",
        continuity: "background",
        recordedAt: 123,
      }),
    ]);
    expect(store.memoryRecallForTurn("other-turn")).toEqual([]);
  });

  it("replaces memory recall metadata for the same turn", () => {
    const store = new ExecutionContractStore({ now: () => 123 });
    store.recordMemoryRecall({
      turnId: "turn-1",
      source: "qmd",
      path: "memory/old.md",
      continuity: "background",
      distinctivePhrases: ["한국식 vs 일본식 이름 선택"],
    });
    store.replaceMemoryRecallForTurn("turn-1", [
      {
        turnId: "turn-1",
        source: "root",
        path: "memory/ROOT.md",
        continuity: "background",
        distinctivePhrases: ["root summary"],
      },
    ]);

    const records = store.memoryRecallForTurn("turn-1");
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({
      source: "root",
      path: "memory/ROOT.md",
      distinctivePhrases: ["root summary"],
      recordedAt: 123,
    });
  });

  it("records LLM-classified deterministic requirements as first-class state", () => {
    const store = new ExecutionContractStore({ now: () => 2000 });

    store.recordDeterministicRequirement({
      requirementId: "dr_1",
      source: "llm_classifier",
      status: "active",
      kinds: ["date_range", "calculation"],
      reason: "User asks for an average over the recent 30-day sales period.",
      suggestedTools: ["Clock", "DateRange", "Calculation"],
      acceptanceCriteria: [
        "Determine the date range with runtime clock evidence.",
        "Calculate the average with deterministic calculation evidence.",
      ],
    });

    expect(store.snapshot().taskState.deterministicRequirements).toEqual([
      {
        requirementId: "dr_1",
        source: "llm_classifier",
        status: "active",
        kinds: ["date_range", "calculation"],
        reason: "User asks for an average over the recent 30-day sales period.",
        suggestedTools: ["Clock", "DateRange", "Calculation"],
        acceptanceCriteria: [
          "Determine the date range with runtime clock evidence.",
          "Calculate the average with deterministic calculation evidence.",
        ],
        evidenceIds: [],
        createdAt: 2000,
        updatedAt: 2000,
      },
    ]);
  });

  it("records deterministic evidence and marks the linked requirement satisfied", () => {
    const store = new ExecutionContractStore({ now: () => 3000 });
    store.recordDeterministicRequirement({
      requirementId: "dr_1",
      source: "llm_classifier",
      status: "active",
      kinds: ["calculation"],
      reason: "Need exact average.",
      suggestedTools: ["Calculation"],
      acceptanceCriteria: [],
    });

    store.recordDeterministicEvidence({
      evidenceId: "de_1",
      requirementIds: ["dr_1"],
      toolName: "Calculation",
      toolUseId: "tu_1",
      kind: "calculation",
      status: "passed",
      inputSummary: "average amount over 3 rows",
      output: {
        operation: "average",
        field: "amount",
        result: 20,
        rowCount: 3,
      },
      assertions: ["rowCount=3", "sum=60", "average=20"],
      resources: ["workspace:data/sales.csv"],
    });

    const snapshot = store.snapshot();
    expect(snapshot.taskState.deterministicEvidence).toEqual([
      {
        evidenceId: "de_1",
        requirementIds: ["dr_1"],
        toolName: "Calculation",
        toolUseId: "tu_1",
        kind: "calculation",
        status: "passed",
        inputSummary: "average amount over 3 rows",
        output: {
          operation: "average",
          field: "amount",
          result: 20,
          rowCount: 3,
        },
        assertions: ["rowCount=3", "sum=60", "average=20"],
        resources: ["workspace:data/sales.csv"],
        recordedAt: 3000,
      },
    ]);
    expect(snapshot.taskState.deterministicRequirements[0]).toMatchObject({
      requirementId: "dr_1",
      status: "satisfied",
      evidenceIds: ["de_1"],
      updatedAt: 3000,
    });
  });

  it("keeps simple file understanding turns on the light path", () => {
    const store = new ExecutionContractStore({ now: () => 789 });

    store.startTurn({
      userMessage: "WSJ 파이프라인 뭐하는건지 알려줘",
    });

    const snapshot = store.snapshot();
    expect(snapshot.control).toEqual({
      mode: "light",
      reason: "simple_file_understanding",
    });
    expect(shouldInjectExecutionContract(snapshot)).toBe(false);
  });

  it("keeps existing file delivery turns on the light path", () => {
    const store = new ExecutionContractStore({ now: () => 790 });

    store.startTurn({
      userMessage: "여기서 파일로 줘",
    });

    expect(store.snapshot().control).toEqual({
      mode: "light",
      reason: "deliver_existing_file",
    });
  });

  it("keeps explicit existing-file chat delivery turns on the light path", () => {
    expect(
      classifyExecutionControl(
        "wsj_pipeline/WSJ_PIPELINE_HANDBOOK.md 이거 채팅으로 전달해줘",
      ),
    ).toEqual({
      mode: "light",
      reason: "deliver_existing_file",
    });
  });

  it("uses heavy control for state-changing document generation", () => {
    expect(classifyExecutionControl("리포트를 docx 파일로 만들어줘")).toEqual({
      mode: "heavy",
      reason: "state_changing_or_risky_action",
    });
  });
});

describe("completionClaimNeedsContractVerification", () => {
  it("requires evidence before completion claims when acceptance criteria exist", () => {
    const store = new ExecutionContractStore({ now: () => 1 });
    store.startTurn({
      userMessage:
        "<task_contract><acceptance_criteria><item>테스트 통과</item></acceptance_criteria></task_contract>",
    });

    expect(
      completionClaimNeedsContractVerification(
        store.snapshot(),
        "완료했습니다. 테스트도 통과했습니다.",
      ),
    ).toBe(true);

    const criterionId = store.snapshot().taskState.criteria[0]!.id;
    store.recordVerificationEvidence({
      source: "beforeCommit",
      status: "passed",
      criterionIds: [criterionId],
      detail: "npm test passed",
    });

    expect(
      completionClaimNeedsContractVerification(
        store.snapshot(),
        "완료했습니다. 테스트도 통과했습니다.",
      ),
    ).toBe(false);
  });

  it("does not carry old acceptance criteria into a later light read/explain turn", () => {
    const store = new ExecutionContractStore({ now: () => 1 });
    store.startTurn({
      userMessage:
        "<task_contract><acceptance_criteria><item>테스트 통과</item></acceptance_criteria></task_contract>",
    });
    store.startTurn({
      userMessage: "WSJ 파이프라인 뭐하는건지 알려줘",
    });

    expect(store.snapshot().taskState.acceptanceCriteria).toEqual(["테스트 통과"]);
    expect(store.snapshot().control.mode).toBe("light");
    expect(
      completionClaimNeedsContractVerification(
        store.snapshot(),
        "설명 완료했습니다.",
      ),
    ).toBe(false);
  });
});

describe("buildSpawnWorkOrderPrompt", () => {
  it("wraps child prompts with explicit work order and acceptance criteria", () => {
    const store = new ExecutionContractStore({ now: () => 1 });
    store.startTurn({
      userMessage:
        "<task_contract><acceptance_criteria><item>파일 경로 보고</item></acceptance_criteria></task_contract>",
    });

    const prompt = buildSpawnWorkOrderPrompt({
      parent: store.snapshot(),
      childPrompt: "문서 작성 부분을 맡아줘.",
      persona: "writer",
      allowedTools: ["FileWrite"],
    });

    expect(prompt).toContain("<work_order>");
    expect(prompt).toContain("<acceptance_criteria>");
    expect(prompt).toContain("파일 경로 보고");
    expect(prompt).toContain("문서 작성 부분을 맡아줘.");
    expect(prompt).toContain("Do not modify files outside your assigned scope");
  });

  it("includes structured criteria and resource bindings in child work orders", () => {
    const store = new ExecutionContractStore({ now: () => 1 });
    store.startTurn({
      userMessage: [
        "<task_contract>",
        "<acceptance_criteria><item>tests pass</item></acceptance_criteria>",
        "<resource_bindings mode=\"enforce\">",
        "<allowed_workspace_paths><item>reports/</item></allowed_workspace_paths>",
        "</resource_bindings>",
        "</task_contract>",
      ].join("\n"),
    });

    const prompt = buildSpawnWorkOrderPrompt({
      parent: store.snapshot(),
      childPrompt: "Review report generation.",
      persona: "reviewer",
      allowedTools: ["FileRead"],
    });

    expect(prompt).toContain("<resource_bindings mode=\"enforce\">");
    expect(prompt).toContain("reports/");
    expect(prompt).toContain("<item id=\"");
    expect(prompt).toContain("tests pass");
  });
});
