import type { TickContext, StrategyDecision } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'
import { calculateRSI } from '../../signals/radar.js'

export interface ConvictionFactors {
  rsiScore: number       // 0-25: RSI extreme (< 25 or > 75)
  volumeScore: number    // 0-25: volume surge vs average
  oiScore: number        // 0-25: open interest magnitude
  fundingScore: number   // 0-25: funding rate alignment
}

export interface ConvictionResult {
  total: number
  direction: 'BUY' | 'SELL'
  factors: ConvictionFactors
}

/**
 * Compute conviction score from multiple market signals.
 * Total = sum of 4 factor scores (each 0-25, total max 100).
 */
export function computeConviction(ctx: TickContext): ConvictionResult {
  const { ticker, candles } = ctx

  // RSI factor (0-25)
  const rsi = calculateRSI(candles, 14)
  let rsiScore: number
  let direction: 'BUY' | 'SELL'

  if (rsi < 25) {
    rsiScore = 25
    direction = 'BUY'
  } else if (rsi < 30) {
    rsiScore = 20
    direction = 'BUY'
  } else if (rsi < 40) {
    rsiScore = 10
    direction = 'BUY'
  } else if (rsi > 75) {
    rsiScore = 25
    direction = 'SELL'
  } else if (rsi > 70) {
    rsiScore = 20
    direction = 'SELL'
  } else if (rsi > 60) {
    rsiScore = 10
    direction = 'SELL'
  } else {
    // RSI 40-60: neutral, weak signal
    rsiScore = 0
    direction = rsi < 50 ? 'BUY' : 'SELL'
  }

  // Volume factor (0-25): higher 24h volume → stronger signal
  let volumeScore: number
  if (ticker.volume24h > 5_000_000) volumeScore = 25
  else if (ticker.volume24h > 2_000_000) volumeScore = 20
  else if (ticker.volume24h > 1_000_000) volumeScore = 15
  else if (ticker.volume24h > 500_000) volumeScore = 10
  else volumeScore = 5

  // OI factor (0-25): higher OI → more liquidity → stronger signal
  let oiScore: number
  if (ticker.openInterest > 5_000_000) oiScore = 25
  else if (ticker.openInterest > 2_000_000) oiScore = 20
  else if (ticker.openInterest > 1_000_000) oiScore = 15
  else if (ticker.openInterest > 500_000) oiScore = 10
  else oiScore = 5

  // Funding factor (0-25): alignment with direction
  const absFunding = Math.abs(ticker.fundingRate)
  let fundingScore: number

  const fundingAligned =
    (ticker.fundingRate < 0 && direction === 'BUY') ||
    (ticker.fundingRate > 0 && direction === 'SELL')

  if (fundingAligned) {
    if (absFunding > 0.005) fundingScore = 25
    else if (absFunding > 0.001) fundingScore = 20
    else fundingScore = 15
  } else {
    if (absFunding > 0.005) fundingScore = 5
    else fundingScore = 10
  }

  const total = rsiScore + volumeScore + oiScore + fundingScore

  return {
    total,
    direction,
    factors: { rsiScore, volumeScore, oiScore, fundingScore },
  }
}

export class AggressiveTaker extends BaseStrategy {
  readonly name = 'aggressive-taker'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, config } = ctx
    const symbol = ticker.symbol

    const minConviction = typeof config.params['min_conviction'] === 'number' ? config.params['min_conviction'] : 75
    const orderSize = typeof config.params['order_size'] === 'number' ? config.params['order_size'] : 0.1
    const maxPosition = typeof config.params['max_position'] === 'number' ? config.params['max_position'] : 0.5
    const stopPct = typeof config.params['stop_pct'] === 'number' ? config.params['stop_pct'] : 1.0

    // Calculate net position for the symbol
    const netPosition = positions
      .filter((p) => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    const conviction = computeConviction(ctx)

    // Not enough conviction → HOLD
    if (conviction.total < minConviction) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: `AggressiveTaker: conviction ${conviction.total} below threshold ${minConviction}`,
      }]
    }

    if (conviction.direction === 'BUY') {
      // Check max position
      if (netPosition >= maxPosition) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: 'AggressiveTaker: max long position reached',
        }]
      }

      const entryPrice = ticker.ask // cross the spread
      return [{
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'IOC',
        confidence: conviction.total,
        reason: `AggressiveTaker: BUY conviction=${conviction.total} (rsi=${conviction.factors.rsiScore} vol=${conviction.factors.volumeScore} oi=${conviction.factors.oiScore} fund=${conviction.factors.fundingScore})`,
        stopLoss: entryPrice * (1 - stopPct / 100),
      }]
    }

    // direction === 'SELL'
    if (netPosition <= -maxPosition) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'AggressiveTaker: max short position reached',
      }]
    }

    const entryPrice = ticker.bid // cross the spread
    return [{
      action: 'SELL',
      symbol,
      size: orderSize,
      orderType: 'IOC',
      confidence: conviction.total,
      reason: `AggressiveTaker: SELL conviction=${conviction.total} (rsi=${conviction.factors.rsiScore} vol=${conviction.factors.volumeScore} oi=${conviction.factors.oiScore} fund=${conviction.factors.fundingScore})`,
      stopLoss: entryPrice * (1 + stopPct / 100),
    }]
  }
}
