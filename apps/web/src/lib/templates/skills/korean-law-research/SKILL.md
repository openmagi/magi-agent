---
name: korean-law-research
description: Use when looking up Korean laws, statutes, enforcement decrees, court precedents, legal interpretations, admin rules, ordinances, or legal terminology. Also use for article amendment history, old/new comparison, 3-tier delegation, and comprehensive legal research chains.
user_invocable: true
---

# Korean Law Research (한국 법령 조회)

법제처 국가법령정보센터 Open API 기반의 종합 법령 서비스. 64개 도구로 법령, 판례, 해석례, 행정규칙, 자치법규, 법률용어 등을 검색·조회·분석한다.

## 유저에게 안내할 수 있는 기능

### 가능한 것
- **법령 조문 조회**: "소득세법 제89조 보여줘", "민사집행법 제188조"
- **법령 검색**: 전체 현행 법령 검색 (1,600+ 법령, 약칭 자동 인식)
- **판례 검색**: 대법원·헌재·조세심판·행정심판 등 전체 판례 DB
- **법령해석례**: 법제처 법령해석 사례 (행정해석, 유권해석)
- **행정규칙**: 훈령/예규/고시/공고 검색·조회
- **자치법규**: 조례/규칙 검색·조회
- **조문 연혁**: 특정 조문의 개정 이력 (개정일, 변경 전후 비교)
- **신구대조**: 법령 개정 전후 비교표
- **3단 대조표**: 법률→시행령→시행규칙 위임 구조
- **별표/서식**: 법령 부속 별표 목록 조회
- **법률용어 사전**: 법령용어 정의 검색
- **종합 리서치**: 법령+판례+해석례 한번에 검색
- **약칭 자동 인식**: "화관법" → 화학물질관리법, "민집법" → 민사집행법

### 불가능한 것
- 법률 자문 또는 법적 판단 (참고용이며 전문가 상담 권장)
- 별표/서식 본문 추출 (목록 + 다운로드 링크만 제공)
- 최신 당일 개정 반영 (법제처 API 반영 시점에 따름)

## API Endpoints

All endpoints are accessed via `integration.sh` (chat-proxy routing to public-data-worker).

### 기본 3종

**1. 법령 검색**
```bash
integration.sh "law/search" '{"query": "개인정보보호법", "limit": 10}'
```

**2. 조문 조회**
```bash
integration.sh "law/article" '{"name": "소득세법", "article": "89"}'
```

**3. 판례 검색**
```bash
integration.sh "law/precedent" '{"query": "양도소득세 1세대1주택 비과세", "limit": 5}'
```

### 확장 도구 (64종) — Generic endpoint

```bash
integration.sh "law/tool" '{"action": "<tool-name>", "args": {...}}'
```

#### 검색 (11종)
| action | 설명 | 주요 args |
|--------|------|----------|
| `search` | 법령 검색 (API 직접) | `query, limit, page` |
| `search-admin-rule` | 행정규칙 | `query, limit, kind` |
| `search-ordinance` | 자치법규 | `query, limit` |
| `search-precedent` | 판례 | `query, limit` |
| `search-interpretation` | 법령해석례 | `query, limit` |
| `search-all` | 통합 검색 | `query, limit` |
| `suggest-names` | 법령명 자동완성 | `query` |
| `advanced-search` | 고급 검색 | `query, target, fromDate, toDate` |
| `history` | 법령 변경이력 | `date(YYYYMMDD), ministry` |
| `annexes` | 별표/서식 목록 | `lawName, kind` |
| `parse-jo` | 조문번호 변환 | `text` or `code` |

#### 조회 (9종)
| action | 설명 | 주요 args |
|--------|------|----------|
| `get-text` | 법령 전문 | `mst, lawId, jo` |
| `get-admin-rule` | 행정규칙 전문 | `id` |
| `get-ordinance` | 자치법규 전문 | `ordinSeq` |
| `get-precedent` | 판례 전문 | `id` |
| `get-interpretation` | 해석례 전문 | `id` |
| `get-batch-articles` | 여러 조문 일괄 | `mst, articles[]` |
| `get-article-precedents` | 조문+관련 판례 | `mst, jo` |
| `compare-old-new` | 신구대조 | `mst, lawId` |
| `get-three-tier` | 3단비교 | `mst, lawId` |

#### 분석 (9종)
| action | 설명 | 주요 args |
|--------|------|----------|
| `compare-articles` | 두 조문 비교 | `law1:{mst,jo}, law2:{mst,jo}` |
| `get-tree` | 법령체계도 | `mst, lawId` |
| `article-history` | 조문 개정 연혁 | `lawId, jo, fromDate, toDate` |
| `summarize-precedent` | 판례 요약 | `id` |
| `precedent-keywords` | 판례 키워드 | `id` |
| `similar-precedents` | 유사 판례 | `id, limit` |
| `statistics` | 법령 통계 | `mst, lawId` |
| `parse-links` | 조문 내 참조 파싱 | `text` |
| `external-links` | 외부 링크 | `mst, lawId` |

#### 특수법원 (10종)
| action | 설명 |
|--------|------|
| `search-tax-tribunal` | 조세심판원 재결례 |
| `get-tax-tribunal` | 조세심판원 전문 |
| `search-customs` | 관세청 법령해석 |
| `get-customs` | 관세청 해석 전문 |
| `search-constitutional` | 헌재 결정례 |
| `get-constitutional` | 헌재 결정례 전문 |
| `search-admin-appeal` | 행정심판례 |
| `get-admin-appeal` | 행정심판례 전문 |
| `search-ftc` / `search-nlrc` / `search-pipc` | 공정위/노동위/개보위 |

#### 지식베이스 (7종)
| action | 설명 |
|--------|------|
| `term-search` | 법령용어 검색 |
| `term-detail` | 용어 상세 정의 |
| `daily-term` | 일상용어 검색 |
| `daily-to-legal` | 일상→법령 |
| `legal-to-daily` | 법령→일상 |
| `term-articles` | 용어→조문 |
| `related-laws` | 관련법령 |

#### 체인 (7종)
| action | 워크플로우 |
|--------|-----------|
| `chain-system` | 검색→3단비교→관련법령 |
| `chain-action-basis` | 법체계→해석례→판례 |
| `chain-dispute-prep` | 판례+행심+헌재 병렬 |
| `chain-amendment-track` | 신구대조+조문이력 |
| `chain-ordinance-compare` | 상위법→전국 조례 |
| `chain-full-research` | 법령+판례+해석례 종합 |
| `chain-procedure` | 법체계→별표→시행규칙 |

#### 기타 (5종)
| action | 설명 |
|--------|------|
| `ai-search` | 자연어 통합검색 |
| `english-search` | 영문법령 검색 |
| `english-text` | 영문법령 조회 |
| `historical-search` | 연혁법령 검색 |
| `historical-text` | 연혁법령 조회 |

## Workflow

1. **유저 질문 분석** → 어떤 도구가 적합한지 판단
2. **검색** → `search`, `search-precedent`, `search-interpretation` 등
3. **상세 조회** → `get-text`, `get-precedent`, `get-interpretation`
4. **분석** → `article-history`, `compare-old-new`, `get-three-tier`
5. **종합 리서치** → `chain-full-research`

### 변시 준비 추천 워크플로우

| 공부 목적 | 추천 action |
|-----------|------------|
| 조문 원문 확인 | `get-text` |
| 조문 개정 이력 | `article-history` |
| 신구 조문 비교 | `compare-old-new` |
| 판례 분석 | `get-precedent` → `similar-precedents` |
| 학설 대립 시 실무 해석 | `search-interpretation` |
| 위임 구조 파악 | `get-three-tier` |
| 종합 리서치 | `chain-full-research` |

## 응답 작성 가이드

- 조문 인용: **"소득세법 제89조에 따르면..."** 출처 명시
- 판례 인용: **사건번호와 판시사항** 포함
- 해석례 인용: **회신기관, 회신일자** 포함
- 항상 마지막에 **"이 내용은 법률 참고용이며, 구체적인 사안은 전문가 상담을 권장합니다"**
- 조문 원문이 길면 핵심 발췌 + 전문 요약

## Red Flags

- 법률 참고용이며 법률 자문이 아님
- 법제처 API 응답 시간에 따라 지연 가능 (보통 1-3초)
- 별표/서식 본문 추출은 미지원 (목록 + 링크만)

## Korean Law Reference Guide (한국법 조사 참조 가이드)

### 법원(法源) 체계 -- Legal Source Hierarchy

| 단계 | 유형 | 제정 주체 | 성격 |
|------|------|----------|------|
| 1 | 헌법 | 국민투표 | 최상위 규범 |
| 2 | 법률 (Act/Statute) | 국회 | 일반적/추상적 규범 |
| 3 | 대통령령 (시행령) | 대통령 | 법률의 위임 사항 구체화 |
| 4 | 총리령/부령 (시행규칙) | 국무총리/소관 장관 | 시행령의 세부 절차/양식 |
| 5 | 행정규칙 | 행정기관 | 내부 지침 (고시, 훈령, 예규) |

**Research checkpoints:**
- 위임 여부 확인: "대통령령으로 정한다" -> 시행령 반드시 확인
- 하위법령 미제정 -> `[Unverified -- 하위법령 미제정]`
- 위임고시 vs 일반고시 구별 (대외적 구속력)
- 자기완결 조항 ("~한다") vs 위임 조항 ("~대통령령으로 정한다")

### 부칙(附則) 체크리스트

| 항목 | 확인 사항 |
|------|----------|
| 시행일 | 부칙 제1조 -- "이 법은 공포 후 X개월이 경과한 날부터 시행한다" |
| 경과규정 | "이 법 시행 전에 ~한 경우에는 종전의 규정에 따른다" |
| 적용례 | "이 법 시행 후 최초로 ~하는 경우부터 적용한다" |
| 한시법 | 부칙에 유효기간 명시 |
| 다른 법률의 개정 | 부칙으로 타 법률 조항 함께 개정 |

Rule: 법률 본문만 읽지 말 것 -- 반드시 부칙까지 확인.

### 판례 검색 가이드

| 법원 | 역할 | 검색처 |
|------|------|--------|
| 대법원 | 최종심, 법률해석 통일 | supremecourt.go.kr |
| 헌법재판소 | 위헌심판, 헌법소원 | ccourt.go.kr |
| 고등법원 | 항소심 | 종합법률정보 |
| 특허법원 | 특허/상표 항소심 | 종합법률정보 |
| 행정법원 | 행정소송 1심 | 종합법률정보 |

**판례 번호 형식:**
- 대법원: "2024다12345" (민사), "2024도6789" (형사)
- 헌재: "2024헌바123", "2024헌마456"

## Legal Opinion Style Guide (법률 의견서 작성 가이드)

### 문서 구조

1. 문서 유형 표시 (MEMORANDUM)
2. 작성 일자 (YYYY. M. D.)
3. 정보 블록 (수신/발신/제목)
4. 배경 사실 관계
5. 질의의 요지
6. 법률 의견의 한계 (Disclaimer)
7. 검토의견 (본론): 관련 법령 -> 관련 판례 -> 적용 및 분석
8. 결론
9. 종결 Disclaimer
10. 서명 블록

### 핵심 원칙

- 질의 중심 구조: 의뢰인 질의가 문서 뼈대 결정
- 법령 -> 판례 -> 적용 순서
- 조문 인용 시 반드시 "제N조 제N항 제N호" 형식
- 판례 인용: "대법원 YYYY. M. D. 선고 사건번호 판결"
- 법률용어는 한자 병기 권장 (처음 등장 시)

### 법률 문체 규칙

- 경어체: "~입니다", "~것으로 판단됩니다"
- 조건절: "~하는 경우", "~에 해당하는 때"
- 결론: "~할 것으로 사료됩니다", "~하여야 할 것입니다"
- 불확실: "~할 가능성이 있습니다", "~여지가 있는 것으로 보입니다"
- 이 기능은 `/general-legal-research` 스킬의 품질 게이트와 함께 사용 권장

## Source Grading Quick Reference (for Korean sources)

- **Grade A (primary):** 법률 제N호, 대통령령, 국가법령정보센터, 법제처, 국회, 대법원, 헌법재판소
- **Grade B (authoritative secondary):** 판례, 유권해석, 법률신문, 대한변호사협회, law firm memos
- **Grade C (academic):** 법학연구, 석사/박사 논문, KCI/RISS
- **Grade D (unreliable):** 위키백과, 나무위키 -- NEVER cite as sole basis
