---
name: autonomous-trading
description: Use when user wants to trade crypto, stocks, or manage an autonomous trading bot. Also use for portfolio management, market making, arbitrage, or any trading-related request.
metadata:
  author: openmagi
  version: "1.0"
---

# Autonomous Trading Engine

Hyperliquid 등 거래소에서 자율 트레이딩을 실행하는 봇 스킬.

## When to Use
- 유저가 트레이딩/매매를 하고 싶다고 할 때
- 시장 분석 + 자동 매매를 원할 때
- 마켓 메이킹, 차익거래, 모멘텀 전략을 실행할 때

## Onboarding Flow

유저가 처음 트레이딩을 요청하면 아래 순서로 진행:

### Step 1: 거래소 선택
"어떤 거래소에서 트레이딩할래?"
- Hyperliquid (추천, 테스트넷 지원)
- Binance (Phase 2)
- Alpaca US Stocks (Phase 5)

### Step 2: 인증 설정
**Hyperliquid (온체인):**
- Privy 월렛 자동 사용 (별도 API 키 불필요)
- 또는 유저가 직접 private key 제공

**CEX/브로커:**
- API 키 + 시크릿을 대화로 수신
- 즉시 암호화 저장 (credentials.enc)
- 대화 기록에서 키 삭제 안내

### Step 3: 네트워크 선택
- "테스트넷으로 시작할까? (추천)" → testnet
- "메인넷" → mainnet (잔고 확인 필수)

### Step 4: 엔진 설치
```
system.run ["sh", "-c", "cd skills/trading/engine && npm install --production 2>&1"]
```
설치 완료 확인 후 다음 단계.

### Step 5: 잔고 확인
```
system.run ["node", "skills/trading/engine/index.mjs", "status"]
```

### Step 6: 전략 선택
"어떤 전략으로 시작할래?"

**초보자:** APEX 자율 모드 (추천)
- 프리셋: conservative / default / aggressive
- Radar가 기회 탐색 → Pulse가 모멘텀 감지 → 자동 진입/청산

**커스텀 전략:**
- simple-mm: 고정 스프레드 마켓 메이킹
- avellaneda-mm: 재고 인식 최적 MM
- LLM 커스텀: 자연어로 전략 정의

**LLM 커스텀 예시:**
"ETH가 RSI 30 이하면 5% 롱, 70 이상이면 절반 익절, 손절 -3%"

### Step 7: 검증 (Mock Test)
```
system.run ["node", "skills/trading/engine/index.mjs", "start", "--mock", "--max-ticks", "5"]
```
Mock 결과 확인 → 문제 없으면 실전 시작.

### Step 8: 실전 시작
```
system.run ["node", "skills/trading/engine/index.mjs", "start"]
```
엔진이 백그라운드로 실행됨. Heartbeat이 55분마다 상태 체크.

## User Commands

유저가 대화 중 사용할 수 있는 명령:

| 명령 | 실행 |
|------|------|
| "상태" / "status" | `system.run ["node", "skills/trading/engine/index.mjs", "status"]` |
| "중지" / "stop" | `system.run ["node", "skills/trading/engine/index.mjs", "stop"]` |
| "전부 청산" | `system.run ["node", "skills/trading/engine/index.mjs", "close-all"]` |
| "리포트" | `system.run ["node", "skills/trading/engine/index.mjs", "reflect"]` |
| "전략 바꿔" | 엔진 중지 → 전략 재설정 → 재시작 |
| "레버리지 N배로" | configs/engine.json 수정 → 엔진 재시작 |
| "테스트넷 전환" | configs/engine.json 수정 → 엔진 재시작 |

## Risk Management

- **Risk Guardian**: OPEN → COOLDOWN (2연패 or drawdown ≥50%) → CLOSED (일일 손실 한도)
- **Guard**: 2-phase 트레일링 스탑 (breathe → lock)
- **Exchange SL**: 거래소 레벨 스탑로스 (프로세스 크래시해도 유지)
- **일일 손실 한도**: preset별 $250-$1,000
- **LLM 안전장치**: maxPositionPct, 레버리지 캡, 올인 차단, validateDecision 게이트

## APEX Presets

| Preset | 슬롯 | 레버리지 | Radar 임계치 | 일일 손실 한도 |
|--------|------|---------|-------------|--------------|
| conservative | 2 | 5x | 190 | $250 |
| default | 3 | 10x | 170 | $500 |
| aggressive | 3 | 15x | 150 | $1,000 |

## Strategies Reference

### Market Making
| 전략 | 설명 |
|------|------|
| simple-mm | 고정 스프레드 양방향 호가 |
| avellaneda-mm | Avellaneda-Stoikov 재고 인식 최적 MM |

### LLM Custom
유저가 자연어로 전략 정의. 매 틱마다 시장 스냅샷 + 유저 전략 프롬프트를 LLM에 전송. Risk Guardian을 반드시 통과.

## HEARTBEAT Integration

HEARTBEAT.md에 아래 섹션 추가:
```
## Trading Engine Check
system.run ["node", "skills/trading/engine/index.mjs", "heartbeat"]
- 엔진 alive 확인 → 죽었으면 재시작
- SCRATCHPAD에 포지션/PnL 업데이트
- 이상 감지 시 유저에게 알림
```

## Red Flags
- API 키를 MEMORY.md나 SCRATCHPAD에 절대 저장하지 말 것
- 테스트넷에서 충분히 검증 후 메인넷 전환
- 이 도구는 자동화 트레이딩을 제공하며, 투자 자문이 아님
- 손실 가능성에 대해 유저에게 반드시 고지
