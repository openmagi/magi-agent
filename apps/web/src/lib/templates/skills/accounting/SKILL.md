---
name: accounting
description: "Use for general accounting questions, K-IFRS/K-GAAP guidance, financial statement forensics, and routing to specialized accounting skills (cash-flow-statement, audit-report-draft, financial-statements, financial-statement-forensics)."
user_invocable: true
metadata:
  author: openmagi
  version: "2.0"
  category: accounting
---

# Accounting Hub (회계)

K-IFRS / K-GAAP 기반 회계 전문 스킬 허브. 일반 회계 질의응답 + 전문 스킬 라우팅.

## Specialized Skills

| Skill | Command | Use Case |
|-------|---------|----------|
| **cash-flow-statement** | `/현금흐름표` | 시산표/원장 → 현금흐름표 xlsx (K-IFRS 1007) |
| **audit-report-draft** | `/감사보고서` | 감사보고서 초안 docx (필수 주석 25개) |
| **financial-statements** | `/재무제표` | 재무상태표 + 포괄손익계산서 + 자본변동표 xlsx |
| **financial-statement-forensics** | `/재무제표포렌식` | 상장사 재무제표 포렌식, 이익품질, 공시·감사 리스크 분석 |
| **capital-allocation-quality** | `/자본배분` | 비영업자산, 주주환원, 재투자 효율, CapEx 품질 분석 |

### Auto-Routing

사용자 요청에 따라 자동으로 해당 스킬을 호출:

| User Says | Route To |
|-----------|----------|
| "현금흐름표 만들어줘", "cash flow" | cash-flow-statement |
| "감사보고서 초안", "audit report" | audit-report-draft |
| "재무제표 작성", "재무상태표", "손익계산서" | financial-statements |
| "재무제표 전체 세트" | financial-statements → cash-flow-statement (순차) |
| "재무제표 포렌식", "분식 가능성", "QoE", "회계 리스크" | financial-statement-forensics |
| "자본배분", "주주환원", "CapEx가 낭비인지 투자자인지", "현금 많은 회사 평가" | capital-allocation-quality |
| 일반 회계 질문, 기준서 해석 | 이 스킬에서 직접 답변 |

## General Accounting Guidance

이 스킬에서 직접 처리하는 범위:

### K-IFRS 기준서 해석
- 특정 기준서 조항의 의미 및 적용 방법 설명
- K-IFRS vs K-GAAP 차이 비교
- IFRS 최신 개정사항 안내

### 회계 처리 상담
- 계정과목 분류 판단
- 회계 추정 방법론 (감가상각, 손상, 공정가치)
- 연결/별도 회계 차이
- 수익인식 시점 및 방법 (K-IFRS 1115)
- 리스 회계 (K-IFRS 1116)

### 세무 연계
- 세무조정 관련 질문 → `tax-regulation-research` 스킬 참조
- 법인세 회계 (K-IFRS 1012)

## Proactive Questioning Protocol

전문 스킬로 라우팅하기 전, 사용자가 제공하지 않은 정보를 **한 번에** 물어보기:

| Required Information | Why |
|---------------------|-----|
| **Reporting period** | 회계기간 |
| **Entity name** | 법인명 |
| **Entity type** | 상장 / 비상장 / 중소기업 |
| **Applicable standards** | K-IFRS or K-GAAP |
| **Reporting basis** | 별도 or 연결 |

추가 정보는 각 전문 스킬에서 별도로 확인.

## Knowledge Base Integration

```bash
# 업로드된 회계 데이터 검색
system.run ["sh", "-c", "kb-search.sh '시산표'"]
system.run ["sh", "-c", "kb-search.sh '감사보고서'"]
system.run ["sh", "-c", "kb-search.sh '재무제표'"]
```

## Key K-IFRS References

| Standard | Topic |
|----------|-------|
| K-IFRS 1001 | 재무제표 표시 |
| K-IFRS 1002 | 재고자산 |
| K-IFRS 1007 | 현금흐름표 |
| K-IFRS 1012 | 법인세 |
| K-IFRS 1016 | 유형자산 |
| K-IFRS 1019 | 종업원급여 |
| K-IFRS 1033 | 주당이익 |
| K-IFRS 1036 | 자산손상 |
| K-IFRS 1037 | 충당부채 |
| K-IFRS 1038 | 무형자산 |
| K-IFRS 1040 | 투자부동산 |
| K-IFRS 1107 | 금융상품 공시 |
| K-IFRS 1109 | 금융상품 |
| K-IFRS 1115 | 고객과의 계약에서 생기는 수익 |
| K-IFRS 1116 | 리스 |

## Accounting Standards Service

기준서 원문 검색 및 조회 (public-data-worker 연동):

```bash
# K-IFRS/K-GAAP 기준서 검색
integration.sh "accounting/tool" '{"action": "accounting-search", "args": {"query": "수익인식", "limit": "5"}}'

# 특정 기준서 전문 조회
integration.sh "accounting/tool" '{"action": "accounting-get-standard", "args": {"id": "1115"}}'

# 특정 문단 조회
integration.sh "accounting/tool" '{"action": "accounting-search-article", "args": {"standard": "1115", "paragraph": "35"}}'

# K-IFRS vs K-GAAP 비교
integration.sh "accounting/tool" '{"action": "accounting-compare-standards", "args": {"standard": "1115"}}'

# 필수 공시 요건 조회
integration.sh "accounting/tool" '{"action": "accounting-get-disclosure", "args": {"standard": "1115"}}'

# XBRL 계정과목 검색
integration.sh "accounting/tool" '{"action": "accounting-search-xbrl", "args": {"query": "매출채권"}}'

# 시산표 계정 → XBRL 매핑
integration.sh "accounting/tool" '{"action": "accounting-account-mapping", "args": {"accounts": ["외상매출금"]}}'

# 회계 기준 ↔ 관련 법령 크로스 검색
integration.sh "accounting/tool" '{"action": "accounting-related-laws", "args": {"query": "리스 세무조정"}}'
```

**사용 시점:**
- 기준서 해석 질문 → `accounting-search` + `accounting-get-standard`
- 재무제표 작성 중 분류 확인 → `accounting-search-xbrl` + `accounting-account-mapping`
- 공시 작성 → `accounting-get-disclosure`
- K-IFRS vs K-GAAP 차이 → `accounting-compare-standards`
- 세무 연계 → `accounting-related-laws`

## Caveats

- **Professional review required.** 모든 출력물은 공인회계사의 검토 필요.
- **Do not fabricate numbers.** 사용자 제공 데이터만 사용.
- **When in doubt, ask.** 회계 처리 모호 시 선택지 제시.
