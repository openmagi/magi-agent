---
name: writing-style
description: "Use when the user asks you to write in a specific style, tone, or voice — or when they provide writing samples (text, images, documents) as style references. Also triggers on: '이 문체로 써줘', '이런 톤으로', 'write like this', 'mimic this style', '글 스타일', '문체 분석', '레퍼런스 등록'."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
---

# Writing Style — 문체 모방 스킬

사람의 문체를 학습하고 모방하여 AI 특유의 어투가 아닌 자연스러운 글쓰기를 수행한다.

## 핵심 원리

1. **샘플 수집** — 유저가 제공한 텍스트/이미지/문서에서 문체 샘플을 추출·저장
2. **문체 분석** — 샘플에서 문장 구조, 톤, 어휘, 호흡 등 문체 특징을 추출
3. **적응형 검색** — 샘플 수에 따라 고정(전수) 또는 RAG(선별) 자동 전환
4. **문체 적용** — 검색된 샘플을 few-shot으로 주입하여 글 생성

## 디렉토리 구조

```
skills/writing-style/
├── SKILL.md          # 이 파일
├── samples/          # 문체 샘플 저장소
│   ├── sample-001.md
│   ├── sample-002.md
│   └── ...
└── style-profile.md  # 문체 분석 프로필 (자동 생성)
```

## 1. 샘플 등록

유저가 레퍼런스를 제공하면 샘플로 저장한다.

### 텍스트 입력

유저가 텍스트를 직접 붙여넣거나 메시지로 보내는 경우:

```bash
# 다음 번호 확인
system.run ["sh", "-c", "ls skills/writing-style/samples/ 2>/dev/null | wc -l | xargs -I{} expr {} + 1"]
```

```bash
# 샘플 저장 (cat heredoc)
system.run ["sh", "-c", "mkdir -p skills/writing-style/samples && cat > skills/writing-style/samples/sample-NNN.md << 'SAMPLE_EOF'\n---\nsource: 유저 직접 입력\nregistered: YYYY-MM-DD\ntags: [장르/주제 태그]\n---\n\n[추출한 텍스트]\nSAMPLE_EOF"]
```

### 이미지 입력

유저가 스크린샷, 사진, 캡처 이미지로 레퍼런스를 보내는 경우:

1. **이미지에서 텍스트를 직접 읽는다** — Claude의 vision 기능으로 이미지 속 텍스트를 정확히 추출
2. 추출한 텍스트를 위와 동일하게 `sample-NNN.md`로 저장
3. 이미지가 손글씨, 캡처, 책 사진 등 어떤 형태든 텍스트만 추출하면 됨

**주의:** OCR 정확도가 낮은 경우(흐릿한 이미지, 손글씨 등) 유저에게 확인 요청.

### 문서 파일 입력

PDF, DOCX 등 문서 파일인 경우:

```bash
# document-reader 스킬로 텍스트 변환 후 저장
system.run ["sh", "-c", "curl -s -X POST http://document-worker.clawy-system.svc:3009/convert -H 'X-Mimetype: application/pdf' -H 'X-Filename: ref.pdf' --data-binary @/workspace/uploads/ref.pdf"]
```

변환된 텍스트에서 문체가 드러나는 부분(서두, 본문 단락)을 선별하여 샘플로 저장. 목차, 참고문헌, 표 등 비문체 부분은 제외.

### 샘플 저장 규칙

- **샘플당 300~2000자** (한글 기준). 너무 짧으면 문체 패턴 부족, 너무 길면 노이즈
- 하나의 소스에서 여러 샘플 추출 가능 (다른 섹션, 다른 글)
- frontmatter에 `source`, `registered`, `tags` 기록
- 파일명: `sample-001.md`, `sample-002.md`, ... (순번)

## 2. 문체 분석 (style-profile.md)

샘플이 3개 이상 모이면 문체 프로필을 자동 생성/갱신한다.

```bash
system.run ["sh", "-c", "cat skills/writing-style/samples/*.md"]
```

전체 샘플을 읽고 다음 항목을 분석하여 `skills/writing-style/style-profile.md`에 저장:

```markdown
# Style Profile

## 문장 구조
- 평균 문장 길이: [짧음/중간/긴 편]
- 문장 종결 패턴: [~다, ~했다, ~인 것이다, 구어체 등]
- 접속사 사용: [빈번/적음, 주로 사용하는 접속사]

## 톤 & 어조
- 격식: [격식/비격식/혼합]
- 감정 표현: [절제/풍부]
- 유머: [있음/없음/간헐적]
- 독자와의 거리: [가까움/중립/거리감]

## 어휘 특징
- 한자어 비율: [높음/보통/낮음]
- 외래어 사용: [적극/소극]
- 전문용어: [분야별 특수 어휘]
- 특징적 표현: [자주 쓰는 비유, 관용구, 특이 표현]

## 단락 구조
- 단락 길이: [짧은 단문/중간/장문]
- 전개 방식: [두괄식/미괄식/혼합]
- 예시 사용: [빈번/적음]

## 피해야 할 패턴 (AI 특유 어투)
- [이 문체에서 절대 안 쓰는 표현 목록]
- 예: "~할 수 있습니다", "다양한", "효과적인", "중요합니다" 등
```

## 3. 글쓰기 실행

유저가 글쓰기를 요청하면 다음 순서로 진행:

### Step 1: 모드 판단

```bash
system.run ["sh", "-c", "ls skills/writing-style/samples/ 2>/dev/null | wc -l"]
```

| 샘플 수 | 모드 | 동작 |
|---------|------|------|
| 0개 | 없음 | 유저에게 레퍼런스 요청 |
| 1~5개 | **고정 (B)** | 전체 샘플 로드 |
| 6개+ | **RAG (C)** | qmd vector_search로 관련 샘플 선별 |

### Step 2-B: 고정 모드 (샘플 ≤ 5)

```bash
# 모든 샘플 로드
system.run ["sh", "-c", "cat skills/writing-style/samples/*.md"]
```

모든 샘플 + style-profile.md(있으면)를 context에 넣고 글 생성.

### Step 2-C: RAG 모드 (샘플 > 5)

```bash
# qmd로 주제 관련 샘플 검색
system.run ["sh", "-c", "qmd search 'writing-style' --query '[유저 요청 주제/키워드]' --limit 5"]
```

벡터 검색이 가능한 경우:
```bash
system.run ["sh", "-c", "qmd vector-search 'writing-style' --query '[유저 요청 주제/키워드]' --limit 5"]
```

검색된 상위 3~5개 샘플을 로드하고, 반드시 style-profile.md도 함께 로드.

**qmd 인덱싱** — 샘플 추가/변경 시 자동 갱신:
```bash
system.run ["sh", "-c", "qmd index skills/writing-style/samples/ --collection writing-style"]
```

### Step 3: 글 생성

검색된 샘플 + style-profile을 바탕으로 글을 작성한다.

**시스템 지시:**

```
아래 레퍼런스 글들의 문체를 정밀하게 모방하여 작성하라.

[문체 프로필 요약]

모방해야 할 요소:
- 문장 길이와 호흡
- 종결어미 패턴
- 접속사와 전환 표현
- 비유와 예시 사용법
- 단락 구조와 전개 방식
- 어휘 선택 (한자어/외래어 비율)

절대 금지:
- AI 특유 패턴: "다양한", "효과적인", "~할 수 있습니다", "중요합니다", "살펴보겠습니다"
- 불필요한 나열 (1. 2. 3. 식 목록)
- 과도한 헤더/볼드 사용
- 매 문단 시작을 같은 패턴으로
- "결론적으로", "요약하면", "마지막으로" 같은 기계적 전환

[레퍼런스 샘플 1]
---
[레퍼런스 샘플 2]
---
[레퍼런스 샘플 3]
```

## 4. 유저 인터랙션

### 레퍼런스 등록 요청

유저: "이 글을 레퍼런스로 등록해줘" / "이 문체 저장해" / "이 이미지 문체 참고"
→ 샘플 추출 → 저장 → 현재 샘플 수 안내 → 3개 이상이면 style-profile 갱신

### 문체 확인

유저: "내 문체 프로필 보여줘" / "어떤 문체로 학습됐어?"
→ style-profile.md 읽어서 보여주기

### 글쓰기 요청

유저: "이 주제로 글 써줘" / "블로그 써줘" / "이 문체로 작성해"
→ Step 1~3 실행

### 문체 비교

유저: "이 글이 내 문체랑 맞아?" / "문체 점검해줘"
→ 제출된 글과 style-profile 비교 → 일치/불일치 항목 피드백

## 5. 주의사항

- **qmd가 없는 환경**: RAG 모드 불가 → 고정 모드로 폴백 (샘플 최대 5개 로드)
- **샘플 언어**: 한국어/영어/일본어 등 혼합 가능. style-profile에 주 언어 표기
- **여러 문체**: 유저가 복수 문체를 원하면 `samples/` 하위에 폴더 분리 (`samples/blog/`, `samples/essay/`) 후 태그로 구분
- **문체 충돌**: 상반된 문체 샘플이 섞이면 품질 저하 → 유저에게 일관성 확인 요청
- **저작권**: 샘플은 봇 내부 문체 학습용으로만 사용. 원문 그대로 출력 금지
