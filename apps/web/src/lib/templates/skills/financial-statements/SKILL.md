---
name: financial-statements
description: "Use when the user wants to generate financial statements (재무제표) — balance sheet, income statement, and statement of changes in equity. K-IFRS 1001 based, xlsx multi-sheet output."
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: accounting
---

# Financial Statements Generator (재무제표)

K-IFRS 1001 기준 재무상태표 + 포괄손익계산서 + 자본변동표 통합 생성. 시산표/원장 데이터 → xlsx 멀티시트 출력.

**ALWAYS use .xlsx for financial data (never CSV).** Korean text breaks in CSV.

## Required Information

Before generating, **confirm all missing items in a single message:**

| Item | Why |
|------|-----|
| **Reporting period** | 회계기간 (시작일~종료일) |
| **Entity name** | 보고서 헤더용 법인명 |
| **Entity type** | 상장 / 비상장 / 중소기업 — 공시 깊이 결정 |
| **Applicable standards** | K-IFRS or K-GAAP |
| **Reporting basis** | 별도 or 연결 |
| **Prior period data** | 전기 비교 표시용 |
| **기 수** | 제 몇 기 (예: 제 15 기) |

## Knowledge Base Integration

```bash
system.run ["sh", "-c", "kb-search.sh '시산표'"]
system.run ["sh", "-c", "kb-search.sh '합계잔액시산표'"]
system.run ["sh", "-c", "kb-search.sh '일반원장'"]
system.run ["sh", "-c", "kb-search.sh '재무상태표'"]
system.run ["sh", "-c", "kb-search.sh '손익계산서'"]
```

## 1. Statement of Financial Position (재무상태표)

K-IFRS 1001.54 기준 최소 표시 항목:

```
자산
  I. 유동자산
     1. 현금및현금성자산
     2. 단기금융상품
     3. 매출채권 및 기타유동채권
     4. 재고자산
     5. 기타유동자산
     6. 당기법인세자산
     유동자산 합계

  II. 비유동자산
     1. 장기금융상품
     2. 장기매출채권 및 기타비유동채권
     3. 유형자산
     4. 사용권자산
     5. 무형자산
     6. 투자부동산
     7. 관계기업 투자
     8. 이연법인세자산
     9. 기타비유동자산
     비유동자산 합계

  자산 총계

부채
  I. 유동부채
     1. 매입채무 및 기타유동채무
     2. 단기차입금
     3. 유동성장기부채
     4. 리스부채 (유동)
     5. 미지급법인세
     6. 충당부채
     7. 기타유동부채
     유동부채 합계

  II. 비유동부채
     1. 장기차입금
     2. 사채
     3. 리스부채 (비유동)
     4. 순확정급여부채
     5. 이연법인세부채
     6. 기타비유동부채
     비유동부채 합계

  부채 총계

자본
  I. 자본금
  II. 주식발행초과금
  III. 기타자본구성요소
     - 자기주식
     - 기타포괄손익누계액
  IV. 이익잉여금
     - 법정적립금
     - 임의적립금
     - 미처분이익잉여금

  자본 총계

부채와 자본 총계
```

## 2. Statement of Comprehensive Income (포괄손익계산서)

K-IFRS 1001.82 기준:

```
I. 매출액
II. 매출원가
III. 매출총이익 (I - II)
IV. 판매비와관리비
   1. 급여
   2. 퇴직급여
   3. 복리후생비
   4. 감가상각비
   5. 무형자산상각비
   6. 대손상각비
   7. 임차료
   8. 세금과공과
   9. 광고선전비
   10. 경상연구개발비
   11. 기타
V. 영업이익 (III - IV)
VI. 기타수익
   - 유형자산처분이익
   - 외화환산이익
   - 기타
VII. 기타비용
   - 유형자산처분손실
   - 외화환산손실
   - 기타
VIII. 금융수익
   - 이자수익
   - 배당금수익
IX. 금융비용
   - 이자비용
X. 법인세비용차감전순이익 (V + VI - VII + VIII - IX)
XI. 법인세비용
XII. 당기순이익 (X - XI)

XIII. 기타포괄손익
   - 후속적으로 당기손익으로 재분류되지 않는 항목
     - 확정급여제도 재측정요소
     - 기타포괄손익-공정가치 측정 금융자산 평가손익
   - 후속적으로 당기손익으로 재분류될 수 있는 항목
     - 해외사업환산손익
     - 현금흐름위험회피 파생상품 평가손익

XIV. 총포괄이익 (XII + XIII)

XV. 주당이익 (상장사 필수)
   - 기본주당이익
   - 희석주당이익
```

## 3. Statement of Changes in Equity (자본변동표)

K-IFRS 1001.106 기준:

```
                    자본금  주식발행초과금  기타자본  기타포괄손익  이익잉여금    총계
                                                    누계액
전기초 잔액           xxx      xxx         xxx       xxx         xxx        xxx
총포괄이익
  당기순이익           -        -           -         -          xxx        xxx
  기타포괄손익          -        -           -        xxx          -         xxx
자본거래
  유상증자            xxx      xxx          -         -           -         xxx
  자기주식 취득         -        -         (xxx)       -           -        (xxx)
  배당금               -        -           -         -         (xxx)      (xxx)
전기말 잔액           xxx      xxx         xxx       xxx         xxx        xxx

당기초 잔액           xxx      xxx         xxx       xxx         xxx        xxx
총포괄이익
  당기순이익           -        -           -         -          xxx        xxx
  기타포괄손익          -        -           -        xxx          -         xxx
자본거래
  ...
당기말 잔액           xxx      xxx         xxx       xxx         xxx        xxx
```

## Excel Generation (Multi-Sheet)

```javascript
const ExcelJS = require('exceljs');
const wb = new ExcelJS.Workbook();
wb.creator = 'Open Magi Bot';
wb.created = new Date();

// Common styles
const headerFill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFE2EFDA' } };
const sectionFont = { bold: true, size: 12 };
const numFmt = '#,##0';

function setupSheet(name, columns) {
  const ws = wb.addWorksheet(name);
  // Title row
  ws.mergeCells('A1:D1');
  ws.getCell('A1').font = { size: 16, bold: true };
  ws.getCell('A1').alignment = { horizontal: 'center' };
  // Period row
  ws.mergeCells('A2:D2');
  ws.getCell('A2').alignment = { horizontal: 'center' };
  // Entity row
  ws.mergeCells('A3:D3');
  ws.getCell('A3').alignment = { horizontal: 'center' };
  // Column widths
  columns.forEach((w, i) => { ws.getColumn(i + 1).width = w; });
  return ws;
}

// Sheet 1: 재무상태표
const bs = setupSheet('재무상태표', [8, 40, 20, 20]);
bs.getCell('A1').value = '재 무 상 태 표';
bs.getCell('A2').value = '제 XX 기  YYYY년 MM월 DD일 현재';
bs.getRow(5).values = ['', '과 목', '당기', '전기'];
bs.getRow(5).font = { bold: true };
bs.getRow(5).fill = headerFill;
bs.getColumn(3).numFmt = numFmt;
bs.getColumn(4).numFmt = numFmt;

// Sheet 2: 포괄손익계산서
const is = setupSheet('포괄손익계산서', [8, 40, 20, 20]);
is.getCell('A1').value = '포 괄 손 익 계 산 서';
is.getCell('A2').value = '제 XX 기  YYYY년 MM월 DD일 부터  YYYY년 MM월 DD일 까지';
is.getRow(5).values = ['', '과 목', '당기', '전기'];
is.getRow(5).font = { bold: true };
is.getRow(5).fill = headerFill;
is.getColumn(3).numFmt = numFmt;
is.getColumn(4).numFmt = numFmt;

// Sheet 3: 자본변동표
const ce = setupSheet('자본변동표', [25, 15, 15, 15, 15, 15, 15]);
ce.getCell('A1').value = '자 본 변 동 표';
ce.mergeCells('A1:G1');
ce.getCell('A2').value = '제 XX 기  YYYY년 MM월 DD일 부터  YYYY년 MM월 DD일 까지';
ce.mergeCells('A2:G2');
ce.getRow(5).values = ['', '자본금', '주식발행초과금', '기타자본', '기타포괄손익누계액', '이익잉여금', '총계'];
ce.getRow(5).font = { bold: true };
ce.getRow(5).fill = headerFill;
for (let c = 2; c <= 7; c++) { ce.getColumn(c).numFmt = numFmt; }

// ... populate rows with actual ledger data ...

await wb.xlsx.writeFile('/tmp/financial-statements.xlsx');
console.log('Created /tmp/financial-statements.xlsx');
```

Send the file:
Use `FileDeliver(target="chat")` with the generated artifact id.

## Cross-Verification Checklist

생성 후 반드시 검증:

| Check | Formula |
|-------|---------|
| 재무상태표 대차 | 자산 총계 = 부채 총계 + 자본 총계 |
| 당기순이익 일치 | 포괄손익계산서 당기순이익 = 자본변동표 당기순이익 |
| 이익잉여금 | 기초 + 당기순이익 - 배당금 = 기말 |
| 자본 총계 | 자본변동표 기말 총계 = 재무상태표 자본 총계 |
| 현금 | 현금흐름표 기말 = 재무상태표 현금및현금성자산 (cash-flow-statement 스킬 연계) |

## Workflow

1. KB에서 시산표/원장 데이터 검색
2. 누락 정보 확인 (기간, 법인, 기준, 전기 데이터)
3. 시산표 파싱 → 계정과목 분류
4. 3개 재무제표 동시 생성 (xlsx 멀티시트)
5. Cross-verification 수행
6. 불일치 시 원인 분석 후 사용자에게 확인
7. `FileDeliver(target="chat")`로 전송

## Combined Workflow

현금흐름표까지 포함한 완전한 재무제표 세트가 필요한 경우:
1. 이 스킬로 재무상태표 + 포괄손익계산서 + 자본변동표 생성
2. `/현금흐름표` (cash-flow-statement 스킬)로 현금흐름표 추가
3. 4개 재무제표 간 cross-verification 수행

## Standards Reference

```bash
# K-IFRS 1001 전문
integration.sh "accounting/tool" '{"action": "accounting-get-standard", "args": {"id": "1001"}}'
# XBRL 계정과목 매핑
integration.sh "accounting/tool" '{"action": "accounting-account-mapping", "args": {"accounts": ["매출채권"]}}'
```

## Caveats

- **DRAFT only.** 공인회계사의 전문적 검토 필수.
- **Do not fabricate numbers.** 사용자 제공 데이터만 사용.
- **When in doubt, ask.** 계정 분류가 모호한 경우 선택지 제시.
- **Currency:** KRW (원) 기본, `#,##0` 형식.
- **K-GAAP 차이:** 일반기업회계기준은 포괄손익계산서 → 손익계산서, 일부 항목 차이.
- **연결재무제표:** 별도/연결 기준에 따라 비지배지분, 관계기업 관련 항목 추가.
