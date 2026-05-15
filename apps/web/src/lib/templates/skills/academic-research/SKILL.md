---
name: academic-research
description: Use when searching for academic papers, citations, research topics, or scholarly information. Combines arXiv (free), Semantic Scholar, and Google Scholar (via Serper) for comprehensive academic search.
---

# Academic Research

학술 논문 검색, 인용 분석, 리서치 서베이를 위한 통합 스킬.
3가지 소스를 조합해서 포괄적인 학술 검색을 수행한다.

## Available Sources

| Source | API Key Required | Coverage | Best For |
|--------|-----------------|----------|----------|
| arXiv | No (free) | CS, Physics, Math, Bio preprints | 최신 프리프린트, 전문 검색 |
| Semantic Scholar | Optional (`SEMANTIC_SCHOLAR_API_KEY`) | 200M+ papers | 인용 분석, 관련 논문 탐색 |
| Serper Scholar | Required (`SERPER_API_KEY`) | Google Scholar index | 폭넓은 학술 검색, 인용수 기준 정렬 |

## 1. arXiv API (Free, No Key)

arXiv는 무료 오픈 API — 키 없이 바로 사용 가능.

### Search

```bash
# 키워드 검색 (최대 10개 결과)
curl -s "http://export.arxiv.org/api/query?search_query=all:transformer+attention&start=0&max_results=10"

# 제목 검색
curl -s "http://export.arxiv.org/api/query?search_query=ti:large+language+model&max_results=5"

# 저자 검색
curl -s "http://export.arxiv.org/api/query?search_query=au:hinton&max_results=5"

# 카테고리 + 키워드 (예: cs.CL 카테고리에서 검색)
curl -s "http://export.arxiv.org/api/query?search_query=cat:cs.CL+AND+all:retrieval+augmented&max_results=10"

# 날짜 범위 (최근 논문)
curl -s "http://export.arxiv.org/api/query?search_query=all:diffusion+model&sortBy=submittedDate&sortOrder=descending&max_results=10"
```

**Response**: Atom XML format. 각 `<entry>`에 `<title>`, `<summary>`, `<author>`, `<link>`, `<published>` 포함.

### 특정 논문 가져오기

```bash
# arXiv ID로 논문 조회
curl -s "http://export.arxiv.org/api/query?id_list=2301.00001"

# PDF 다운로드 URL: https://arxiv.org/pdf/2301.00001
```

### Search Query Syntax

- `all:` — 전체 필드 검색
- `ti:` — 제목만
- `au:` — 저자명
- `abs:` — 초록
- `cat:` — 카테고리 (cs.CL, cs.CV, cs.AI, physics.*, math.*, q-bio.* 등)
- Boolean: `AND`, `OR`, `ANDNOT`
- Rate limit: 3초 간격 권장

## 2. Semantic Scholar API

`SEMANTIC_SCHOLAR_API_KEY` 환경변수가 있으면 rate limit 향상 (1→100 req/sec).
키가 없어도 기본 검색은 가능 (1 req/sec).

### Paper Search

```bash
# 키워드 검색
curl -s "https://api.semanticscholar.org/graph/v1/paper/search?query=attention+is+all+you+need&limit=10&fields=title,authors,year,citationCount,abstract,url,externalIds" \
  -H "x-api-key: $SEMANTIC_SCHOLAR_API_KEY"

# 연도 필터
curl -s "https://api.semanticscholar.org/graph/v1/paper/search?query=large+language+models&year=2024-2025&limit=10&fields=title,authors,year,citationCount,abstract,url" \
  -H "x-api-key: $SEMANTIC_SCHOLAR_API_KEY"

# 특정 분야 (fieldsOfStudy)
curl -s "https://api.semanticscholar.org/graph/v1/paper/search?query=protein+folding&fieldsOfStudy=Biology,Computer+Science&limit=10&fields=title,authors,year,citationCount,abstract" \
  -H "x-api-key: $SEMANTIC_SCHOLAR_API_KEY"
```

### Paper Details

```bash
# Paper ID, DOI, arXiv ID로 조회
curl -s "https://api.semanticscholar.org/graph/v1/paper/arXiv:1706.03762?fields=title,authors,year,citationCount,abstract,references,citations" \
  -H "x-api-key: $SEMANTIC_SCHOLAR_API_KEY"

# DOI로 조회
curl -s "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1038/s41586-021-03819-2?fields=title,authors,year,citationCount,abstract" \
  -H "x-api-key: $SEMANTIC_SCHOLAR_API_KEY"
```

### Citation / Reference Graph

```bash
# 이 논문을 인용한 논문들
curl -s "https://api.semanticscholar.org/graph/v1/paper/arXiv:1706.03762/citations?fields=title,authors,year,citationCount&limit=20" \
  -H "x-api-key: $SEMANTIC_SCHOLAR_API_KEY"

# 이 논문이 참조한 논문들
curl -s "https://api.semanticscholar.org/graph/v1/paper/arXiv:1706.03762/references?fields=title,authors,year,citationCount&limit=20" \
  -H "x-api-key: $SEMANTIC_SCHOLAR_API_KEY"
```

### Author Search

```bash
curl -s "https://api.semanticscholar.org/graph/v1/author/search?query=Yann+LeCun&fields=name,hIndex,citationCount,paperCount" \
  -H "x-api-key: $SEMANTIC_SCHOLAR_API_KEY"
```

### Available Fields

- Paper: `title`, `authors`, `year`, `citationCount`, `abstract`, `url`, `venue`, `externalIds`, `references`, `citations`, `fieldsOfStudy`, `publicationDate`, `journal`
- Author: `name`, `hIndex`, `citationCount`, `paperCount`, `papers`

## 3. Serper Scholar (Google Scholar via API)

`SERPER_API_KEY` 환경변수 필요. Google Scholar 결과를 API로 가져온다.

### Search

```bash
curl -s -X POST "https://google.serper.dev/scholar" \
  -H "X-API-KEY: $SERPER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "q": "transformer architecture deep learning",
    "num": 10
  }'
```

**Response**:
```json
{
  "organic": [
    {
      "title": "Attention Is All You Need",
      "link": "https://...",
      "snippet": "...",
      "publication_info": "A Vaswani... - Advances in neural..., 2017",
      "cited_by": { "total": 120000, "link": "..." },
      "versions": { "total": 15, "link": "..." }
    }
  ]
}
```

### Advanced Search

```bash
# 연도 범위
curl -s -X POST "https://google.serper.dev/scholar" \
  -H "X-API-KEY: $SERPER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "q": "retrieval augmented generation",
    "yearLow": 2023,
    "yearHigh": 2025,
    "num": 10
  }'

# 특정 저자
curl -s -X POST "https://google.serper.dev/scholar" \
  -H "X-API-KEY: $SERPER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "q": "author:\"Geoffrey Hinton\" deep learning",
    "num": 10
  }'
```

## Workflow

### 논문 검색 (일반)
1. **arXiv** — 최신 프리프린트 빠르게 확인 (무료, 즉시)
2. **Semantic Scholar** — 인용수, 관련 논문, 메타데이터 확인
3. **Serper Scholar** — Google Scholar 커버리지로 보완

### 서베이 / 문헌 조사
1. Serper Scholar로 주제 검색 → 인용수 높은 핵심 논문 파악
2. Semantic Scholar로 핵심 논문의 citation graph 탐색
3. arXiv에서 최신 관련 프리프린트 확인
4. 결과를 표로 정리: 논문명, 저자, 연도, 인용수, 핵심 기여

### 특정 논문 분석
1. DOI 또는 arXiv ID로 Semantic Scholar에서 상세 조회
2. References → 이 논문의 기반이 된 연구
3. Citations → 이 논문의 영향력과 후속 연구

## Best Practices

- arXiv는 **항상 사용 가능** (무료, 키 불필요) — 기본 소스로 활용
- Semantic Scholar 키가 없으면 1 req/sec 제한 — 배치 요청 시 3초 간격 유지
- Serper 키가 없으면 Google Scholar 검색 불가 — 사용자에게 Settings에서 등록 안내
- 검색 결과를 사용자에게 보여줄 때: 논문 제목, 저자, 연도, 인용수, 링크 포함
- 초록(abstract)은 길면 핵심 문장 2-3개로 요약
- 인용수는 Semantic Scholar 기준이 가장 정확

## Red Flags

- arXiv API rate limit: 3초 간격 미준수 시 차단될 수 있음
- Semantic Scholar 키 없이 대량 요청 시 429 에러
- Serper API 크레딧 소진 시 사용 불가 — 사용자에게 안내
- arXiv 결과는 XML → 파싱 필요 (json 아님)
