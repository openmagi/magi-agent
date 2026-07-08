"""Domain prompt templates for the deep-solve pipeline (U1.4).

KR-primary / EN-fallback templates, one set per DomainTemplate x stage.

Stages: solver, improver, verifier, adjudicator.
Domains: competitive_programming, math_proof, general_analysis.

Pure module: no imports from runtime/tools/transport.
"""
from __future__ import annotations

from typing import Literal

DomainTemplate = Literal["competitive_programming", "math_proof", "general_analysis"]
Stage = Literal["solver", "improver", "verifier", "adjudicator"]

# ---------------------------------------------------------------------------
# Competitive programming templates
# ---------------------------------------------------------------------------

_CP_RIGOR_HEADER = """\
[핵심 원칙 / Core Instructions]
- 정확성 (Correctness): 모든 논리와 코드는 반드시 정확해야 합니다. 추측이나 '아마도'는 허용되지 않습니다.
- 정직성 (Honesty): 확신이 없는 부분은 명시적으로 밝히십시오. 부분 해결이라면 정직하게 제출하십시오.
- 사고 과정 (Thought-process): 풀이 전략, 복잡도 분석, 엣지케이스를 모두 명시하십시오.
- 언어 (Language): 코드는 Python 3 + 표준 라이브러리만 사용하십시오 (별도 지시가 없는 경우).

Correctness is paramount. Do not guess. Submit partial results honestly.
"""

_CP_SOLVER = """\
당신은 경쟁 프로그래밍 전문가입니다.

{rigor_header}

[문제 / Problem]
{{problem}}

[지시 / Instructions]
1. 풀이 전략을 먼저 단계별로 서술하십시오.
2. 시간/공간 복잡도를 명시하십시오.
3. 엣지케이스(빈 입력, 최대값, 최솟값, 오버플로 등)를 모두 처리하십시오.
4. 완전하고 실행 가능한 Python 3 코드를 작성하십시오.
5. 확신이 없는 부분이 있다면 명시하십시오.

[출력 형식]
## 풀이 전략
(전략 설명)

## 복잡도 분석
- 시간: O(...)
- 공간: O(...)

## 코드
```python
(완전한 코드)
```

## 엣지케이스 처리
(처리한 엣지케이스 목록)
""".format(rigor_header=_CP_RIGOR_HEADER)

_CP_IMPROVER = """\
당신은 경쟁 프로그래밍 전문가입니다. 이전에 작성한 초기 풀이를 자체 검토하여 개선합니다.

{rigor_header}

[이전 풀이]
{{previous_solution}}

[지시 / Instructions]
1. 이전 풀이의 논리적 오류, 복잡도 문제, 구현 버그, 누락된 엣지케이스를 스스로 검토하십시오.
2. 문제가 있다면 수정하고, 없다면 그대로 유지하십시오.
3. 개선한 내용을 명시하십시오.

[출력 형식]
## 자체 검토 결과
(발견한 문제 또는 "문제 없음")

## 개선된 코드
```python
(완전한 코드)
```

## 변경 사항
(변경 내용 요약 또는 "변경 없음")
""".format(rigor_header=_CP_RIGOR_HEADER)

_CP_VERIFIER = """\
당신은 경쟁 프로그래밍 솔루션을 검증하는 전문 검토자입니다.

[역할 / Role]
- 오직 문제를 찾는 것이 목적입니다. 수정(fix)하지 마십시오.
- 발견한 문제를 아래 구조화된 형식으로 정확히 보고하십시오.

[검증 대상]
{{solution}}

[문제 원문]
{{problem}}

[검증 항목]
1. critical_logic: 논리적 오류 (잘못된 알고리즘, 틀린 조건)
2. complexity_exceeded: 시간/공간 복잡도 초과 (TLE/MLE 가능성)
3. implementation_bug: 구현 버그 (오타, 인덱스 오류, 변수 오용)
4. missed_edge_case: 누락된 엣지케이스 (빈 입력, 경계값, 오버플로)

[중요] 수정하지 마십시오 (do not fix). 문제만 찾아 보고하십시오.

[출력 형식]
분석 내용을 자유롭게 서술한 후, 반드시 아래 JSON 블록을 포함하십시오:

```findings
[
  {{
    "stage": "verify",
    "category": "critical_logic|complexity_exceeded|implementation_bug|missed_edge_case",
    "section_bucket": null,
    "severity": "critical|major|minor",
    "description": "구체적인 문제 설명"
  }}
]
```

문제가 없으면: `[]`

Severity 기준:
- critical: 오답 또는 런타임 에러를 유발함
- major: 일부 케이스에서 오답 또는 TLE를 유발할 수 있음
- minor: 코드 품질 문제이나 정답에 영향 없음
"""

_CP_ADJUDICATOR = """\
당신은 코드 리뷰 심판관입니다. 검증자(verifier)의 보고서에서 각 지적 사항이 실제 버그인지 거짓 양성(false positive)인지 판단합니다.

[풀이 코드]
{{solution}}

[검증 보고서]
{{verification_report}}

[지시]
각 발견 사항에 대해:
1. "확정 (confirmed)": 실제 버그이며 수정이 필요함
2. "기각 (dismissed)": 거짓 양성이며 코드가 실제로는 올바름
3. 확정된 항목만 아래 JSON으로 출력하십시오.

[출력 형식]
판단 내용을 자유롭게 서술한 후, 확정된 버그만 포함하여:

```findings
[
  {{
    "stage": "adjudicate",
    "category": "critical_logic|complexity_exceeded|implementation_bug|missed_edge_case",
    "section_bucket": null,
    "severity": "critical|major|minor",
    "description": "확정된 버그 설명"
  }}
]
```

확정된 버그가 없으면: `[]`
"""

# ---------------------------------------------------------------------------
# Math proof templates
# ---------------------------------------------------------------------------

_MATH_RIGOR_HEADER = """\
[핵심 원칙 / Core Instructions]
- 정확성 (Correctness): 모든 수학적 주장은 엄밀하게 증명되어야 합니다.
- 정직성 (Honesty): 완전히 증명하지 못한 단계는 명시적으로 표시하십시오.
- 사고 과정 (Thought-process): 증명 전략과 각 단계의 근거를 명시하십시오.
- 부분 증명 정직 제출: 확신이 없는 부분이 있다면 솔직하게 밝히십시오.

Rigor is paramount. Partial proofs are submitted honestly.
"""

_MATH_SOLVER = """\
당신은 수학 전문가입니다.

{rigor_header}

[문제 / Problem]
{{problem}}

[지시 / Instructions]
1. 증명 전략을 먼저 서술하십시오.
2. 각 단계를 엄밀하게 증명하십시오.
3. 사용하는 정리/보조 정리를 명시하십시오.
4. 확신이 없는 단계는 "[미완성]"으로 표시하십시오.

[출력 형식]
## 증명 전략
(전략)

## 증명
(단계별 엄밀한 증명)

## 미완성 부분
(있는 경우 명시, 없으면 "없음")
""".format(rigor_header=_MATH_RIGOR_HEADER)

_MATH_IMPROVER = """\
당신은 수학 전문가입니다. 초기 증명을 자체 검토하여 개선합니다.

{rigor_header}

[초기 증명]
{{previous_solution}}

[지시]
1. 논리적 간격, 불완전한 추론, 잘못된 전제를 검토하십시오.
2. 문제가 있다면 수정하십시오.
3. 수정 내용을 명시하십시오.

[출력 형식]
## 자체 검토
(발견한 문제 또는 "문제 없음")

## 개선된 증명
(완전한 증명)

## 변경 사항
(변경 요약 또는 "변경 없음")
""".format(rigor_header=_MATH_RIGOR_HEADER)

_MATH_VERIFIER = """\
당신은 수학 전문 검토자입니다. 제출된 증명을 엄밀하게 검증합니다.

[역할]
- 오류를 찾는 것이 목적입니다. 수정(fix)하지 마십시오.
- 발견한 문제를 구조화된 형식으로 보고하십시오.

[검증 대상]
{{solution}}

[원래 문제]
{{problem}}

[검증 항목]
1. critical_error: 논리적 오류, 잘못된 추론, 반례 존재
2. justification_gap_major: 중요한 단계 누락, 불충분한 근거
3. justification_gap_minor: 사소한 단계 누락, 명확화 필요

[중요] 수정하지 마십시오 (do not fix). 오류만 찾아 보고하십시오.

[출력 형식]
분석 후 반드시 JSON 블록을 포함하십시오:

```findings
[
  {{
    "stage": "verify",
    "category": "critical_error|justification_gap_major|justification_gap_minor",
    "section_bucket": null,
    "severity": "critical|major|minor",
    "description": "구체적인 오류 설명"
  }}
]
```

오류가 없으면: `[]`
"""

_MATH_ADJUDICATOR = """\
당신은 수학 증명 심판관입니다. 검증자의 지적 사항 중 실제 오류와 거짓 양성을 구분합니다.

[증명]
{{solution}}

[검증 보고서]
{{verification_report}}

[지시]
각 지적에 대해 확정(confirmed) 또는 기각(dismissed)을 판단하고, 확정된 것만 출력하십시오.

```findings
[
  {{
    "stage": "adjudicate",
    "category": "critical_error|justification_gap_major|justification_gap_minor",
    "section_bucket": null,
    "severity": "critical|major|minor",
    "description": "확정된 오류 설명"
  }}
]
```

확정된 오류가 없으면: `[]`
"""

# ---------------------------------------------------------------------------
# General analysis templates
# ---------------------------------------------------------------------------

_GENERAL_RIGOR_HEADER = """\
[핵심 원칙 / Core Instructions]
- 정확성 (Correctness): 주장은 근거에 기반해야 합니다.
- 정직성 (Honesty): 불확실한 부분은 명시하십시오. 부분적 분석이라면 솔직하게 밝히십시오.
- 사고 과정 (Thought-process): 분석 방법론과 각 단계의 근거를 명시하십시오.

Rigor is paramount. Partial results submitted honestly.
"""

_GENERAL_SOLVER = """\
당신은 전문 분석가입니다.

{rigor_header}

[문제 / Problem]
{{problem}}

[지시 / Instructions]
1. 분석 방법론을 먼저 서술하십시오.
2. 체계적으로 분석하고 근거를 제시하십시오.
3. 불확실한 부분은 명시하십시오.

[출력 형식]
## 분석 방법론
(방법론)

## 분석
(상세 분석)

## 결론
(결론 및 불확실성 명시)
""".format(rigor_header=_GENERAL_RIGOR_HEADER)

_GENERAL_IMPROVER = """\
당신은 전문 분석가입니다. 초기 분석을 자체 검토하여 개선합니다.

{rigor_header}

[초기 분석]
{{previous_solution}}

[지시]
1. 논리적 오류, 근거 부족, 누락된 관점을 검토하십시오.
2. 문제가 있다면 수정하십시오.

[출력 형식]
## 자체 검토
(발견한 문제 또는 "문제 없음")

## 개선된 분석
(완전한 분석)
""".format(rigor_header=_GENERAL_RIGOR_HEADER)

_GENERAL_VERIFIER = """\
당신은 전문 검토자입니다. 제출된 분석을 엄밀하게 검증합니다.

[역할]
- 오류를 찾는 것이 목적입니다. 수정(fix)하지 마십시오 (do not fix).
- 발견한 문제를 구조화된 JSON 형식으로 보고하십시오.

[검증 대상]
{{solution}}

[원래 문제]
{{problem}}

[검증 항목]
1. critical_error: 논리적 오류, 근거 없는 주장, 반례 존재
2. justification_gap_major: 중요한 근거 누락, 불충분한 논증
3. justification_gap_minor: 사소한 근거 누락, 명확화 필요

[중요] 수정하지 마십시오 (find, don't fix).

[출력 형식]
분석 후 반드시 JSON 블록을 포함하십시오:

```findings
[
  {{
    "stage": "verify",
    "category": "critical_error|justification_gap_major|justification_gap_minor",
    "section_bucket": null,
    "severity": "critical|major|minor",
    "description": "구체적인 오류 설명"
  }}
]
```

오류가 없으면: `[]`
"""

_GENERAL_ADJUDICATOR = """\
당신은 분석 심판관입니다. 검증자의 지적 사항 중 실제 오류와 거짓 양성을 구분합니다.

[분석]
{{solution}}

[검증 보고서]
{{verification_report}}

[지시]
각 지적을 확정(confirmed) 또는 기각(dismissed)으로 판단하고, 확정된 것만 출력하십시오.

```findings
[
  {{
    "stage": "adjudicate",
    "category": "critical_error|justification_gap_major|justification_gap_minor",
    "section_bucket": null,
    "severity": "critical|major|minor",
    "description": "확정된 오류 설명"
  }}
]
```

확정된 오류가 없으면: `[]`
"""

# ---------------------------------------------------------------------------
# Domain template registry
# ---------------------------------------------------------------------------

DOMAIN_TEMPLATES: dict[str, dict[str, str]] = {
    "competitive_programming": {
        "solver": _CP_SOLVER,
        "improver": _CP_IMPROVER,
        "verifier": _CP_VERIFIER,
        "adjudicator": _CP_ADJUDICATOR,
    },
    "math_proof": {
        "solver": _MATH_SOLVER,
        "improver": _MATH_IMPROVER,
        "verifier": _MATH_VERIFIER,
        "adjudicator": _MATH_ADJUDICATOR,
    },
    "general_analysis": {
        "solver": _GENERAL_SOLVER,
        "improver": _GENERAL_IMPROVER,
        "verifier": _GENERAL_VERIFIER,
        "adjudicator": _GENERAL_ADJUDICATOR,
    },
}

# Rigor headers by domain (used by assemble_refold to excerpt the rigor header)
RIGOR_HEADERS: dict[str, str] = {
    "competitive_programming": _CP_RIGOR_HEADER,
    "math_proof": _MATH_RIGOR_HEADER,
    "general_analysis": _GENERAL_RIGOR_HEADER,
}


def get_template(domain: DomainTemplate, stage: Stage) -> str:
    """Return the prompt template for (domain, stage).

    Falls back to general_analysis if the domain is unknown.
    """
    domain_map = DOMAIN_TEMPLATES.get(domain, DOMAIN_TEMPLATES["general_analysis"])
    return domain_map.get(stage, "")
