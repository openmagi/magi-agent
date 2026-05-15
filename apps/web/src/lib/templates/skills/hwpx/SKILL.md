---
name: hwpx
description: "한글(HWPX) 문서 생성/읽기/편집 스킬. .hwpx 파일, 한글 문서, Hancom, OWPML 관련 요청 시 사용."
metadata:
  author: Canine89
  version: "1.0"
  source: https://github.com/Canine89/hwpxskill
---

# HWPX 문서 스킬 — 레퍼런스 복원 우선(XML-first) 워크플로우

한글(Hancom Office)의 HWPX 파일을 **XML 직접 작성** 중심으로 생성, 편집, 읽기할 수 있는 스킬.
HWPX는 ZIP 기반 XML 컨테이너(OWPML 표준)이다. python-hwpx API의 서식 버그를 완전히 우회하며, 세밀한 서식 제어가 가능하다.

## 환경 설정

런타임은 HWPX 스크립트와 템플릿을 이미 번들한다. 사용자 turn 중에 `git clone`, `pip install`, `apk add` 같은 설치 작업을 하지 않는다.

기본 경로:

1. `DocumentWrite(format="hwpx")`로 생성/편집
2. `FileDeliver(target="chat")`로 기본 전달
3. KB 보관이 명시된 경우만 `FileDeliver(target="kb" | "both")`

`DocumentWrite(format="hwpx")`는 단순 fast renderer가 아니라 agentic HWPX authoring loop를 먼저 탄다. 런타임이 `source.md`, starter `section0.xml`, 템플릿 header를 준비하고, 모델이 XML을 작성한 뒤 `build_hwpx.py`와 `validate.py`를 실행한다. 이후 결과 HWPX의 본문 텍스트가 `source.md`의 주요 marker를 실제로 포함하는지도 검사하므로, starter template만 빌드한 빈 문서는 완료 처리되지 않는다. 레퍼런스 HWPX 편집에서는 `analyze_template.py`로 기준 XML을 추출하고, 빌드 후 `page_guard.py`까지 통과해야 완료된다. 따라서 일반적인 생성/편집 요청은 raw `hwpx.sh` 스크립트보다 native `DocumentWrite`를 우선한다.

```bash
# hwpx.sh는 PATH에 등록되어 있다고 가정
# 필요 시 native tool이 내부적으로 동일한 런타임을 사용한다
```

## 기본 동작 모드 (필수): 첨부 HWPX 분석 → 고유 XML 복원(99% 근접) → 요청 반영 재작성

사용자가 `.hwpx`를 첨부한 경우, 이 스킬은 아래 순서를 **기본값**으로 따른다.

1. **native edit 우선**: 가능하면 `DocumentWrite(mode="edit", format="hwpx")`를 호출해 런타임의 reference-preserving loop를 사용
2. **레퍼런스 확보**: 첨부된 HWPX를 기준 문서로 사용
3. **심층 분석/추출**: `analyze_template.py`로 `header.xml`, `section0.xml` 추출
4. **구조 복원**: header 스타일 ID/표 구조/셀 병합/여백/문단 흐름을 최대한 동일하게 유지
5. **요청 반영 재작성**: 사용자가 요구한 텍스트/데이터만 교체하고 구조는 보존
6. **빌드/검증**: `build_hwpx.py` + `validate.py`로 결과 산출 및 무결성 확인
7. **쪽수 가드(필수)**: `page_guard.py`로 레퍼런스 대비 페이지 드리프트 위험 검사

### 99% 근접 복원 기준 (실무 체크리스트)

- `charPrIDRef`, `paraPrIDRef`, `borderFillIDRef` 참조 체계 동일
- 표의 `rowCnt`, `colCnt`, `colSpan`, `rowSpan`, `cellSz`, `cellMargin` 동일
- 문단 순서, 문단 수, 주요 빈 줄/구획 위치 동일
- 페이지/여백/섹션(secPr) 동일
- 변경은 사용자 요청 범위(본문 텍스트, 값, 항목명 등)로 제한

### 쪽수 동일(100%) 필수 기준

- 사용자가 레퍼런스를 제공한 경우 **결과 문서의 최종 쪽수는 레퍼런스와 동일해야 한다**
- 쪽수가 늘어날 가능성이 보이면 먼저 텍스트를 압축/요약해서 기존 레이아웃에 맞춘다
- 사용자 명시 요청 없이 `hp:p`, `hp:tbl`, `rowCnt`, `colCnt`, `pageBreak`, `secPr`를 변경하지 않는다
- `validate.py` 통과만으로 완료 처리하지 않는다. 반드시 `page_guard.py`도 통과해야 한다
- `page_guard.py` 실패 시 결과를 완료로 제출하지 않고, 원인(길이 과다/구조 변경)을 수정 후 재빌드한다

### 기본 실행 명령 (첨부 레퍼런스가 있을 때)

```bash
HWPX_DIR="$HOME/.openclaw/hwpxskill"

# 1) 레퍼런스 분석 + XML 추출
hwpx.sh analyze reference.hwpx \
  --extract-header /tmp/ref_header.xml \
  --extract-section /tmp/ref_section.xml

# 2) /tmp/ref_section.xml을 복제해 /tmp/new_section0.xml 작성
#    (구조 유지, 텍스트/데이터만 요청에 맞게 수정)

# 3) 복원 빌드
hwpx.sh build \
  --header /tmp/ref_header.xml \
  --section /tmp/new_section0.xml \
  --output result.hwpx

# 4) 검증
hwpx.sh validate result.hwpx

# 5) 쪽수 드리프트 가드 (필수)
hwpx.sh page-guard \
  --reference reference.hwpx \
  --output result.hwpx
```

---

## 워크플로우 1: XML-first 문서 생성 (레퍼런스 파일이 없을 때)

가능하면 먼저 native `DocumentWrite`로 생성하고, 이 워크플로우는 low-level XML 제어가 필요할 때만 직접 사용한다.

### 흐름

1. **템플릿 선택** (base/gonmun/report/minutes/proposal)
2. **section0.xml 작성** (본문 내용)
3. **(선택) header.xml 수정** (새 스타일 추가 필요 시)
4. **build_hwpx.py로 빌드**
5. **validate.py로 검증**

> 원칙: 사용자가 레퍼런스 HWPX를 제공한 경우에는 이 워크플로우 대신 상단의 "기본 동작 모드(레퍼런스 복원 우선)"를 사용한다.

### 기본 사용법

```bash
# 빈 문서 (base 템플릿)
hwpx.sh build --output result.hwpx

# 템플릿 사용
hwpx.sh build --template gonmun --output result.hwpx

# 커스텀 section0.xml 오버라이드
hwpx.sh build --template gonmun --section my_section0.xml --output result.hwpx

# header도 오버라이드
hwpx.sh build --header my_header.xml --section my_section0.xml --output result.hwpx

# 메타데이터 설정
hwpx.sh build --template report --section my.xml \
  --title "제목" --creator "작성자" --output result.hwpx
```

### 실전 패턴: section0.xml을 인라인 작성 → 빌드

```bash
# 1. section0.xml을 임시파일로 작성
SECTION=$(mktemp /tmp/section0_XXXX.xml)
cat > "$SECTION" << 'XMLEOF'
본문 내용
XMLEOF

# 2. 빌드
hwpx.sh build --section "$SECTION" --output result.hwpx

# 3. 정리
rm -f "$SECTION"
```

---

## 워크플로우 2: 기존 문서 편집 (unpack → Edit → pack)

기존 문서 편집도 가능하면 native `DocumentWrite(mode=edit)`를 먼저 사용하고, 세밀한 XML 보정이 필요할 때만 이 경로로 내려간다.

```bash
# 1. HWPX → 디렉토리 (XML pretty-print)
hwpx.sh unpack document.hwpx ./unpacked/

# 2. XML 직접 편집 (Read/Edit 도구로)
# 본문: ./unpacked/Contents/section0.xml
# 스타일: ./unpacked/Contents/header.xml

# 3. 다시 HWPX로 패키징
hwpx.sh pack ./unpacked/ edited.hwpx

# 4. 검증
hwpx.sh validate edited.hwpx
```

---

## 워크플로우 3: 읽기/텍스트 추출

```bash
# 순수 텍스트
hwpx.sh extract document.hwpx

# 테이블 포함
hwpx.sh extract document.hwpx --include-tables

# 마크다운 형식
hwpx.sh extract document.hwpx --format markdown
```

---

## 워크플로우 4: 검증

```bash
hwpx.sh validate document.hwpx
```

검증 항목: ZIP 유효성, 필수 파일 존재, mimetype 내용/위치/압축방식, XML well-formedness

---

## 워크플로우 5: 레퍼런스 기반 문서 생성 (첨부 HWPX가 있을 때 기본 적용)

사용자가 제공한 HWPX 파일을 분석하여 동일한 레이아웃의 문서를 생성하는 워크플로우.

### 흐름

1. **분석** — `analyze_template.py`로 레퍼런스 문서 심층 분석
2. **header.xml 추출** — 레퍼런스의 스타일 정의를 그대로 사용
3. **section0.xml 작성** — 분석 결과의 구조를 따라 새 내용으로 작성
4. **빌드** — 추출한 header.xml + 새 section0.xml로 빌드
5. **검증** — `validate.py`
6. **쪽수 가드** — `page_guard.py` (실패 시 재수정)

### 사용법

```bash
# 1. 심층 분석 (구조 청사진 출력)
hwpx.sh analyze reference.hwpx

# 2. header.xml과 section0.xml을 추출하여 참고용으로 보관
hwpx.sh analyze reference.hwpx \
  --extract-header /tmp/ref_header.xml \
  --extract-section /tmp/ref_section.xml

# 3. 분석 결과를 보고 새 section0.xml 작성
#    - 동일한 charPrIDRef, paraPrIDRef 사용
#    - 동일한 테이블 구조 (열 수, 열 너비, 행 수, rowSpan/colSpan)
#    - 동일한 borderFillIDRef, cellMargin

# 4. 추출한 header.xml + 새 section0.xml로 빌드
hwpx.sh build \
  --header /tmp/ref_header.xml \
  --section /tmp/new_section0.xml \
  --output result.hwpx

# 5. 검증
hwpx.sh validate result.hwpx

# 6. 쪽수 드리프트 가드 (필수)
hwpx.sh page-guard \
  --reference reference.hwpx \
  --output result.hwpx
```

### 분석 출력 항목

| 항목 | 설명 |
|------|------|
| 폰트 정의 | hangul/latin 폰트 매핑 |
| borderFill | 테두리 타입/두께 + 배경색 (각 면별 상세) |
| charPr | 글꼴 크기(pt), 폰트명, 색상, 볼드/이탤릭/밑줄/취소선, fontRef |
| paraPr | 정렬, 줄간격, 여백(left/right/prev/next/intent), heading, borderFillIDRef |
| 문서 구조 | 페이지 크기, 여백, 페이지 테두리, 본문폭 |
| 본문 상세 | 모든 문단의 id/paraPr/charPr + 텍스트 내용 |
| 표 상세 | 행×열, 열너비 배열, 셀별 span/margin/borderFill/vertAlign + 내용 |

### 핵심 원칙

- **charPrIDRef/paraPrIDRef를 그대로 사용**: 추출한 header.xml의 스타일 ID를 변경하지 말 것
- **열 너비 합계 = 본문폭**: 분석 결과의 열너비 배열을 그대로 복제
- **rowSpan/colSpan 패턴 유지**: 분석된 셀 병합 구조를 정확히 재현
- **cellMargin 보존**: 분석된 셀 여백 값을 동일하게 적용
- **페이지 증가 금지**: 사용자 명시 승인 없이 결과 쪽수를 늘리지 말 것
- **치환 우선 편집**: 새 문단/표 추가보다 기존 텍스트 노드 치환을 우선할 것

---

## section0.xml 작성 가이드

### 필수 구조

section0.xml의 첫 문단(`<hp:p>`)의 첫 런(`<hp:run>`)에 반드시 `<hp:secPr>` + `<hp:colPr>` 포함.

**Tip**: `templates/base/Contents/section0.xml`의 첫 문단을 그대로 복사하면 된다.

### 표 크기 계산

- **A4 본문폭**: 42520 HWPUNIT = 59528(용지) - 8504×2(좌우여백)
- **열 너비 합 = 본문폭** (42520)
- 예: 3열 균등 → 14173 + 14173 + 14174 = 42520
- 예: 2열 (라벨:내용 = 1:4) → 8504 + 34016 = 42520
- **행 높이**: 셀당 보통 2400~3600 HWPUNIT

### ID 규칙

- 문단 id: `1000000001`부터 순차 증가
- 표 id: `1000000099` 등 별도 범위 사용 권장
- 모든 id는 문서 내 고유해야 함

---

## 템플릿

| 템플릿 | 용도 | 특징 |
| --- | --- | --- |
| base | 기본 골격 | 최소 스타일, 빈 문서 시작점 |
| gonmun | 공문서 | 기관명, 수신처, 시행일자, 연락처 |
| report | 보고서 | 섹션 헤더, 들여쓰기, 체크박스 |
| minutes | 회의록 | 섹션 라벨, 테두리 구분 |
| proposal | 제안서 | 색상 헤더, 번호 뱃지 |

---

## 스크립트 요약

| hwpx.sh 명령 | Python 스크립트 | 용도 |
|--------------|----------------|------|
| `hwpx.sh build` | `build_hwpx.py` | **핵심** — 템플릿 + XML → HWPX 조립 |
| `hwpx.sh analyze` | `analyze_template.py` | HWPX 심층 분석 (레퍼런스 기반 생성의 청사진) |
| `hwpx.sh unpack` | `office/unpack.py` | HWPX → 디렉토리 (XML pretty-print) |
| `hwpx.sh pack` | `office/pack.py` | 디렉토리 → HWPX (mimetype first) |
| `hwpx.sh validate` | `validate.py` | HWPX 파일 구조 검증 |
| `hwpx.sh page-guard` | `page_guard.py` | 레퍼런스 대비 페이지 드리프트 위험 검사 (필수 게이트) |
| `hwpx.sh extract` | `text_extract.py` | HWPX 텍스트 추출 |

## 단위 변환

| 값 | HWPUNIT | 의미 |
|----|---------|------|
| 1pt | 100 | 기본 단위 |
| 10pt | 1000 | 기본 글자크기 |
| 1mm | 283.5 | 밀리미터 |
| 1cm | 2835 | 센티미터 |
| A4 폭 | 59528 | 210mm |
| A4 높이 | 84186 | 297mm |
| 좌우여백 | 8504 | 30mm |
| 본문폭 | 42520 | 150mm (A4-좌우여백) |

## Critical Rules

1. **HWPX만 지원**: `.hwp`(바이너리) 파일은 지원하지 않는다. 사용자가 `.hwp` 파일을 제공하면 **한글 오피스에서 `.hwpx`로 다시 저장**하도록 안내할 것. (파일 → 다른 이름으로 저장 → 파일 형식: HWPX)
2. **secPr 필수**: section0.xml 첫 문단의 첫 run에 반드시 secPr + colPr 포함
3. **mimetype 순서**: HWPX 패키징 시 mimetype은 첫 번째 ZIP 엔트리, ZIP_STORED
4. **네임스페이스 보존**: XML 편집 시 `hp:`, `hs:`, `hh:`, `hc:` 접두사 유지
5. **itemCnt 정합성**: header.xml의 charProperties/paraProperties/borderFills itemCnt가 실제 자식 수와 일치
6. **ID 참조 정합성**: section0.xml의 charPrIDRef/paraPrIDRef가 header.xml 정의와 일치
7. **검증**: 생성 후 반드시 `hwpx.sh validate`로 무결성 확인
8. **레퍼런스**: 상세 XML 구조는 `$HWPX_DIR/references/hwpx-format.md` 참조
9. **build_hwpx.py 우선**: 새 문서 생성은 build_hwpx.py 사용 (python-hwpx API 직접 호출 지양)
10. **빈 줄**: `<hp:p/>` 사용 (self-closing tag)
11. **레퍼런스 우선 강제**: 사용자가 HWPX를 첨부하면 반드시 `hwpx.sh analyze` + 추출 XML 기반으로 복원/재작성할 것
12. **쪽수 동일 필수**: 레퍼런스 기반 작업에서는 최종 결과의 쪽수를 레퍼런스와 동일하게 유지할 것
13. **무단 페이지 증가 금지**: 사용자 명시 요청/승인 없이 쪽수 증가를 유발하는 구조 변경 금지
14. **구조 변경 제한**: 사용자 요청이 없는 한 문단/표의 추가·삭제·분할·병합 금지 (치환 중심 편집)
15. **page_guard 필수 통과**: `validate.py`와 별개로 `page_guard.py`를 반드시 통과해야 완료 처리
