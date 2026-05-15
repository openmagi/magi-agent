---
name: polymarket
description: "예측시장(Polymarket) 데이터 조회. prediction market, 예측시장, Polymarket, betting odds, 확률, 이벤트 예측, 베팅, 승률, 시장 분석 관련 질문에 사용."
metadata:
  author: openmagi
  version: "1.0"
  phase: "read-only"
---

# Polymarket 예측시장 — 조회 · 분석 · 트렌드

Polymarket(polymarket.com) 예측시장 데이터를 조회하고 분석한다. Phase 1은 읽기 전용 — 트레이딩은 추후 지원 예정.

## When to Use
- 특정 이벤트의 예측 확률을 알고 싶을 때 (선거, 경제, 스포츠, 크립토 등)
- 트렌딩 예측시장을 둘러보고 싶을 때
- 시장 데이터 분석 (거래량, 유동성, 가격 추이)
- 이벤트 결과 확률의 시계열 변화를 추적할 때
- 시장 심리/센티먼트를 예측 확률로 파악하고 싶을 때

## Data Model

Polymarket 데이터는 3단계 구조:

```
Event (이벤트)
├── title: "2026년 미국 대선 승자는?"
├── volume24hr: 24시간 거래량
└── markets[] (시장 목록)
    ├── Market A: "트럼프 당선?" → outcomes: ["Yes","No"], outcomePrices: ["0.55","0.45"]
    ├── Market B: "해리스 당선?" → outcomes: ["Yes","No"], outcomePrices: ["0.30","0.70"]
    └── Market C: "디샌티스 당선?" → outcomes: ["Yes","No"], outcomePrices: ["0.08","0.92"]
```

**핵심 개념:**
- **Event**: 상위 질문. 1개 이상의 Market을 포함
- **Market**: 바이너리(Yes/No) 거래 가능한 개별 시장
- **outcomePrices**: 각 결과의 내재 확률. `["0.65","0.35"]` = Yes 65%, No 35%. **달러 가격이 아니라 확률**
- **conditionId**: CLOB API 쿼리에 사용하는 시장 식별자
- **clobTokenIds**: 각 outcome의 토큰 ID 배열 (index가 outcomes 배열과 1:1 매핑)
- **Resolution(정산)**: 이벤트 결과가 확정되면 시장이 정산됨. Yes가 맞으면 Yes 토큰 = $1, No 토큰 = $0

## API Reference

### Gamma API — 이벤트 & 시장 검색

Base URL: `https://gamma-api.polymarket.com`

#### 1. 트렌딩 마켓 조회

```bash
curl -s "https://gamma-api.polymarket.com/events?limit=10&order=volume24hr&ascending=false&active=true" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    d.forEach(e=>{
      const m=e.markets&&e.markets[0];
      if(!m)return;
      const prices=JSON.parse(m.outcomePrices||'[]');
      const yes=(parseFloat(prices[0]||0)*100).toFixed(1);
      const no=(parseFloat(prices[1]||0)*100).toFixed(1);
      const vol=m.volume24hr?(parseFloat(m.volume24hr)/1000).toFixed(1)+'K':'N/A';
      const end=m.endDate?m.endDate.slice(0,10):'TBD';
      console.log('| '+e.title.slice(0,60).padEnd(60)+' | '+yes+'%'.padStart(6)+' | '+no+'%'.padStart(6)+' | $'+vol.padStart(8)+' | '+end+' |');
    });
  "
```

**응답 구조 (Event):**
```json
{
  "id": "12345",
  "title": "Will Bitcoin reach $200K in 2026?",
  "slug": "will-bitcoin-reach-200k-in-2026",
  "volume": "5234567.89",
  "volume24hr": "234567.89",
  "liquidity": "456789.12",
  "startDate": "2026-01-01T00:00:00Z",
  "endDate": "2026-12-31T23:59:59Z",
  "markets": [
    {
      "id": "0x...",
      "question": "Will Bitcoin reach $200K in 2026?",
      "outcomes": "[\"Yes\",\"No\"]",
      "outcomePrices": "[\"0.35\",\"0.65\"]",
      "conditionId": "0x...",
      "clobTokenIds": "[\"token_yes_id\",\"token_no_id\"]",
      "volume24hr": "234567.89",
      "liquidity": "456789.12"
    }
  ]
}
```

#### 2. 이벤트 검색 (키워드)

```bash
curl -s "https://gamma-api.polymarket.com/events?limit=10&title=bitcoin&active=true" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    d.forEach(e=>console.log(e.id+' | '+e.title+' | vol24h: $'+(parseFloat(e.volume24hr||0)/1000).toFixed(1)+'K'));
  "
```

#### 3. 범용 검색 (public-search)

```bash
curl -s "https://gamma-api.polymarket.com/public-search?query=election&limit=10" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    (d||[]).forEach(e=>console.log(e.id+' | '+e.title));
  "
```

#### 4. 단일 이벤트 상세

```bash
curl -s "https://gamma-api.polymarket.com/events/EVENT_ID" | \
  node -e "
    const e=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    console.log('Title: '+e.title);
    console.log('Volume: $'+parseFloat(e.volume||0).toLocaleString());
    console.log('Liquidity: $'+parseFloat(e.liquidity||0).toLocaleString());
    (e.markets||[]).forEach(m=>{
      const p=JSON.parse(m.outcomePrices||'[]');
      console.log('  Market: '+m.question);
      console.log('  Yes: '+(parseFloat(p[0]||0)*100).toFixed(1)+'% | No: '+(parseFloat(p[1]||0)*100).toFixed(1)+'%');
      console.log('  conditionId: '+m.conditionId);
    });
  "
```

#### 5. 단일 마켓 상세

```bash
curl -s "https://gamma-api.polymarket.com/markets/MARKET_ID" | \
  node -e "
    const m=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    const p=JSON.parse(m.outcomePrices||'[]');
    console.log('Question: '+m.question);
    console.log('Yes: '+(parseFloat(p[0]||0)*100).toFixed(1)+'% | No: '+(parseFloat(p[1]||0)*100).toFixed(1)+'%');
    console.log('Volume: $'+parseFloat(m.volume||0).toLocaleString());
    console.log('24h Volume: $'+parseFloat(m.volume24hr||0).toLocaleString());
    console.log('Liquidity: $'+parseFloat(m.liquidity||0).toLocaleString());
    console.log('End: '+(m.endDate||'TBD'));
    console.log('Description: '+(m.description||'N/A').slice(0,500));
  "
```

#### 6. 카테고리(태그) 목록

```bash
curl -s "https://gamma-api.polymarket.com/tags" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    (d||[]).forEach(t=>console.log('- '+t.label+' ('+t.slug+')'));
  "
```

---

### CLOB API — 가격 & 오더북

Base URL: `https://clob.polymarket.com`

#### 7. 현재 가격 조회

```bash
# 단일 토큰 가격
curl -s "https://clob.polymarket.com/price?token_id=TOKEN_ID&side=buy" | \
  node -e "const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));console.log('Price: '+(parseFloat(d.price)*100).toFixed(1)+'%');"

# 시장 전체 가격 (conditionId 사용)
curl -s "https://clob.polymarket.com/prices?market=CONDITION_ID" | \
  node -e "const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));Object.entries(d).forEach(([k,v])=>console.log(k+': '+(parseFloat(v)*100).toFixed(1)+'%'));"
```

#### 8. 오더북 조회

```bash
curl -s "https://clob.polymarket.com/book?token_id=TOKEN_ID" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    console.log('=== BIDS (매수) ===');
    (d.bids||[]).slice(0,5).forEach(b=>console.log('  '+(parseFloat(b.price)*100).toFixed(1)+'% | $'+parseFloat(b.size).toFixed(2)));
    console.log('=== ASKS (매도) ===');
    (d.asks||[]).slice(0,5).forEach(a=>console.log('  '+(parseFloat(a.price)*100).toFixed(1)+'% | $'+parseFloat(a.size).toFixed(2)));
    const bestBid=d.bids&&d.bids[0]?parseFloat(d.bids[0].price):0;
    const bestAsk=d.asks&&d.asks[0]?parseFloat(d.asks[0].price):0;
    if(bestBid&&bestAsk)console.log('Spread: '+(((bestAsk-bestBid)*100).toFixed(2))+'%');
  "
```

#### 9. 미드포인트 & 스프레드

```bash
# 미드포인트
curl -s "https://clob.polymarket.com/midpoint?token_id=TOKEN_ID" | \
  node -e "const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));console.log('Midpoint: '+(parseFloat(d.mid)*100).toFixed(2)+'%');"

# 스프레드
curl -s "https://clob.polymarket.com/spread?token_id=TOKEN_ID" | \
  node -e "const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));console.log('Spread: '+(parseFloat(d.spread)*100).toFixed(2)+'%');"
```

#### 10. 가격 히스토리 (트렌드 분석)

```bash
curl -s "https://clob.polymarket.com/prices-history?market=CONDITION_ID&interval=1d&fidelity=60" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    const h=d.history||[];
    if(h.length===0){console.log('No history data');process.exit();}
    const recent=h.slice(-10);
    console.log('=== Price History (recent) ===');
    let prev=null;
    recent.forEach(pt=>{
      const date=new Date(pt.t*1000).toISOString().slice(0,10);
      const pct=(parseFloat(pt.p)*100).toFixed(1);
      let arrow='  ';
      if(prev!==null){
        const diff=parseFloat(pt.p)-prev;
        arrow=diff>0.005?' ^':diff<-0.005?' v':' -';
      }
      console.log(date+' | '+pct+'%'+arrow);
      prev=parseFloat(pt.p);
    });
    const first=parseFloat(h[0].p);
    const last=parseFloat(h[h.length-1].p);
    const change=((last-first)*100).toFixed(1);
    const dir=last>first?'UP':last<first?'DOWN':'FLAT';
    console.log('Trend: '+dir+' ('+change+'%p over period)');
  "
```

**interval 옵션:** `1d`, `1w`, `1m`, `all`
**fidelity:** 데이터 포인트 수 (60 = 60개 포인트)

---

### Data API — 포지션 & 분석

Base URL: `https://data-api.polymarket.com`

#### 11. 오픈 인터레스트 (미결제약정)

```bash
curl -s "https://data-api.polymarket.com/oi?market=CONDITION_ID" | \
  node -e "const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));console.log('Open Interest: $'+parseFloat(d.openInterest||0).toLocaleString());"
```

#### 12. 최근 거래 내역

```bash
curl -s "https://data-api.polymarket.com/trades?market=CONDITION_ID&limit=20" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    (d||[]).slice(0,10).forEach(t=>{
      const time=new Date(t.timestamp).toISOString().slice(0,16);
      const side=t.side||'?';
      const price=(parseFloat(t.price)*100).toFixed(1);
      const size=parseFloat(t.size).toFixed(2);
      console.log(time+' | '+side.padEnd(4)+' | '+price+'% | $'+size);
    });
  "
```

#### 13. 상위 홀더

```bash
curl -s "https://data-api.polymarket.com/holders?market=CONDITION_ID&limit=10" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    (d||[]).forEach((h,i)=>{
      console.log((i+1)+'. '+h.address.slice(0,10)+'... | $'+parseFloat(h.value||0).toLocaleString());
    });
  "
```

#### 14. 유저 포지션 조회 (지갑 주소 필요)

```bash
curl -s "https://data-api.polymarket.com/positions?user=WALLET_ADDRESS" | \
  node -e "
    const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8'));
    (d||[]).forEach(p=>{
      console.log(p.title+' | '+(p.outcome||'?')+' | size: $'+parseFloat(p.size||0).toFixed(2)+' | avg: '+(parseFloat(p.avgPrice||0)*100).toFixed(1)+'%');
    });
  "
```

---

## Workflow Examples

### 예시 1: 트렌딩 시장 둘러보기

유저: "지금 Polymarket에서 핫한 시장 뭐야?"

1. 트렌딩 이벤트 조회:
```bash
curl -s "https://gamma-api.polymarket.com/events?limit=10&order=volume24hr&ascending=false&active=true"
```
2. 결과를 테이블로 정리:

```
| 이벤트                              | Yes    | No     | 24h 거래량  | 마감일     |
|--------------------------------------|--------|--------|-------------|------------|
| Will Bitcoin reach $200K in 2026?    | 35.0%  | 65.0%  | $234.5K     | 2026-12-31 |
| US GDP growth above 3% in Q2?       | 42.3%  | 57.7%  | $156.2K     | 2026-07-15 |
| ...                                  | ...    | ...    | ...         | ...        |
```

### 예시 2: 특정 이벤트 검색 + 상세 분석

유저: "비트코인 관련 예측시장 찾아줘"

1. 검색: `curl -s "https://gamma-api.polymarket.com/events?limit=10&title=bitcoin&active=true"`
2. 관심 이벤트 상세 조회: `curl -s "https://gamma-api.polymarket.com/events/EVENT_ID"`
3. 가격 히스토리: `curl -s "https://clob.polymarket.com/prices-history?market=CONDITION_ID&interval=1d&fidelity=60"`
4. 오더북: `curl -s "https://clob.polymarket.com/book?token_id=TOKEN_ID"`
5. 종합 분석 제공

### 예시 3: 시장 심층 분석

유저: "이 시장 자세히 분석해줘" (특정 시장 선택 후)

1. 마켓 상세: `curl -s "https://gamma-api.polymarket.com/markets/MARKET_ID"`
2. 오픈 인터레스트: `curl -s "https://data-api.polymarket.com/oi?market=CONDITION_ID"`
3. 최근 거래: `curl -s "https://data-api.polymarket.com/trades?market=CONDITION_ID&limit=20"`
4. 상위 홀더: `curl -s "https://data-api.polymarket.com/holders?market=CONDITION_ID&limit=10"`
5. 가격 추이: `curl -s "https://clob.polymarket.com/prices-history?market=CONDITION_ID&interval=1w&fidelity=60"`
6. 종합 리포트:

```
## BTC $200K in 2026? — 시장 분석

**현재 확률:** Yes 35.0% / No 65.0%
**24h 거래량:** $234,567
**총 거래량:** $5,234,567
**유동성:** $456,789
**오픈 인터레스트:** $1,234,567
**마감:** 2026-12-31

### 가격 추이 (최근 7일)
2026-03-20 | 32.5%  ^
2026-03-21 | 33.1%  ^
2026-03-22 | 33.8%  ^
2026-03-23 | 34.2%  ^
2026-03-24 | 33.9%  v
2026-03-25 | 34.5%  ^
2026-03-26 | 35.0%  ^
Trend: UP (+2.5%p over 7 days)

### 오더북 요약
Best Bid: 34.8% | Best Ask: 35.2% | Spread: 0.4%

### 최근 거래
2026-03-26 14:32 | BUY  | 35.0% | $1,250.00
2026-03-26 14:28 | SELL | 34.9% | $800.50
...

### 해석
- 확률이 지난 7일간 꾸준히 상승 → 시장이 점점 낙관적
- 스프레드 0.4%로 매우 좁음 → 유동성 양호
- 대규모 거래(>$1K) 다수 → 기관/고래 참여 시사
```

---

## Output Formatting

### 확률 표시
- **항상** Yes와 No 양쪽 확률을 함께 표시: `Yes 65.0% / No 35.0%`
- 멀티마켓 이벤트는 각 후보별 확률 나열:
  ```
  트럼프: 55.0%
  해리스: 30.0%
  디샌티스: 8.0%
  기타: 7.0%
  ```

### 테이블 형식 (시장 목록)
```
| 시장 질문 | Yes | No | 24h 거래량 | 유동성 | 마감 |
```

### 가격 추이 (텍스트 차트)
```
날짜       | 확률   | 방향
2026-03-20 | 32.5%  ^  (상승)
2026-03-21 | 33.1%  ^  (상승)
2026-03-22 | 32.8%  v  (하락)
2026-03-23 | 32.8%  -  (보합)
```
방향 표시: `^` 상승, `v` 하락, `-` 보합 (0.5%p 이내 변동)

### 금액 표시
- 1K 이상: `$234.5K`
- 1M 이상: `$1.2M`
- 1K 미만: `$567.89`

---

## 시장 데이터 해석 가이드

### Volume (거래량)
- **24h 거래량 높음**: 시장에 대한 관심이 높고 활발히 거래 중
- **거래량 급증**: 관련 뉴스/이벤트 발생 → 확률이 빠르게 움직일 수 있음
- **거래량 낮음**: 관심이 적거나 결과가 거의 확정적인 시장

### Liquidity (유동성)
- **유동성 높음**: 오더북이 두꺼움 → 대규모 거래해도 가격 변동 적음, 시장 가격이 신뢰할 만함
- **유동성 낮음**: 소규모 거래에도 가격이 크게 움직임 → 확률이 덜 신뢰할 만함
- 일반적으로 유동성 $100K+ 시장이 의미 있는 시그널

### Spread (스프레드)
- **좁은 스프레드** (<1%): 시장이 효율적, 매수/매도 합의가 높음
- **넓은 스프레드** (>3%): 불확실성 높거나 유동성 부족

### Open Interest (미결제약정)
- 현재 시장에 묶여 있는 총 자금
- 높을수록 시장에 대한 확신(베팅)이 큼
- OI 증가 + 확률 상승 = 강한 상승 신호
- OI 증가 + 확률 하락 = 강한 하락 신호

### Price History (가격 추이)
- 꾸준한 상승/하락: 시장 컨센서스가 한 방향으로 이동 중
- 급격한 변동: 뉴스/이벤트에 의한 충격
- 안정적 횡보: 시장이 현재 확률에 합의

---

## Rules

1. **outcomePrices는 확률이다** — 달러 가격이 아님. `0.65` = 65% 확률
2. **항상 Yes와 No 양쪽 확률을 표시** — 한쪽만 보여주면 오해 소지
3. **시장 요약에 volume과 liquidity를 반드시 포함** — 확률만으로는 신뢰도 판단 불가
4. **확률 =/= 예측** — "시장이 65% 확률을 부여" =/= "이것이 반드시 일어남". 시장의 집단 판단일 뿐
5. **트레이딩 요청 시**: "트레이딩 기능은 현재 준비 중입니다. Polymarket에서 직접 거래하실 수 있습니다: https://polymarket.com" 안내
6. **Resolution(정산) 설명**: 유저가 물으면 "이벤트 결과가 확정되면 정산됩니다. 맞는 쪽 토큰은 $1, 틀린 쪽은 $0이 됩니다" 설명
7. **API 에러 시**: Gamma API는 공개 API로 rate limit이 있을 수 있음. 429 에러 시 잠시 후 재시도
8. **outcomes/outcomePrices는 문자열 JSON** — `JSON.parse()` 필요. 예: `outcomes: "[\"Yes\",\"No\"]"`
9. **conditionId vs clobTokenIds**: Gamma API 결과에서 conditionId는 CLOB의 `market` 파라미터에, clobTokenIds의 개별 값은 `token_id` 파라미터에 사용
10. **링크 제공**: 마켓 상세 조회 시 `https://polymarket.com/event/EVENT_SLUG` 링크를 함께 제공
