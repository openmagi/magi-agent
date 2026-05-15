---
name: audit-report-draft
description: "Use when the user wants to generate an audit report draft (감사보고서 초안) with K-IFRS mandatory disclosure notes. Outputs .docx format."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: accounting
---

# Audit Report Draft Generator (감사보고서 초안)

K-IFRS 기준 감사보고서 초안 자동 생성. 필수 주석 25개 자동 반영, .docx 출력.

## Required Information

Before generating, **confirm all missing items in a single message:**

| Item | Why |
|------|-----|
| **Reporting period** | 회계기간 (시작일~종료일) |
| **Entity name** | 법인명 (보고서 헤더 + 감사의견) |
| **Entity type** | 상장 / 비상장 / 중소기업 — KAM, 영업부문 공시 여부 결정 |
| **Applicable standards** | K-IFRS or K-GAAP |
| **Reporting basis** | 별도 or 연결 |
| **Audit opinion type** | 적정 (Unqualified) / 한정 (Qualified) / 부적정 (Adverse) / 의견거절 (Disclaimer) |
| **Auditor name** | 감사인 (회계법인명) |
| **KAM topics** | 핵심감사사항 주제 (상장사 필수) |
| **Prior period data** | 전기 비교 표시용 |

## Knowledge Base Integration

```bash
# Search for prior audit reports
system.run ["sh", "-c", "kb-search.sh '감사보고서'"]
system.run ["sh", "-c", "kb-search.sh '재무제표'"]
system.run ["sh", "-c", "kb-search.sh '주석'"]

# Search for financial data
system.run ["sh", "-c", "kb-search.sh '시산표'"]
system.run ["sh", "-c", "kb-search.sh '합계잔액시산표'"]
```

## Audit Report Structure (감사보고서 구조)

```
1. 독립된 감사인의 감사보고서
   - 수신: (이사회/주주총회)
   - 감사의견
   - 감사의견의 근거
   - 핵심감사사항 (KAM) — 상장사 필수
   - 경영진과 지배기구의 책임
   - 감사인의 책임
   - 감사인 정보 (서명란)

2. 재무제표
   a. 재무상태표 (Statement of Financial Position)
   b. 포괄손익계산서 (Statement of Comprehensive Income)
   c. 자본변동표 (Statement of Changes in Equity)
   d. 현금흐름표 (Statement of Cash Flows)
   e. 주석 (Notes to Financial Statements)
```

## K-IFRS Mandatory Disclosure Notes (필수 주석 25개)

| # | Note Title | Standard Reference |
|---|-----------|-------------------|
| 1 | 일반사항 (회사의 개요) | K-IFRS 1001.138 |
| 2 | 재무제표 작성기준 | K-IFRS 1001.16-17 |
| 3 | 유의적인 회계정책 | K-IFRS 1001.117-124 |
| 4 | 중요한 회계추정 및 판단 | K-IFRS 1001.125-133 |
| 5 | 현금및현금성자산 | K-IFRS 1007.46 |
| 6 | 매출채권 및 기타채권 | K-IFRS 1109, 1115 |
| 7 | 재고자산 | K-IFRS 1002.36 |
| 8 | 유형자산 | K-IFRS 1016.73-79 |
| 9 | 무형자산 | K-IFRS 1038.118-128 |
| 10 | 투자부동산 | K-IFRS 1040.75-79 |
| 11 | 리스 (사용권자산, 리스부채) | K-IFRS 1116.47-60 |
| 12 | 금융상품 (분류, 공정가치) | K-IFRS 1107 |
| 13 | 차입금 | K-IFRS 1107.7 |
| 14 | 충당부채 및 우발부채 | K-IFRS 1037.84-92 |
| 15 | 종업원급여 (퇴직급여) | K-IFRS 1019.135-152 |
| 16 | 자본 | K-IFRS 1001.79 |
| 17 | 수익 (고객과의 계약) | K-IFRS 1115.113-129 |
| 18 | 법인세 | K-IFRS 1012.79-88 |
| 19 | 주당이익 | K-IFRS 1033.70-73 |
| 20 | 특수관계자 공시 | K-IFRS 1024.13-24 |
| 21 | 금융위험관리 | K-IFRS 1107.31-42 |
| 22 | 보고기간후 사건 | K-IFRS 1010.21 |
| 23 | 현금흐름표 관련 주석 | K-IFRS 1007.44-47 |
| 24 | 영업부문 (상장사만) | K-IFRS 1108.20-24 |
| 25 | 관계기업/공동기업 투자 | K-IFRS 1028.38 |

### Entity Type별 주석 적용

| Note | 상장 | 비상장 | 중소기업 |
|------|------|--------|---------|
| #19 주당이익 | Required | Optional | N/A |
| #24 영업부문 | Required | N/A | N/A |
| KAM (핵심감사사항) | Required | Optional | N/A |
| #21 금융위험관리 | Full | Simplified | Simplified |

## Audit Opinion Templates

### 적정의견 (Unqualified)
```
우리의 의견으로는, 별첨된 재무제표는 (주)회사명의 YYYY년 MM월 DD일 현재의
재무상태와 동일로 종료되는 보고기간의 재무성과 및 현금흐름을 한국채택국제회계기준에
따라 중요성의 관점에서 공정하게 표시하고 있습니다.
```

### 한정의견 (Qualified)
```
위 문단에서 설명하고 있는 사항이 미치는 영향을 제외하고는, 별첨된 재무제표는
(주)회사명의 ... 공정하게 표시하고 있습니다.
```

### 부적정의견 (Adverse)
```
위 문단에서 설명하고 있는 사항이 재무제표에 미치는 영향의 중요성과 전반적 영향에
비추어, 별첨된 재무제표는 ... 공정하게 표시하고 있지 아니합니다.
```

### 의견거절 (Disclaimer)
```
위 문단에서 설명하고 있는 사항의 중요성에 비추어, 우리는 (주)회사명의 재무제표에
대하여 감사의견을 표명하지 아니합니다.
```

## DOCX Generation

```javascript
const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
        Table, TableRow, TableCell, WidthType } = require('docx');
const fs = require('fs');
const KOREAN_FONT = 'Noto Sans CJK KR';

const doc = new Document({
  styles: {
    paragraphStyles: [{
      id: 'bodyKorean',
      name: 'Body Korean',
      run: { size: 22, font: KOREAN_FONT },
      paragraph: { spacing: { after: 120, line: 276 } },
    }],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 }, // A4 (twips)
        margin: { top: 1440, bottom: 1440, left: 1800, right: 1440 },
      },
    },
    children: [
      // Cover page
      new Paragraph({ text: '' }),
      new Paragraph({
        children: [new TextRun({ text: '감 사 보 고 서', bold: true, size: 44, font: KOREAN_FONT })],
        alignment: AlignmentType.CENTER,
        spacing: { before: 3000 },
      }),
      new Paragraph({
        children: [new TextRun({ text: '제 XX 기', size: 28, font: KOREAN_FONT })],
        alignment: AlignmentType.CENTER,
        spacing: { before: 600 },
      }),
      new Paragraph({
        children: [new TextRun({ text: '(주)회사명', size: 28, font: KOREAN_FONT })],
        alignment: AlignmentType.CENTER,
        spacing: { before: 200 },
      }),
      new Paragraph({
        children: [new TextRun({ text: '감사인: XXXX 회계법인', size: 24, font: KOREAN_FONT, color: '666666' })],
        alignment: AlignmentType.CENTER,
        spacing: { before: 400 },
      }),

      // Audit opinion section
      new Paragraph({
        text: '독립된 감사인의 감사보고서',
        heading: HeadingLevel.HEADING_1,
        spacing: { before: 600 },
      }),
      new Paragraph({
        text: '(주)회사명 주주 및 이사회 귀중',
        style: 'bodyKorean',
        spacing: { before: 200 },
      }),

      // Opinion paragraph
      new Paragraph({ text: '감사의견', heading: HeadingLevel.HEADING_2 }),
      new Paragraph({
        text: '우리는 (주)회사명의 재무제표를 감사하였습니다. ...',
        style: 'bodyKorean',
      }),

      // Basis for opinion
      new Paragraph({ text: '감사의견의 근거', heading: HeadingLevel.HEADING_2 }),
      new Paragraph({
        text: '우리는 대한민국의 회계감사기준에 따라 감사를 수행하였습니다. ...',
        style: 'bodyKorean',
      }),

      // KAM (listed companies only)
      // new Paragraph({ text: '핵심감사사항', heading: HeadingLevel.HEADING_2 }),

      // Management responsibility
      new Paragraph({ text: '재무제표에 대한 경영진과 지배기구의 책임', heading: HeadingLevel.HEADING_2 }),
      new Paragraph({
        text: '경영진은 한국채택국제회계기준에 따라 이 재무제표를 작성하고 공정하게 표시할 책임이 있으며, ...',
        style: 'bodyKorean',
      }),

      // Auditor responsibility
      new Paragraph({ text: '재무제표감사에 대한 감사인의 책임', heading: HeadingLevel.HEADING_2 }),
      new Paragraph({
        text: '우리의 목적은 재무제표에 전체적으로 부정이나 오류로 인한 중요한 왜곡표시가 없는지에 대하여 합리적인 확신을 얻어 ...',
        style: 'bodyKorean',
      }),

      // Notes section
      new Paragraph({ text: '주석', heading: HeadingLevel.HEADING_1, spacing: { before: 600 } }),

      // ... Add all applicable mandatory notes (1-25) ...
    ],
  }],
});

const buffer = await Packer.toBuffer(doc);
fs.writeFileSync('/tmp/audit-report-draft.docx', buffer);
console.log('Created /tmp/audit-report-draft.docx');
```

Send the file:
Use `FileDeliver(target="chat")` with the generated artifact id.

## Workflow

1. KB에서 기존 감사보고서 및 재무 데이터 검색
2. 누락 정보 확인 (법인, 감사의견 유형, KAM 주제 등)
3. 감사의견문 작성 (의견 유형에 따른 템플릿 적용)
4. 필수 주석 25개 중 해당 항목 자동 구성 (entity type별 필터링)
5. 전문적 판단 필요 항목 플래그 (추정, 공정가치, 손상)
6. `.docx` 생성 후 `FileDeliver(target="chat")`로 전송

## Professional Judgment Flags

생성 시 다음 항목은 반드시 `[검토 필요]` 태그를 붙여 회계사에게 확인 요청:

- 공정가치 측정 (Level 2, 3)
- 손상검사 가정 (할인율, 성장률)
- 충당부채 추정 (소송, 보증, 복구)
- 수익인식 시점 판단
- 리스 분류 (금융리스 vs 운용리스)
- 이연법인세 실현가능성

## Standards Reference

```bash
# 필수 주석 공시 요건 조회
integration.sh "accounting/tool" '{"action": "accounting-get-disclosure", "args": {"standard": "1001"}}'
# 특정 기준서 문단 참조
integration.sh "accounting/tool" '{"action": "accounting-search-article", "args": {"standard": "1001", "paragraph": "138"}}'
```

## Caveats

- **DRAFT only.** 공인회계사의 전문적 검토 필수.
- **Do not fabricate numbers.** 사용자 제공 데이터만 사용.
- **When in doubt, ask.** 회계 처리 모호 시 선택지 제시.
- **K-GAAP 차이:** 일반기업회계기준 사용 시 주석 요구사항 및 용어 조정.
- **KAM:** 비상장 법인은 KAM 선택사항. 상장사는 필수 포함.
