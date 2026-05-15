---
name: korean-corporate-disclosure
description: Use when looking up Korean company filings, financial statements, shareholder disclosures, or DART/FSS corporate data. Also use for Korean stock market company research.
---

# Korean Corporate Disclosure (한국 기업공시 - DART)

금융감독원 OpenDART API를 통해 한국 상장사 공시, 재무제표, 지분공시, 기업 개황을 조회한다.

## When to Use

- 한국 상장사 공시 검색 (사업보고서, 분기보고서 등)
- 재무제표 조회 (연결/개별, 연간/분기)
- 대량보유 상황보고 (5% 이상 지분)
- 임원/주요주주 소유보고
- 기업 기본정보 (CEO, 업종, 설립일 등)

## API Access

플랫폼이 API key를 제공하므로 `integration.sh dart/...` 로 바로 사용 가능. **무료** (크레딧 차감 없음).

응답 형식: `{ "data": <DART JSON 응답>, "synced_at": "..." }`

### 1. 공시 검색

```
integration.sh "dart/list?corp_code=00126380&bgn_de=20240101&end_de=20241231&page_count=10"
```

Parameters:
- `corp_code`: 기업 고유번호 (8자리)
- `bgn_de` / `end_de`: 검색 기간 (YYYYMMDD)
- `pblntf_ty`: 공시유형 (`A`=정기, `B`=주요사항, `C`=발행공시, `D`=지분공시, `E`=기타)
- `corp_cls`: 법인구분 (`Y`=유가, `K`=코스닥, `N`=코넥스, `E`=기타)
- `last_reprt_at`: 최종보고서만 (`Y`/`N`)
- `page_no`, `page_count`: 페이지네이션

Response data: `corp_name`, `report_nm`, `rcept_no` (접수번호), `rcept_dt` (접수일), `flr_nm` (공시제출인)

### 2. 기업 개황

```
integration.sh dart/company?corp_code=00126380
```

Response data: `corp_name`, `corp_name_eng`, `stock_code`, `ceo_nm`, `corp_cls`, `jurir_no` (법인등록번호), `bizr_no` (사업자등록번호), `adres`, `hm_url`, `est_dt`, `acc_mt` (결산월)

### 3. 재무제표 조회

**주요계정:**
```
integration.sh "dart/fnlttSinglAcnt?corp_code=00126380&bsns_year=2024&reprt_code=11011"
```

**전체 재무제표 (연결):**
```
integration.sh "dart/fnlttSinglAcntAll?corp_code=00126380&bsns_year=2024&reprt_code=11011&fs_div=CFS"
```

Report codes:
| reprt_code | 기간 |
|-----------|------|
| `11013` | 1분기 |
| `11012` | 반기 |
| `11014` | 3분기 |
| `11011` | 사업연도 (연간) |

`fs_div`: `CFS` (연결), `OFS` (개별)

Response data: `account_nm` (계정명), `thstrm_amount` (당기금액), `frmtrm_amount` (전기금액), `sj_div` (`BS`=재무상태표, `IS`=손익계산서, `CF`=현금흐름표)

### 4. 지분공시

**대량보유 상황보고 (5% 이상):**
```
integration.sh dart/majorstock?corp_code=00126380
```

Response data: `repror` (보고자), `stkqy` (보유주식수), `stkrt` (보유비율%), `stkqy_irds` (변동수량)

**임원/주요주주 소유보고:**
```
integration.sh dart/elestock?corp_code=00126380
```

### 5. 기업 고유번호 검색

```
integration.sh "dart/corpcode?corp_name=삼성전자"
```

기업명으로 DART 고유번호를 직접 검색. 정확히 일치하는 결과를 우선 반환, 없으면 부분 일치 (최대 20건).

Response data: `[{ corp_code, corp_name, stock_code, modify_date }]`

**참고**: corp_code는 주식코드(stock_code)와 다르다 — 8자리 기업 고유번호.

### 6. 공시 원본 조회

**공시 본문 전체 (소규모 문서) / 목차 (대규모 문서):**
```
integration.sh "dart/document?rcept_no=20250311001085"
```

- 500KB 미만: `{ type: "full", text: "..." }` — 전문 텍스트 반환
- 500KB 이상: `{ type: "toc", sections: [{ id, title, size_bytes }] }` — 목차 반환

**특정 섹션 조회 (대규모 문서용):**
```
integration.sh "dart/document-section?rcept_no=20250311001085&section_id=D-0-0-1-0"
```

Response data: `{ type: "section", section_id, text: "..." }`

**워크플로우**: 대규모 공시의 경우 `dart/document`로 목차 확인 → `dart/document-section`으로 필요한 섹션만 조회.

## Status Codes

| code | 의미 |
|------|------|
| `000` | 정상 |
| `010` | 등록되지 않은 키 |
| `011` | 사용 한도 초과 |
| `100` | 데이터 없음 |
| `800` | 필수 파라미터 누락 |

## 7. 검색 프리셋 (Search Presets)

공시 유형별 정규식 패턴을 외우지 않고 프리셋 이름으로 조회. `dart/list` 기본 파라미터에 아래 필터를 조합해 사용.

| preset | pblntf_ty | report_nm 정규식 | 용도 |
|--------|-----------|-----------------|------|
| `treasury_buy` | B | `자기주식.*취득` | 자기주식 취득 결정 (주가 부양 시그널) |
| `treasury_sell` | B | `자기주식.*처분` | 자기주식 처분 결정 |
| `treasury_trust` | B | `자기주식.*신탁` | 자기주식 신탁 계약 |
| `cb_issue` | B | `전환사채` | CB 발행결정 (희석 경계) |
| `bw_issue` | B | `신주인수권부사채` | BW 발행결정 |
| `eb_issue` | B | `교환사채` | EB 발행결정 |
| `rights_offering` | B | `유상증자` | 유상증자 결정 |
| `bonus_issue` | B | `무상증자` | 무상증자 결정 |
| `capital_reduction` | B | `감자` | 감자 결정 |
| `merger` | B | `합병` | 합병 결정 |
| `split` | B | `분할` | 분할 결정 |
| `stock_exchange` | B | `주식교환|주식이전` | 주식교환·이전 |
| `business_transfer` | B | `영업양도` | 영업양도 |
| `business_acquisition` | B | `영업양수` | 영업양수 |
| `large_holding_5pct` | D | (전체) | 지분공시 전체 (5%룰 + 임원지분) |
| `annual_report` | A | `사업보고서` | 사업보고서 |
| `half_report` | A | `반기보고서` | 반기보고서 |
| `quarterly_report` | A | `분기보고서` | 분기보고서 |
| `audit_report` | F | (전체) | 외부감사 관련 공시 전체 |
| `correction_all` | (any) | `\[기재정정\]|\[첨부정정\]|\[첨부추가\]` | 정정공시 전체 |
| `insolvency` | B | `부도발생|영업정지|회생절차|해산사유|채권은행` | 부실·법적 리스크 |
| `litigation` | B | `소송` | 소송 제기 |

**사용 패턴** (프리셋은 `dart/list`를 여러번 호출 + report_nm 필터링):

```
# "최근 30일 자기주식 취득한 상장사" → treasury_buy
today=$(date +%Y%m%d)
bgn=$(date -d "30 days ago" +%Y%m%d)
integration.sh "dart/list?bgn_de=$bgn&end_de=$today&pblntf_ty=B&page_count=100"
# 응답 list에서 report_nm =~ /자기주식.*취득/ 만 필터링
```

### 90일 자동분할 패턴

**전체시장 조회(`corp_code` 미지정)는 OpenDART 제약으로 3개월 이내만 허용**. 180일 조회가 필요하면 90일씩 분할 후 병합:

```bash
# 180일 분할 예시 (청크 3개)
for i in 0 90 180; do
  end=$(date -d "$i days ago" +%Y%m%d)
  bgn=$(date -d "$((i+90)) days ago" +%Y%m%d)
  integration.sh "dart/list?bgn_de=$bgn&end_de=$end&pblntf_ty=A&page_count=100"
done
# 중복 rcept_no 제거 후 병합
```

상한: 40 chunks ≈ 10년. 특정 기업(`corp_code`)이면 분할 불필요.

## 8. 자본 이벤트 Enum (Corporate Events)

`dart/list` 대신 **직접 자본이벤트 엔드포인트**를 호출하면 구조화된 데이터 획득. 36개 이벤트:

| event_type | endpoint | 설명 |
|-----------|----------|------|
| `default_occurrence` | `dfOcr` | 부도발생 |
| `business_suspension` | `bsnSp` | 영업정지 |
| `rehabilitation_filing` | `ctrcvsBgrq` | 회생절차 개시신청 |
| `dissolution_cause` | `dsRsOcr` | 해산사유 발생 |
| `bank_management_start` | `bnkMngtPcbg` | 채권은행 관리절차 개시 |
| `bank_management_stop` | `bnkMngtPcsp` | 채권은행 관리절차 중단 |
| `litigation` | `lwstLg` | 소송 등 제기 |
| `rights_offering` | `piicDecsn` | 유상증자 결정 |
| `bonus_issue` | `fricDecsn` | 무상증자 결정 |
| `rights_bonus_combo` | `pifricDecsn` | 유무상증자 결정 |
| `capital_reduction` | `crDecsn` | 감자 결정 |
| `cb_issuance` | `cvbdIsDecsn` | CB 발행결정 |
| `bw_issuance` | `bdwtIsDecsn` | BW 발행결정 |
| `eb_issuance` | `exbdIsDecsn` | EB 발행결정 |
| `cocobond_issuance` | `wdCocobdIsDecsn` | 조건부자본증권 발행결정 |
| `treasury_acquisition` | `tsstkAqDecsn` | 자기주식 취득 결정 |
| `treasury_disposal` | `tsstkDpDecsn` | 자기주식 처분 결정 |
| `treasury_trust_contract` | `tsstkAqTrctrCnsDecsn` | 자기주식취득 신탁계약 체결 |
| `treasury_trust_cancel` | `tsstkAqTrctrCcDecsn` | 자기주식취득 신탁계약 해지 |
| `stock_exchange` | `stkExtrDecsn` | 주식교환·이전 결정 |
| `company_split_merger` | `cmpDvmgDecsn` | 회사분할합병 결정 |
| `company_split` | `cmpDvDecsn` | 회사분할 결정 |
| `company_merger` | `cmpMgDecsn` | 회사합병 결정 |
| `asset_transfer_etc` | `astInhtrfEtcPtbkOpt` | 자산양수도·풋백옵션 |
| `tangible_asset_transfer` | `tgastTrfDecsn` | 유형자산 양도 결정 |
| `tangible_asset_acquisition` | `tgastInhDecsn` | 유형자산 양수 결정 |
| `other_corp_stock_transfer` | `otcprStkInvscrTrfDecsn` | 타법인 주식 양도 |
| `other_corp_stock_acquisition` | `otcprStkInvscrInhDecsn` | 타법인 주식 양수 |
| `business_transfer` | `bsnTrfDecsn` | 영업양도 결정 |
| `business_acquisition` | `bsnInhDecsn` | 영업양수 결정 |
| `bond_with_stock_right_acquisition` | `stkrtbdInhDecsn` | 주권관련 사채권 양수 |
| `bond_with_stock_right_transfer` | `stkrtbdTrfDecsn` | 주권관련 사채권 양도 |
| `overseas_listing_decision` | `ovLstDecsn` | 해외상장 결정 |
| `overseas_delisting_decision` | `ovDlstDecsn` | 해외상장폐지 결정 |
| `overseas_listing` | `ovLst` | 해외상장 |
| `overseas_delisting` | `ovDlst` | 해외상장폐지 |

```
integration.sh "dart/tsstkAqDecsn?corp_code=00126380&bgn_de=20230101&end_de=20260418"
```

**타임라인 모드:** 여러 이벤트를 병렬 조회 후 `rcept_dt` 순 통합 — 봇이 직접 구성.

## Analyst Recipes

### 내부자 매수/매도 클러스터 시그널 (insider_signal)

**데이터 소스:** `dart/majorstock` (5%룰) + `dart/elestock` (임원/주요주주)

**집계 로직:**
1. 두 API 병렬 호출 → 기간 필터링
2. 각 보고자(`repror`)의 `stkqy_irds` (변동수량) 합산
3. 순증 > 0 = 매수자, 순감 < 0 = 매도자
4. 분기별 클러스터링: 분기별 고유 매수자 수 vs 매도자 수

**시그널 판정** (cluster_threshold = 5 기본):
- `strong_buy_cluster`: buyers ≥ 5 AND buyers > sellers × 2
- `strong_sell_cluster`: sellers ≥ 5 AND sellers > buyers × 2
- `neutral_or_mixed`: 그 외

```
# 삼성전자 최근 1년
integration.sh "dart/majorstock?corp_code=00126380"
integration.sh "dart/elestock?corp_code=00126380"
# 응답 → 보고자별 stkqy_irds 집계 → 분기별 매수자/매도자 카운트
```

### 회계 리스크 스코어 (disclosure_anomaly)

**데이터 소스:** `dart/list` (3년치 공시 전체)

**가중치 (총 100점):**
| 플래그 | 조건 | 점수 |
|--------|------|------|
| `high_amendment_ratio` | 정정공시 비율 > 20% | +30 |
| `elevated_amendment_ratio` | 정정공시 비율 > 10% | +15 |
| `auditor_change` (2회 이상) | 감사인 2회 이상 교체 | +30 |
| `auditor_change` (1회) | 감사인 1회 교체 | +20 |
| `non_clean_audit_opinion` | 비적정 감사의견 발견 | +40 |
| `capital_stress_cluster` | 유상증자/CB/감자 등 ≥ 3건 | +10 |

**verdict:** `score ≥ 70` red_flag / `≥ 40` warning / `≥ 15` watch / `< 15` clean

```
# 3년치 공시 수집 (연도별 분할 - 전체시장 제약 회피)
for year in 2024 2025 2026; do
  integration.sh "dart/list?corp_code=00126380&bgn_de=${year}0101&end_de=${year}1231&page_count=100"
done
# amendments = report_nm 에 [기재정정]|[첨부정정] 포함 건수
# amendment_ratio = amendments / total
# 감사인 교체 = 감사보고서 flr_nm 시계열 변화
# 자본 스트레스 = 유상증자|CB|감자|무상증자 공시 수
```

### 버핏 퀄리티 체크리스트 (buffett_quality_snapshot)

**데이터 소스:** `dart/fnlttSinglAcnt?reprt_code=11011` (연간 주요계정) — N년치 (3년 간격으로 호출해 율 절약)

**필요 계정** (`account_nm` 매칭):
- 매출액(Revenue) / 영업이익(Operating Income) / 당기순이익(Net Income)
- 자본총계(Equity) / 부채총계(Liabilities)

**비율 계산:**
- ROE = 당기순이익 / 자본총계 × 100
- D/E = 부채총계 / 자본총계 × 100
- 영업이익률 = 영업이익 / 매출액 × 100
- CAGR(x, y, n) = (y / x)^(1/n) − 1

**체크리스트 (4가지):**
| 체크 | 조건 |
|------|------|
| `consistent_high_roe` | 관측된 모든 연도 ROE ≥ 15% |
| `low_debt` | 최근 D/E ≤ 100% |
| `growing_revenue` | 매출 CAGR ≥ 5% |
| `growing_earnings` | 순이익 CAGR ≥ 5% |

**overall_score:** `N/4` (N = 통과 개수)

```
# 5년 조회 예시: 2020, 2022, 2024 3번 호출로 5년 커버 (윈도우 3년 간격)
for year in 2020 2022 2024; do
  integration.sh "dart/fnlttSinglAcnt?corp_code=00126380&bsns_year=$year&reprt_code=11011"
done
# 연도별 → 매출/순이익/자본/부채 추출 → ROE·D/E·CAGR 계산 → 체크리스트 4종
```

**여러 기업 비교 시:** 각 기업 스냅샷 병렬 → 지표별 랭킹 (`by_roe_desc`, `by_debt_ratio_asc`, `by_revenue_cagr_desc`, `by_roe_stddev_asc`).

### 대용량 사업보고서 PDF/HWP 첨부 처리

DART 사업보고서는 원문 XML 외 **첨부 PDF/HWP**에 세부 내용. 추출 경로:

1. `dart/document?rcept_no=...`로 목차(TOC) 확인
2. 첨부파일은 document-worker로 라우트 (PDF/DOCX)
3. HWP/HWPX는 `hwpx-canine` 스킬 사용

## Workflow

1. **기업코드 확인**: `integration.sh "dart/corpcode?corp_name=삼성전자"`로 corp_code 검색
2. **기업 개황**: `integration.sh dart/company?corp_code=...`로 기본정보 확인
3. **공시 검색**: `integration.sh "dart/list?corp_code=...&bgn_de=...&end_de=..."`로 관심 공시 목록 조회 — 전체시장은 90일 분할
4. **공시 원문**: `integration.sh "dart/document?rcept_no=..."`로 공시 본문 조회 (대규모 → TOC → section 순서)
5. **재무제표**: `integration.sh "dart/fnlttSinglAcnt?corp_code=...&bsns_year=...&reprt_code=..."`로 주요 계정 조회
6. **지분 분석**: `integration.sh dart/majorstock?corp_code=...`로 대주주 현황 파악
7. **애널리스트 레시피**: insider_signal / disclosure_anomaly / buffett_quality_snapshot 은 위 레시피로 조합 실행

## Red Flags

- **중요**: `&`가 포함된 URL은 반드시 따옴표로 감싸야 함 (예: `integration.sh "dart/fnlttSinglAcnt?corp_code=00126380&bsns_year=2024&reprt_code=11011"`)
- 응답은 `{ data: { status: "000", list: [...] } }` 형태 — `data` 필드에서 추출
- `corp_code`는 주식코드(stock_code)와 다름 — 기업 고유번호 8자리
- 재무데이터는 2015년 이후부터 제공
- **전체시장 조회(corp_code 미지정)는 3개월 제약** — 90일씩 분할 필수
- 계산 결과는 봇이 직접 산수 → 숫자 검증은 봇 responsibility. 확신 없으면 `dart/fnlttSinglAcntAll`로 raw 확인 권장
- 이 자료는 투자 참고용이며 투자 자문이 아님
