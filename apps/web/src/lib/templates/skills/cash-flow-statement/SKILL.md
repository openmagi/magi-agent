---
name: cash-flow-statement
description: "Use when the user wants to generate a cash flow statement (현금흐름표) from trial balance or ledger data. Supports both indirect and direct methods per K-IFRS 1007."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: accounting
---

# Cash Flow Statement Generator (현금흐름표)

K-IFRS 1007 기준 현금흐름표 자동 생성. 분개/시산표 데이터 → xlsx 출력.

**ALWAYS use .xlsx for financial data (never CSV).** Korean text breaks in CSV.

## Required Information

Before generating, **confirm all missing items in a single message:**

| Item | Why |
|------|-----|
| **Reporting period** | 회계기간 (시작일~종료일) |
| **Entity name** | 보고서 헤더용 법인명 |
| **Entity type** | 상장 / 비상장 / 중소기업 — 공시 깊이 결정 |
| **Applicable standards** | K-IFRS (한국채택국제회계기준) or K-GAAP (일반기업회계기준) |
| **Reporting basis** | 별도 (Individual) or 연결 (Consolidated) |
| **Cash flow method** | 직접법 (Direct) or 간접법 (Indirect) |
| **Prior period data** | 전기 비교 표시용 |

## Knowledge Base Integration

```bash
# Search for trial balance / general ledger
system.run ["sh", "-c", "kb-search.sh '시산표'"]
system.run ["sh", "-c", "kb-search.sh '일반원장'"]
system.run ["sh", "-c", "kb-search.sh '합계잔액시산표'"]

# Search for prior period reports
system.run ["sh", "-c", "kb-search.sh '현금흐름표'"]
system.run ["sh", "-c", "kb-search.sh '재무제표'"]
```

## Indirect Method (간접법) — K-IFRS 1007

```
I. 영업활동으로 인한 현금흐름
   1. 당기순이익(손실)
   2. 조정항목 (비현금 항목 가감)
      - 감가상각비
      - 무형자산상각비
      - 대손상각비
      - 퇴직급여
      - 이자비용
      - 이자수익 (차감)
      - 법인세비용
      - 외화환산손실(이익)
      - 유형자산처분손실(이익)
      - 재고자산평가손실
   3. 운전자본 변동
      - 매출채권의 감소(증가)
      - 재고자산의 감소(증가)
      - 선급금의 감소(증가)
      - 선급비용의 감소(증가)
      - 매입채무의 증가(감소)
      - 미지급금의 증가(감소)
      - 미지급비용의 증가(감소)
      - 선수금의 증가(감소)
      - 예수금의 증가(감소)
   4. 이자의 수취
   5. 이자의 지급
   6. 법인세의 납부

II. 투자활동으로 인한 현금흐름
   1. 투자활동으로 인한 현금유입
      - 단기금융상품의 감소
      - 장기금융상품의 감소
      - 유형자산의 처분
      - 무형자산의 처분
      - 보증금의 감소
   2. 투자활동으로 인한 현금유출
      - 단기금융상품의 증가
      - 장기금융상품의 증가
      - 유형자산의 취득
      - 무형자산의 취득
      - 보증금의 증가

III. 재무활동으로 인한 현금흐름
   1. 재무활동으로 인한 현금유입
      - 단기차입금의 증가
      - 장기차입금의 증가
      - 사채의 발행
      - 유상증자
   2. 재무활동으로 인한 현금유출
      - 단기차입금의 상환
      - 장기차입금의 상환
      - 사채의 상환
      - 배당금의 지급
      - 자기주식의 취득

IV. 현금및현금성자산의 증감 (I + II + III)
V. 기초 현금및현금성자산
VI. 외화표시 현금및현금성자산의 환율변동효과
VII. 기말 현금및현금성자산 (IV + V + VI)
```

## Direct Method (직접법)

```
I. 영업활동으로 인한 현금흐름
   1. 영업에서 창출된 현금
      - 고객으로부터의 현금수취
      - 공급자에 대한 현금지급
      - 종업원에 대한 현금지급
      - 기타영업비용 지급
   2. 이자의 수취
   3. 이자의 지급
   4. 배당금의 수취
   5. 법인세의 납부

(II, III sections same as indirect method)
```

## Account Mapping Guide

When working from a trial balance (합계잔액시산표), map accounts to cash flow categories:

| Trial Balance Account (계정과목) | Cash Flow Category |
|--------------------------------|-------------------|
| 현금및현금성자산 | 기초/기말 잔액 |
| 매출채권, 받을어음 | 운전자본 변동 |
| 재고자산, 선급금 | 운전자본 변동 |
| 유형자산 | 투자활동 |
| 무형자산 | 투자활동 |
| 단기/장기 금융상품 | 투자활동 |
| 매입채무, 미지급금 | 운전자본 변동 |
| 단기/장기 차입금 | 재무활동 |
| 사채 | 재무활동 |
| 자본금, 주식발행초과금 | 재무활동 |
| 이익잉여금 배당 | 재무활동 |
| 감가상각비, 대손상각비 | 조정항목 (비현금) |
| 법인세비용 | 조정항목 → 법인세 납부 |
| 이자비용/이자수익 | 조정항목 → 이자 지급/수취 |

## Excel Generation

```javascript
const ExcelJS = require('exceljs');
const wb = new ExcelJS.Workbook();
wb.creator = 'Open Magi Bot';
wb.created = new Date();

const ws = wb.addWorksheet('현금흐름표');

// Title
ws.mergeCells('A1:D1');
ws.getCell('A1').value = '현 금 흐 름 표';
ws.getCell('A1').font = { size: 16, bold: true };
ws.getCell('A1').alignment = { horizontal: 'center' };

// Period
ws.mergeCells('A2:D2');
ws.getCell('A2').value = '제 XX 기  YYYY년 MM월 DD일 부터  YYYY년 MM월 DD일 까지';
ws.getCell('A2').alignment = { horizontal: 'center' };

ws.mergeCells('A3:D3');
ws.getCell('A3').value = '회사명: (주)XXXXXX';
ws.getCell('A3').alignment = { horizontal: 'center' };

// Column headers
ws.getRow(5).values = ['과 목', '', '당기', '전기'];
ws.getRow(5).font = { bold: true };
ws.getRow(5).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFE2EFDA' } };

// Column widths
ws.getColumn(1).width = 8;   // level indicator
ws.getColumn(2).width = 40;  // account name
ws.getColumn(3).width = 20;  // current period
ws.getColumn(4).width = 20;  // prior period

// Number format for amounts
ws.getColumn(3).numFmt = '#,##0';
ws.getColumn(4).numFmt = '#,##0';

// Helper: add a line item
function addLine(row, level, label, currentAmt, priorAmt) {
  ws.getCell(`A${row}`).value = level === 0 ? '' : level === 1 ? 'I' : '  ';
  ws.getCell(`B${row}`).value = label;
  if (level === 0) {
    ws.getCell(`B${row}`).font = { bold: true, size: 12 };
  }
  if (currentAmt !== null) ws.getCell(`C${row}`).value = currentAmt;
  if (priorAmt !== null) ws.getCell(`D${row}`).value = priorAmt;
}

// ... populate rows with actual ledger data ...

// Borders for all data cells
const lastRow = ws.rowCount;
for (let r = 5; r <= lastRow; r++) {
  for (let c = 1; c <= 4; c++) {
    ws.getCell(r, c).border = {
      bottom: { style: 'thin', color: { argb: 'FFD9D9D9' } },
    };
  }
}

await wb.xlsx.writeFile('/tmp/cash-flow-statement.xlsx');
console.log('Created /tmp/cash-flow-statement.xlsx');
```

Send the file:
Use `FileDeliver(target="chat")` with the generated artifact id.

## Cross-Verification

After generating, always verify:
- 기말 현금 = 재무상태표의 현금및현금성자산
- 영업 + 투자 + 재무 = 현금 순증감
- 비현금 거래는 주석 공시 (K-IFRS 1007.43)

## Workflow

1. KB에서 원장 데이터 검색: `kb-search.sh '시산표'`
2. 누락 정보 확인 (기간, 법인, 방법, 전기 데이터)
3. 시산표 파싱 → 계정과목 매핑
4. K-IFRS 형식 `.xlsx` 생성
5. `FileDeliver(target="chat")`로 전송

## Standards Reference

```bash
# K-IFRS 1007 원문 조회
integration.sh "accounting/tool" '{"action": "accounting-get-standard", "args": {"id": "1007"}}'
# 현금흐름표 공시 요건
integration.sh "accounting/tool" '{"action": "accounting-get-disclosure", "args": {"standard": "1007"}}'
```

## Caveats

- **DRAFT only.** 공인회계사의 전문적 검토 필요.
- **Do not fabricate numbers.** 사용자 제공 데이터 또는 KB 검색 결과만 사용.
- **When in doubt, ask.** 회계 처리가 모호한 경우 선택지를 제시하고 사용자 결정.
- **Currency:** KRW (원) 기본, `#,##0` 형식 (소수점 없음).
- **K-GAAP 차이:** 일반기업회계기준 사용 시 용어 및 양식 조정.
