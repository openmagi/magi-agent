---
name: crypto-market-data
description: Use when looking up cryptocurrency prices, market capitalization, trading volume, exchange data, or crypto market trends. Also use for Bitcoin, Ethereum, or altcoin research.
---

# Crypto Market Data

CoinCap API와 CoinGecko API로 암호화폐 시세, 시총, 거래소, 히스토리를 조회한다. CoinCap은 API key 불필요.

## When to Use

- 암호화폐 현재 시세 조회
- 시가총액 순위 및 비교
- 가격 히스토리 (차트 데이터)
- 거래소/거래쌍 정보
- 환율 조회 (BTC/USD, ETH/KRW 등)

## CoinCap API v2 (무료, Key 불필요)

**Base URL**: `https://api.coincap.io/v2`

### 1. 전체 암호화폐 목록

```
web_fetch "https://api.coincap.io/v2/assets?limit=10"
```

Response: `data[]` → `id`, `rank`, `symbol`, `name`, `priceUsd`, `marketCapUsd`, `volumeUsd24Hr`, `changePercent24Hr`, `supply`

### 2. 특정 코인 조회

```
web_fetch "https://api.coincap.io/v2/assets/bitcoin"
```

### 3. 가격 히스토리

```
web_fetch "https://api.coincap.io/v2/assets/bitcoin/history?interval=d1&start=1704067200000&end=1735689600000"
```

Parameters:
- `interval`: `m1`, `m5`, `m15`, `m30`, `h1`, `h2`, `h6`, `h12`, `d1`
- `start` / `end`: Unix timestamp (밀리초)

Response: `data[]` → `priceUsd`, `time`, `date`

### 4. 거래소 목록

```
web_fetch "https://api.coincap.io/v2/exchanges"
```

### 5. 거래쌍 (마켓)

```
web_fetch "https://api.coincap.io/v2/markets?exchangeId=binance&baseSymbol=BTC"
```

### 6. 환율

```
web_fetch "https://api.coincap.io/v2/rates/bitcoin"
```

## CoinGecko API v3 (대안)

**Base URL**: `https://api.coingecko.com/api/v3`

CoinGecko는 더 많은 데이터를 제공하지만 rate limit이 있음 (무료 10-50회/분).

### 주요 엔드포인트

```
web_fetch "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd,krw"
```

```
web_fetch "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=10&page=1"
```

```
web_fetch "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=30"
```

## Quick Reference

| 용도 | CoinCap | CoinGecko |
|------|---------|-----------|
| 현재 시세 | `/assets/{id}` | `/simple/price` |
| 시총 순위 | `/assets?limit=N` | `/coins/markets` |
| 히스토리 | `/assets/{id}/history` | `/coins/{id}/market_chart` |
| 거래소 | `/exchanges` | `/exchanges` |
| 환율 | `/rates` | `/simple/price?vs_currencies=krw` |
| Key 필요 | 불필요 | 불필요 (Pro는 필요) |
| Rate Limit | 200회/분 | 10-50회/분 |

## Workflow

1. **시세 조회**: CoinCap `/assets` 로 현재 가격 확인
2. **히스토리**: `/assets/{id}/history?interval=d1` 로 차트 데이터
3. **비교 분석**: 여러 코인 순위/시총 비교
4. **KRW 가격**: CoinGecko `vs_currencies=krw` 사용

## Red Flags

- CoinCap `id`는 심볼이 아님 — `bitcoin` (O), `BTC` (X)
- CoinCap 히스토리의 `start`/`end`는 밀리초 단위 Unix timestamp
- CoinGecko 무료 tier는 rate limit 엄격 — 대량 조회 시 CoinCap 우선
- 가격 데이터는 실시간이 아닌 약간의 지연 있음
- 이 자료는 투자 참고용이며 투자 자문이 아님
