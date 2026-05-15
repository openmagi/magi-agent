---
title: "AI 에이전트를 위한 Context Engineering: 왜 에이전트가 시간이 지날수록 멍청해지는가 (그리고 해결법)"
description: "AI 에이전트는 단순히 잊는 게 아니다. 자기 자신의 context에 빠져 익사한다. Compaction의 함정부터 RAG의 한계까지, context engineering이 왜 에이전트 인프라에서 가장 어려운 미해결 문제인지 분석하고, 이를 해결하기 위해 만든 오픈소스 멀티 레이어 메모리 시스템 Hipocampus를 소개한다."
date: "2026-03-17"
tags: ["Context Engineering", "AI Agent", "LLM", "Memory", "Hipocampus", "Open Source"]
locale: "ko"
author: "openmagi.ai"
---

AI 에이전트 업계에서 아무도 말하지 않는 불편한 진실이 있다.

에이전트는 단순히 정보를 잊는 게 아니다. 쓰면 쓸수록 **적극적으로 더 멍청해진다.** 모델이 퇴화해서가 아니라, context가 오염되기 때문이다.

장기간 운영 중인 AI 에이전트가 점점 느려지고, 비용은 올라가고, 정확도는 떨어지는 걸 경험해 본 적 있다면 이미 이 문제를 직접 겪은 것이다. 원인은 모델이 아니다. 모델 *주변*의 모든 것이 원인이다.

이것이 **context engineering** 문제다. 프로덕션 AI 에이전트를 만드는 데 있어서 가장 중요한 미해결 과제라고 해도 과언이 아니다.

---

## Context란 정확히 무엇인가?

AI 에이전트에게 메시지를 보내면, 보낸 메시지만 input으로 들어가는 게 아니다. 실제 input은 이런 형태다:

```
[System Prompt]
You are an AI marketing assistant...

[User Profile]
This user runs a small e-commerce business...

[Active Task State]
Currently working on Q1 ad campaign analysis...

[Conversation History]
User: Can you pull the ROAS data for January?
Agent: Here's what I found...
User: Good. Now compare it with December.

[Tool Call Results]
Google Ads API response: { "roas": 3.2, "spend": 12400, ... }
Analytics data: { "sessions": 45200, "conversion_rate": 0.032, ... }

[Current Message]
User: What should we change for February?
```

이 전체가 매 API call마다 하나의 input으로 묶여서 들어간다. LLM은 처음부터 끝까지 다 읽고 응답을 생성한다. 이전 call의 내용을 "기억"하는 게 아니다. 현재 context window에 있는 것만 참조할 뿐이다.

여기서 두 가지 핵심이 나온다:

1. **Context에 있는 모든 것이 토큰 비용이다.** 시스템 프롬프트, 대화 히스토리, 도구 호출 결과. 매 API call마다 전부 과금된다.
2. **Context에 있는 모든 것이 attention을 경쟁한다.** LLM은 모든 토큰 간의 관계를 동시에 계산한다(Attention mechanism). 관련 없는 정보가 많을수록 attention이 분산된다. 중요한 신호가 노이즈에 묻힌다.

Context가 에이전트의 **비용**과 **품질**을 동시에 결정한다. 넣는 토큰 하나하나가 도움이 되거나 해가 된다.

---

## Context 누적 문제

여기서부터 골치 아파진다.

대부분은 context 누적을 "대화가 길어지는 것" 정도로 생각한다. 그건 문제의 일부에 불과하다.

실제 시나리오를 보자. 에이전트에게 경쟁사 가격 조사를 요청한다.

이 하나의 질문에 답하기 위해 에이전트가 하는 일:
1. 경쟁사 웹사이트 5개 검색
2. 가격 페이지 크롤링 (HTML 전체를 markdown으로 변환)
3. 내부 가격 이력 문서 조회
4. 스프레드시트 데이터 추출
5. 분석 후 보고서 작성

답변을 다 만들고 나면, context에는 이런 것들이 쌓여있다:
- 경쟁사 5곳의 가격 데이터
- 내부 가격 문서
- 스프레드시트 데이터
- 에이전트의 분석과 추론 과정
- 모든 중간 도구 호출 결과

잠재적으로 **50,000+ 토큰** 분량의 리서치 데이터가 세션 context에 앉아있는 것이다.

여기서 이렇게 말한다: "좋아, 고마워. 내일 스탠드업 관련해서 팀한테 이메일 하나 써줘."

완전히 다른 작업이다. 그런데 50,000 토큰의 경쟁사 가격 리서치가 **여전히 context에 있다.** 여전히 과금되고 있다. 여전히 모델의 attention을 잡아먹고 있다.

에이전트는 경쟁사 가격 데이터를 "떠올리면서" 스탠드업 이메일을 쓰고 있다. 이메일 품질이 떨어진다. 비용은 두 배다. 유저도 에이전트도 왜 그런지 모른다.

**이것이 근본 문제다: context는 기본적으로 append-only다.** 모든 도구 호출, 모든 검색 결과, 모든 중간 단계. 전부 남는다. 작업끼리 서로 간섭한다. 비용은 복리로 늘어난다. 품질은 떨어진다.

그리고 여기서부터 더 나빠진다.

---

## 시도 #1: Compaction

가장 직관적인 해결책은 compaction이다. Context가 너무 길어지면 LLM에게 요약시키는 것이다.

대부분의 에이전트 프레임워크가 이걸 지원한다. 대화가 임계값(context window의 80% 정도)에 도달하면 전체 히스토리를 요약으로 압축한다. 깔끔하게 리셋, 작은 context.

우아해 보인다. 실전에서는 치명적인 결함이 두 가지 있다.

### Context Drift

요약의 요약의 요약을 반복하면 정보가 기하급수적으로 유실된다:

- **1차:** "유저는 React 개발자이고 TypeScript로 Next.js 프로젝트를 하고 있다. 서버 컴포넌트 중심."
- **2차:** "유저는 웹 개발을 한다."
- **3차:** "유저는 IT 업계에서 일한다."

2~3번의 compaction만에 핵심 디테일이 증발한다.

### 중요도 구분 실패

Compaction은 모든 정보를 동등하게 취급한다. 하지만 정보의 중요도는 같지 않다:

- "유저가 심한 땅콩 알레르기가 있다". 생명과 직결되는 정보이고, 몇 달 뒤에도 필요하다.
- "유저가 오늘 날씨를 물어봤다". 내일이면 쓸모없다.

Compaction은 이 둘을 구분하지 못한다. 같은 압축 비율을 일괄 적용한다. 생명에 관련된 정보가 잡담과 함께 유실된다.

**Compaction은 우선순위 없는 손실 압축이다.** 시간을 벌어줄 뿐이지 문제를 해결하지 않는다.

---

## 시도 #2: 구조화된 Context 파일

더 나은 접근: 대화 히스토리에 전부 쌓아두는 대신 중요한 정보를 구조화된 파일에 기록한다.

제대로 된 에이전트 세팅에서 쓰는 `.md` 기반 context 패턴이다:

- **`MEMORY.md`**: 유저와 프로젝트에 대한 장기 팩트 (~50줄)
- **`SCRATCHPAD.md`**: 현재 작업 상태와 진행 중인 태스크 (~100줄)
- **`AGENTS.md`**: 행동 규칙과 지시사항 (~500줄)

에이전트는 매 세션 시작 시 이 파일들을 읽는다. 압축되고 열화되는 대화 히스토리에 의존하는 대신, 핵심 정보가 세션을 넘어 살아남는 영구 파일에 들어있다.

이건 큰 진전이다. 하지만 새로운 문제가 생긴다:

**크기 압박.** 이 파일들은 매 API call마다 로딩된다. AGENTS.md가 500줄이면 매 메시지마다 500줄의 토큰이 과금된다. MEMORY.md를 상세 노트로 200줄까지 키운다면? 유저가 "안녕"이라고만 해도 200줄의 비용이 나간다.

**큐레이션 부담.** 누군가(에이전트든 유저든)가 이 파일에 뭘 넣을지 결정해야 한다. 너무 많으면 비용 폭발과 attention 분산. 너무 적으면 핵심 정보 누락.

**평면 구조.** 단일 MEMORY.md 파일에는 위계가 없다. 이 정보가 어제 것인지, 지난달 것인지, 아직 유효한지 알 수 없다. 전부 읽어봐야 안다.

구조화된 파일은 필요하지만 충분하지 않다. "중요한 정보가 어디에 사는가" 문제는 해결하지만, "적시에 적절한 정보를 어떻게 찾는가" 문제는 해결하지 못한다.

---

## 시도 #3: RAG 추가

Retrieval-Augmented Generation(RAG)은 검색 문제를 다룬다. 모든 것을 context에 로딩하는 대신, 지식을 검색 가능한 인덱스에 저장하고 관련 있는 것만 가져온다.

에이전트의 축적된 지식을 파일로 저장한다. 검색 엔진(BM25 키워드 검색, 벡터 임베딩, 또는 둘 다)으로 인덱싱한다. 에이전트가 정보를 필요로 할 때 인덱스를 검색해서 관련 청크만 가져온다.

강력하다. 10,000개의 문서 분량의 지식을 가진 에이전트가 매 쿼리마다 가장 관련 있는 3~5개만 로딩한다. 비용이 일정하게 유지된다. Attention이 집중된다.

하지만 RAG에도 한계가 있다:

**뭘 검색해야 하는지 알아야 한다.** RAG는 명확한 쿼리가 있을 때 작동한다. 하지만 ambient context, 즉 에이전트가 "그냥 알고 있어야 하는" 것들은 어떻게 할까? 유저의 타임존, 커뮤니케이션 선호도, 진행 중인 프로젝트 상태 같은 것들. 이런 건 필요한지 모르니까 미리 검색할 수가 없다. 필요한 순간에는 이미 늦었다.

**인덱싱 지연.** 현재 세션에서 기록한 정보가 바로 검색되지 않는다. 에이전트가 오후 2시에 중요한 걸 알아냈는데, 인덱스는 세션이 끝나야 업데이트된다. 그때는 이미 그 정보가 필요했던 시점이 지났을 수 있다.

**시간 인식 없음.** RAG는 의미적으로 가장 관련 있는 결과를 반환하지만, 최신성이나 시간 감소에 대한 개념이 없다. 3개월 전의 결정과 오늘 아침의 결정이 같은 가중치를 받는다. 실전에서는 최근 context가 거의 항상 더 중요하다.

**Cold start.** 빈 지식 베이스의 새 에이전트는 아무것도 검색할 수 없다. RAG는 충분한 지식이 축적된 이후에야 작동한다. 그런데 그 축적 자체에 바로 RAG가 제공해야 할 context 관리가 필요하다.

---

## 진짜 문제: 아무도 전체 스택을 해결하지 않는다

각 접근법은 한 조각씩만 해결한다:

| 접근법 | 해결하는 것 | 놓치는 것 |
|--------|------------|----------|
| Compaction | Context overflow | 정보 유실, 우선순위 없음 |
| 구조화된 파일 | 영구 메모리 | 스케일링, 큐레이션, 평면 구조 |
| RAG | 검색 기반 조회 | Ambient context, 시간 인식, cold start |

프로덕션 에이전트는 이 모든 것이 함께 작동해야 하고, 거기에 뭔가 더 필요하다. 시스템이 해야 할 일:

1. 원본 정보를 영구 보존 (손실 압축 없음)
2. 여러 시간 단위에서 검색 가능한 인덱스 생성
3. 적시에 적절한 context를 로딩
4. 첫날부터 작동 (cold start 없음)
5. 사람의 큐레이션 없이 자체 유지보수

이것을 만들었다.

---

## Compaction Tree 소개

핵심 인사이트: **원본은 절대 삭제하지 않는다. 위에 검색 인덱스를 쌓는다.**

도서관으로 비유하면 이렇다. 기존 compaction은 책을 불태우고 목차만 남기는 것이다. Compaction tree는 모든 책을 서가에 보관하면서 카드 카탈로그를 추가하는 것이다.

```
memory/
├── ROOT.md                 ← 항상 로딩 (~100줄)
│                              토픽 인덱스: "X에 대해 알고 있는가?"
├── monthly/
│   └── 2026-03.md          ← 월간 키워드 인덱스
│                              "3월에는 이런 토픽이 있었다: ..."
├── weekly/
│   └── 2026-W11.md         ← 주간 요약
│                              주요 결정, 완료된 작업
├── daily/
│   └── 2026-03-15.md       ← 일별 compaction 노드
│                              토픽, 결정, 결과
└── 2026-03-15.md            ← 원본 일일 로그 (영구 보존, 절대 삭제 안 함)
                               그날 일어난 모든 것의 전체 기록
```

**탐색 패턴:**

뭔가를 찾아야 할 때. 맨 위에서 시작한다:

1. **ROOT.md**: Topics Index를 확인한다. "경쟁사 가격"에 대해 알고 있는가? 있다. 3월에 기록됐다.
2. **Monthly**: 3월 인덱스에 따르면 경쟁사 분석은 11주차에 있었다.
3. **Weekly**: 11주차 요약에 가격 조사가 3월 12일에 있었다고 나온다.
4. **Daily**: 3월 12일 노드에 주요 결정과 결과가 있다.
5. **Raw**: 3월 12일 원본 로그에 압축되지 않은 전체 원본이 있다.

시간 기반 메모리에 대한 **O(log n) 검색**이다. 필요 이상으로 읽을 필요가 없지만, 드릴다운하면 전체 디테일을 언제든 볼 수 있다.

### Fixed vs. Tentative 노드

Compaction 노드에는 생명주기가 있다:

- **Tentative**: 해당 기간이 아직 진행 중이다. 새 데이터가 들어오면 노드가 다시 생성된다. 오늘의 일별 노드는 tentative다. 이번 주의 주간 노드도 tentative다.
- **Fixed**: 해당 기간이 끝났다. 노드가 고정되고 다시 업데이트되지 않는다. 지난주의 주간 노드는 fixed다.

이 구조 덕분에 **첫날부터 사용 가능하다.** 주간 요약이 생기려면 1주일을 기다릴 필요가 없다. 즉시 tentative로 생성되고, 새 데이터가 들어오면 업데이트된다.

### Smart Threshold

모든 것에 LLM 요약이 필요한 건 아니다. 일일 로그가 50줄이면 그대로 복사하는 게 비용도 안 들고 정보도 안 잃는다. Content가 임계값을 넘을 때만 LLM 요약을 활용한다:

| 레벨 | 임계값 | 미만 | 이상 |
|------|--------|------|------|
| Raw → Daily | ~200줄 | 그대로 복사 | LLM 키워드 밀도 높은 요약 |
| Daily → Weekly | ~300줄 | 일별 노드 합치기 | LLM 요약 |
| Weekly → Monthly | ~500줄 | 주간 노드 합치기 | LLM 요약 |

임계값 미만: 정보 유실 제로. 이상: 내러티브 가독성이 아닌 검색 재현율에 최적화된 키워드 밀도 높은 압축.

---

## Hipocampus: 전체 시스템

Compaction tree는 데이터 구조다. [**Hipocampus**](https://github.com/kevin-hs-sohn/hipocampus)는 그 위에 구축된 전체 시스템이다. 우리가 개발하고, 프로덕션에서 실전 검증하고, 오픈소스로 공개한 3-tier 에이전트 메모리 프로토콜이다.

### 3개의 레이어

```
Layer 1 — System Prompt (항상 로딩, 매 API call마다)
  ├── ROOT.md          ~100줄   Compaction tree의 토픽 인덱스
  ├── SCRATCHPAD.md    ~150줄   현재 작업 상태
  ├── WORKING.md       ~100줄   진행 중인 태스크
  └── TASK-QUEUE.md    ~50줄    대기 항목

Layer 2 — On-Demand (에이전트가 필요하다고 판단할 때 로딩)
  ├── memory/YYYY-MM-DD.md    원본 일일 로그 (영구 보존)
  ├── knowledge/*.md           상세 지식 파일
  └── plans/*.md               작업 계획

Layer 3 — Search (compaction tree + 키워드/벡터 검색을 통해)
  ├── memory/daily/            일별 compaction 노드
  ├── memory/weekly/           주간 compaction 노드
  └── memory/monthly/          월간 compaction 노드
```

**Layer 1**은 "지금 무슨 작업 중인가?"에 답한다. 항상 context에 있고, 항상 과금되므로 극도로 작게 유지한다.

**Layer 2**는 "상세하게 뭘 알고 있는가?"에 답한다. 접근하기 전까지 비용이 없고, 에이전트가 더 많은 context가 필요하다고 인식할 때 on-demand로 로딩한다.

**Layer 3**는 "이걸 본 적이 있는가?"에 답한다. ROOT.md의 Topics Index가 에이전트에게 한눈에 메모리에 정보가 존재하는지 알려준다. 아무것도 로딩하지 않고도. 있다면 트리 탐색이나 키워드 검색으로 가져온다.

### 세션 프로토콜

Hipocampus는 두 가지 필수 루틴을 정의한다:

**Session Start:** 어떤 것에든 응답하기 전에, 에이전트가 Layer 1 파일을 로딩하고 compaction 체인(Daily → Weekly → Monthly → Root)을 실행한다. 트리가 최신 상태로 유지되고 ROOT.md가 최신 상태를 반영한다.

**End-of-Task Checkpoint:** 작업을 완료할 때마다, 에이전트가 원본 일일 파일에 구조화된 로그를 기록한다:

```markdown
## Competitor Pricing Analysis
- request: Compare our pricing with top 5 competitors
- analysis: Scraped pricing pages, pulled internal data
- decisions: Recommended 15% reduction on starter tier
- outcome: Report delivered, shared with team
- references: knowledge/pricing-strategy.md
```

이것이 source of truth다. 그 외의 모든 것(compaction 노드, ROOT.md, Topics Index)은 이 원본 로그로부터 compaction 체인을 통해 파생된다.

### ROOT.md의 강점

가장 강력한 기능은 ROOT.md의 Topics Index다. "뭘 검색해야 하는가?" 문제를 해결한다:

```markdown
## Topics Index
- pricing: competitor-analysis, Q1-review, starter-tier-reduction
- infrastructure: k8s-migration, redis-upgrade, node-scaling
- marketing: ad-campaign-Q1, landing-page-redesign, SEO-audit
```

유저가 가격에 대해 물어보면, 에이전트가 무작정 검색할 필요가 없다. Topics Index를 확인하고, 가격 정보가 있다는 걸 파악하고, 어떤 시간대를 드릴다운할지 바로 안다. 토픽이 인덱스에 없으면, 빈 메모리를 뒤지느라 시간을 낭비하는 대신 외부 검색으로 전환해야 한다는 걸 안다.

**이것이 "로딩할지 말지를 결정하기 위해 로딩하는" 문제를 제거한다.** RAG 기반 메모리 시스템에서 가장 큰 효율성 낭비 요인이다.

### Proactive Dump

Hipocampus는 작업 완료까지 기다렸다가 context를 저장하지 않는다. Proactive dump를 권장한다. 대화가 20개 이상의 메시지로 이어졌을 때, 중요한 결정이 내려졌을 때, 또는 에이전트가 context가 커지고 있다고 감지할 때.

이건 미묘하지만 치명적인 실패 모드에 대한 방어다: **플랫폼의 context 압축.** 호스팅 플랫폼이 대화 히스토리를 압축할 때(대부분의 플랫폼이 긴 세션에서 이렇게 한다), dump되지 않은 디테일은 영구 유실된다. 빨리, 자주 기록하라. 원본 로그는 append-only이므로 한 세션에서 여러 번 dump해도 문제없다.

---

## 왜 이것이 에이전트 플랫폼에게 중요한가

대부분의 에이전트 플랫폼은 배포에 집중한다. 버튼 하나 누르면 봇이 라이브.

하지만 배포는 전체 문제의 5% 정도다. 나머지 95%는 **운영**이다. 에이전트를 몇 주, 몇 달 동안 지속적으로 사용하면서 유용하고, 정확하고, 비용 효율적으로 유지하는 것.

제대로 된 context engineering 없이는:
- 에이전트 비용이 사용량에 비례해서 선형으로 증가한다
- 관련 없는 정보가 쌓이면서 품질이 떨어진다
- 핵심 지식이 compaction 사이클에서 유실된다
- 에이전트가 어제 알았던 것과 3개월 전에 알았던 것을 구분하지 못한다

[Open Magi](https://openmagi.ai)에서 [Hipocampus](https://github.com/kevin-hs-sohn/hipocampus)를 만든 건 우리 자신이 필요했기 때문이다. 프로덕션에서 수백 개의 에이전트를 운영하면서, 전부 같은 벽에 부딪히는 걸 지켜봤다. 며칠간은 잘 작동하다가, 점점 비싸지고, 느려지고, 건망증이 생긴다.

Hipocampus는 이제 플랫폼의 모든 에이전트에 기본 메모리 시스템으로 탑재되어 있다. Open Magi에서 에이전트를 배포하면, API key 달린 챗봇을 받는 게 아니다. 전체 context engineering 스택을 받는 것이다: 계층적 compaction, 멀티 레이어 메모리, RAG 검색, 그리고 에이전트를 몇 달간 지속 운영해도 날카롭게 유지하는 세션 프로토콜.

에이전트를 배포하는 건 쉽다. *유용하게 유지하는 것*이 어려운 부분이다.

---

*Hipocampus는 오픈소스다. [GitHub 저장소](https://github.com/kevin-hs-sohn/hipocampus)에서 직접 에이전트 세팅에 활용할 수 있다.*

*이것은 프로덕션 AI 에이전트 뒤의 인프라에 대한 시리즈의 첫 번째 글이다. 다음 편: AI Agent OS가 실제로 어떤 모습인지, 그리고 왜 에이전트에게도 앱처럼 운영체제가 필요한지.*
