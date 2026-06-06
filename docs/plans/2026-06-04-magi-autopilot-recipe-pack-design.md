# magi-autopilot 1st-party 레시피 팩 — 설계안

- **날짜:** 2026-06-04
- **출처/동기:** `Yeachan-Heo/oh-my-codex`(OMX)의 `$autopilot` strict-FSM 워크플로를
  Magi Agent(Python ADK)의 1st-party 레시피 팩/하네스로 포팅.
- **관련 분석:** `docs/plans/2026-06-03-magi-coding-harness-improvement-analysis.md`,
  `docs/plans/2026-06-03-magi-always-on-improvement-analysis.md`

## 1. 무엇을 가져오는가

OMX `$autopilot`의 엄격 FSM:

```
deep-interview → ralplan(consensus) → ultragoal(+team) → code-review → ultraqa
                  ▲                                                       │
                  └──────────── gate 실패 시 plan 단계로 복귀 ───────────┘
```

유일 정상 종료 = `code-review` clean + `ultraqa` pass(또는 명시적 skip).
이 흐름의 가치는 **새 프리미티브가 아니라 기존 하네스 프리미티브를 게이트된 단일
흐름으로 엮는 합성 레이어**라는 점이다. Magi는 `plan_gate` / `verifier_bus` /
`goal_loop` / `parallel_execution` / hooks를 이미 보유하나 이를 하나로 묶은
오토파일럿이 없다.

## 2. 설계 철칙 (기존 코드베이스 규율 준수)

Magi Agent 하네스/레시피 레이어는 전부 **metadata-only · traffic-free ·
default-off** 규율을 따른다. autopilot도 동일하게:

- 모든 신규 모델은 frozen pydantic, `*_attached: Literal[False]`, `enabled=False`
  디폴트, opt-out 지원. (참조: `goal_loop.GoalLoopPolicy`,
  `plan_gate.PlanGateDecisionSnapshot`)
- 레시피 매니페스트는 refs만 선언(`RecipePackManifest`), 라이브 실행 금지
  (manifest validator가 `*_attached`/live refs 거부).
- FSM 전이는 **순수 함수**(I/O·실행 없음)로 모델링. 실제 ADK 런타임 부착은 후속
  PR에서 callbacks/plugins + gate5b shadow로 단계적 활성.
- **배치 위치:** canonical 런타임은 OSS `openmagi/magi-agent`이다. 구현 위치는
  `magi_agent/...`이며, 다른 배포 환경으로의 동기화는 이 설계의 범위 밖이다.

## 3. 컴포넌트 & 파일 구조

```
magi_agent/
├── harness/
│   └── autopilot.py            # [신규] FSM phase enum + policy + 전이 스냅샷 + 순수 전이함수
├── recipes/
│   └── compiler.py             # [수정] _first_party_packs()에 openmagi.autopilot 매니페스트 추가
└── harness/
    ├── presets.py              # [수정] autopilot 프리셋(default-off, env-gated) + verifier_gates
    └── verifier_bus.py         # [수정] gate 정의 추가: consensus-approval, adversarial-qa, interview-ambiguity
```

### 3.1 `harness/autopilot.py` (신규)

```python
AUTOPILOT_FEATURE_KEY = "autopilot-fsm"

class AutopilotPhase(StrEnum):
    INTERVIEW = "interview"   # deep-interview: 정량 ambiguity 게이트
    PLAN      = "plan"        # consensus: Architect→Critic 이중 승인
    EXECUTE   = "execute"     # goal_loop(+parallel_execution when team)
    REVIEW    = "review"      # verifier_bus code-review gate
    QA        = "qa"          # adversarial QA gate
    COMPLETE  = "complete"    # 정상 종료(터미널)
    BLOCKED   = "blocked"     # 자격증명/반복실패/유저중단(터미널)

class AutopilotGateVerdict(StrEnum):
    PASS = "pass"; FAIL = "fail"; SKIP = "skip"   # qa만 skip 허용

class AutopilotFsmPolicy(BaseModel):   # frozen
    feature_key: Literal["autopilot-fsm"] = AUTOPILOT_FEATURE_KEY
    enabled: bool = False
    max_review_cycles: int = 3          # 무한 review↔plan 루프 방지
    qa_skip_allowed_for_nonruntime: bool = True
    opt_out: ...                        # goal_loop 패턴 재사용
    traffic_attached: Literal[False] = False
    execution_attached: Literal[False] = False
    # validator: *_attached 거짓 강제, enabled=False면 스케줄 불가

class AutopilotPhaseTransition(BaseModel):   # frozen, plan_gate 스냅샷 패턴
    decision_id: str; session_key: str; turn_id: str
    from_phase: AutopilotPhase; to_phase: AutopilotPhase
    gate: str                            # 어떤 게이트가 전이를 일으켰는지
    verdict: AutopilotGateVerdict
    review_cycle: int = 0
    return_to_plan_reason: str | None = None   # gate FAIL시 필수
    route_attached: Literal[False] = False
    # ... transcript/evidence ref 필드

def evaluate_autopilot_transition(
    *, current: AutopilotPhase, verdict: AutopilotGateVerdict,
    review_cycle: int, policy: AutopilotFsmPolicy,
) -> AutopilotPhaseTransition:
    """순수 함수. gate verdict → 다음 phase 결정. I/O·실행 없음.
    - INTERVIEW pass → PLAN
    - PLAN pass → EXECUTE
    - EXECUTE pass → REVIEW
    - REVIEW fail → PLAN (review_cycle+1; max 초과 시 BLOCKED)
    - REVIEW pass → QA
    - QA skip(비런타임) / pass → COMPLETE
    - QA fail → PLAN (return_to_plan_reason 기록)
    """
```

### 3.2 `recipes/compiler.py` — 매니페스트 등록

`_first_party_packs()` 튜플에 추가 (default-off, opt-out, customizable):

```python
RecipePackManifest(
    packId="openmagi.autopilot",
    displayName="Autopilot",
    description="Strict autonomous FSM: interview → consensus-plan → execute → review → adversarial-QA with gate-failure return-to-plan.",
    taskProfileSelectors=("autopilot", "autonomous", "full-auto", "build-me"),
    dependsOnPackIds=(
        "openmagi.agent-methodology",   # plan/tdd/verification/review refs 재사용
        "openmagi.dev-coding",          # diff-capture / tdd-verification / git-diff evidence
    ),
    instructionRefs=("instruction:autopilot:strict-loop-contract",),
    callbackRefs=("callback:autopilot:phase-router",),
    validatorRefs=(
        "validator:autopilot:interview-ambiguity-cleared",
        "validator:autopilot:consensus-architect-then-critic",
        "validator:autopilot:review-clean",
        "validator:autopilot:qa-passed-or-skipped",
        "validator:autopilot:max-review-cycle-bounded",
    ),
    approvalGateRefs=(
        "approval:autopilot:execution-lane",
        "approval:autopilot:live-behavior",
    ),
    checkpointRefs=(
        "checkpoint:autopilot:interview",
        "checkpoint:autopilot:consensus-plan",
        "checkpoint:autopilot:execute",
        "checkpoint:autopilot:review",
        "checkpoint:autopilot:qa",
        "checkpoint:autopilot:return-to-plan",
    ),
    evidenceRefs=(
        "evidence:autopilot:clarified-spec",
        "evidence:autopilot:consensus-record",
        "evidence:autopilot:phase-transition",
    ),
    auditRefs=("audit:autopilot:fsm-lifecycle",),
    adkPrimitiveOwnership=common_adk_owners,
    openmagiBoundaryOwnership=common_openmagi_owners + (
        "OpenMagi autopilot owns recipe-selected FSM transition metadata; "
        "live phase driving attaches through ADK callbacks/plugins later",
    ),
    callbackSetMetadata=("CallbackSet:autopilot:phase-router-metadata-only",),
    validatorSetMetadata=("ValidatorSet:autopilot:fsm-gates-metadata-only",),
    approvalGateMetadata=("ApprovalGate:autopilot:metadata-only",),
),
```

**게이팅(과부하 방지):** `ProfileResolver`는 `task_profile_selectors`에 매칭될 때만
선택하므로, 단발 단순 요청에는 자동 발동하지 않는다. `task_type`이 위 selector이거나
명시적 pack enable일 때만 autopilot이 켜짐.

### 3.3 `harness/presets.py` — 프리셋 배선

`AUTOPILOT` env-gate, default-off로 phase 게이트를 hook point에 매핑:

| preset key | hook_points | verifier_gates |
|---|---|---|
| `autopilot-phase-router` | `beforeTurnStart` | (라우팅, blocking, fail-open) |
| `autopilot-interview-gate` | `beforeLLMCall` | `interview-ambiguity` |
| `autopilot-consensus-gate` | `onTaskCheckpoint` | `consensus-approval` |
| `autopilot-review-gate` | `afterCommit` | `coding-child-review`(재사용)+`review-clean` |
| `autopilot-qa-gate` | `afterTurnEnd` | `adversarial-qa` |

모두 `category=TASK`, `default_on=False`, `env_gates=("MAGI_AUTOPILOT",)`.

### 3.4 기존 프리미티브 재사용 (배선 지점)

| FSM phase | 재사용 프리미티브 |
|---|---|
| interview | `plan_gate` artifactKind=`"interview"` + 신규 ambiguity-score validator |
| plan | `plan_gate` artifactKind=`"consensus"` + verifier_bus `consensus-approval`(Architect→Critic 순서 강제) |
| execute | `goal_loop`(durable 멀티골) + `parallel_execution`(team 필요 시) |
| review | `verifier_bus` 코드리뷰 게이트(APPROVE/CLEAR) |
| qa | `verifier_bus` 신규 `adversarial-qa` 게이트 — 기존 injection-scanner / `dangerous-patterns` 프리셋 활용한 hostile 시나리오 매트릭스 |

`plan_gate.PlanGateArtifactKind`가 이미 `plan|interview|consensus`를 지원 → interview/
consensus 단계는 추가 모델 없이 바로 활용 가능.

## 4. PR 분해 (stacked, 전부 default-off)

1. **PR1 — FSM 코어:** `harness/autopilot.py`(phase enum + policy + transition 스냅샷 +
   순수 `evaluate_autopilot_transition`) + 단위 테스트. 배선/실행 없음. metadata·traffic-free.
2. **PR2 — 레시피 등록:** `_first_party_packs()`에 `openmagi.autopilot` 추가 +
   ProfileResolver 선택/의존성/사이클 테스트(agent-methodology, dev-coding 의존).
3. **PR3 — 게이트 정의:** `verifier_bus`에 `consensus-approval`(순서 강제) /
   `adversarial-qa` / `interview-ambiguity` 게이트 + `presets.py` autopilot 프리셋(env-gated).
4. **PR4 — interview ambiguity 스코어링:** 정량 ambiguity 컨트랙트(depth profile
   quick/standard/deep 임계값) 모델 + validator ref 구현.
5. **PR5 (후속, flag-on):** 라이브 부착 — ADK callbacks/plugins로 phase-router를
   실제 구동. gate5b 방식 shadow/dry-run 먼저, 그다음 canary, fleet. **OSS canonical
   먼저 → monorepo sync.**

## 5. 비목표 (가져오지 않음)

- tmux/HUD/worktree 런치, `--madmax`, team의 tmux 오케스트레이션 — Magi는 호스티드
  k8s+SSE 런타임. 서버사이드 병렬은 `parallel_execution`이 이미 커버.
- `omx setup/doctor/update/plugin-marketplace`, Codex 전용 훅/config — 배포 플럼빙, 무관.
- 멀티프로바이더 어드바이저(`$ask`) — Magi는 Claude-supremacy 라우터.

## 6. 열린 질문

1. PR4 ambiguity 스코어링을 PR1~3과 함께 묶을지, autopilot가 기본 plan_gate만 쓰고
   ambiguity는 별도 deep-interview 레시피로 분리할지.
2. `adversarial-qa` 게이트를 autopilot 전용으로 둘지, 독립 `openmagi.qa` 팩으로 빼서
   재사용 가능하게 할지.
3. PR5 라이브 부착의 owner 레포(OSS `openmagi/magi-agent` Track 19/GA 하네스와의 정합).
```
