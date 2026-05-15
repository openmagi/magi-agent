---
name: phase-gate-verify
description: Use when executing a multi-phase task (from task-pipeline) and need to verify a phase's output before advancing. Enforces deterministic sample checks between phases so errors don't propagate. MUST use when task_contract.verification_mode=full AND estimated_steps >= 2. Prevents the "Phase 2 bug discovered at Phase 5" failure mode where rework is expensive.
user_invocable: false
metadata:
  author: openmagi
  version: "1.0"
---

# phase-gate-verify — Deterministic Phase Boundary Verification

## Why

멀티-Phase 파이프라인에서 한 Phase의 오류가 다음 Phase로 전파되면 디버깅/재작업 비용이 눈덩이처럼 커진다. "15/15 샘플 PASS → 다음 Phase" 같은 LLM 자체평가는 원리적으로 빈 슬롯을 못 본다.

본 스킬: 각 Phase 종료 시 **결정론적 샘플 검증**을 강제. 20건 랜덤 샘플, 각 건에 대해 "출력이 원본과 실제로 매칭되는가" 확인 (LLM이 아닌 `grep`/`jq`/파일 존재 여부 같은 결정론).

## When to Use

**MUST use when:**
- task-pipeline 기반 멀티-Phase 실행 중
- `<task_contract>`에 `verification_mode: full` + `estimated_steps >= 2`
- 법률/재무/감사/시험/의료 자료 처리

**OPTIONAL when:**
- `verification_mode: sample` + 빠른 검증 원할 때 (줄여서 5건 샘플)
- 일반적인 bulk 작업 (pipeline의 기본 checkpoint로)

## The Protocol

### Step 1: 샘플 추출

Phase 종료 시 output에서 **랜덤 샘플** 추출. 고정 seed 사용 (재현성).

```bash
# Example: Phase 2 output이 phase2/final.json에 81개 문제가 있을 때
TOTAL=$(jq '. | length' phase2/final.json)
SAMPLE_SIZE=20  # verification_mode=full. sample 모드는 5.
SAMPLES=$(jq --argjson n "$SAMPLE_SIZE" '[range(0; $n)] | map(. * ('$TOTAL' / '$n') | floor)' phase2/final.json)
```

### Step 2: 각 샘플에 결정론적 check

**중요: LLM subjective evaluation 금지.** 아래 결정론적 check만 사용:

| Check 종류 | 방법 | 예시 |
|-----------|------|------|
| **파일 존재** | `[ -f "$path" ]` | 생성된 PDF가 실제 있나 |
| **패턴 매치** | `grep -qE pattern file` | 출력 포맷이 기대 패턴과 일치하나 |
| **숫자 범위** | `awk '$1>=N && $1<=M'` | page number 범위 내 |
| **교차 참조** | 원본 데이터와 diff | vision 결과 → 텍스트 레이어 grep 확인 |
| **개수 확인** | `wc -l`, `jq length` | 요청된 개수와 일치 |
| **페이지 범위** | PDF 페이지 수 확인 | page_start ≤ page_end ≤ total_pages |

### Step 3: PASS/FAIL 판정

```
sample_results/
  - check_1.txt: "PASS" or "FAIL: <reason>"
  - check_2.txt: ...
  ...

PASS_COUNT=$(grep -c "^PASS" sample_results/*.txt)
TOTAL=20
PASS_RATE=$((PASS_COUNT * 100 / TOTAL))

if [ $PASS_RATE -ge 95 ]; then
  echo "PHASE GATE: PASS ($PASS_COUNT/$TOTAL)"
  # advance
else
  echo "PHASE GATE: FAIL ($PASS_COUNT/$TOTAL, threshold 95%)"
  # do NOT advance — return to Phase 5 retry with failure samples as feedback
fi
```

### Step 4: FAIL 시 행동

**다음 Phase로 진행 금지.** 대신:

1. Failure samples 집계 → 패턴 분석
   - 같은 유형의 실패가 반복? → 전체 Phase 재실행
   - 산발적 실패? → 해당 샘플만 재시도
2. Phase 5-8 (전략 변경)으로 루프:
   - tool/스킬 실패 → 다른 스킬
   - 품질 저하 → decomposition 추가
   - constraint violation → contract 재해석
3. 2회 재시도 후에도 FAIL → 사용자에게 정직한 보고:
   ```
   Phase N에서 샘플 20건 중 X건 실패.
   원인: <실패 패턴 요약>
   계속 진행할까요 (부분 결과로), 다시 시도할까요, 또는 접근 변경할까요?
   ```

## Example: Dongwon 물권법 Case (Phase 2 종료 시점)

```bash
# Phase 2 output: phase2/final.json에 82 문제 (각각 {edition, qnum, page_start, year})
cd /workspace/projects/3_1_mid_물권선지/

# Sample 20 random entries (fixed seed for reproducibility)
SAMPLES=$(jq -r '.[] | select(.include)' phase2/final.json | shuf --random-source=<(yes 42) -n 20)

# For each sample: cross-verify with text-layer extract
echo "$SAMPLES" | while read -r problem; do
  PAGE=$(echo "$problem" | jq -r '.page_start')
  YEAR=$(echo "$problem" | jq -r '.year')
  QNUM=$(echo "$problem" | jq -r '.qnum')

  # Deterministic check: text-layer of that page contains the year?
  PAGE_TEXT=$(jq -r ".pages[$((PAGE-1))].text" text_layer.json)
  if echo "$PAGE_TEXT" | grep -qE "${YEAR}년"; then
    echo "PASS page=$PAGE year=$YEAR qnum=$QNUM" >> sample_results/phase2.txt
  else
    ACTUAL=$(echo "$PAGE_TEXT" | grep -oE '[0-9]{2}년' | head -1)
    echo "FAIL page=$PAGE claimed=$YEAR actual=$ACTUAL qnum=$QNUM" >> sample_results/phase2.txt
  fi
done

# 판정
PASS=$(grep -c "^PASS" sample_results/phase2.txt)
echo "Phase 2 gate: $PASS/20"
# 10/20 = FAIL → 전체 +2 offset 오류 즉시 감지
```

이 케이스에서 Dongwon 프로젝트는 Phase 5-3까지 가서야 50% FAIL을 발견. phase-gate-verify를 Phase 2 종료에 걸었다면 동일 오류를 5단계 일찍 잡았을 것.

## 성공 기준 vs 샘플링 환상 (R12 보충)

- "15/15 sample PASS"만으로는 **결함 없음을 의미하지 않는다** (레이아웃 결함 같은 건 샘플링으로 못 잡음)
- `verification_mode: full`인 경우 sample gate PASS **+ 전수 검증** 둘 다 통과해야 "fully-verified" 선언
- phase-gate-verify는 **샘플 sanity check** 역할. 최종 납품은 전수 검증 스킬 (pdf-extract-robust의 cross-verification 등)이 담당.

## Output Format (phase 완료 보고)

```
[phase-gate-verify]
phase: <N>
samples: <size>
pass: <count>/<size> = <pct>%
threshold: 95%
result: PASS | FAIL
failure_patterns: (if FAIL)
  - <pattern 1> (N occurrences)
  - <pattern 2> (M occurrences)
next_action: advance | retry_phase | request_user
```

## Important Rules

- **Deterministic only** — grep/jq/awk/file-exists. LLM evaluation 금지 (sampling bias 재발).
- **Fixed seed for reproducibility** — 같은 phase 재실행 시 같은 샘플이어야 디버깅 가능.
- **Never advance on FAIL** — <95% PASS면 진행 금지. Phase 5 retry로 루프.
- **Fail fast** — Phase 2 오류가 Phase 5에서 발견되는 상황 원천 차단.
