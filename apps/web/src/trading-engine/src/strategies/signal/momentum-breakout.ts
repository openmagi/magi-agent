import type { TickContext, StrategyDecision, Candle } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'

/**
 * Compute Average True Range over the last `period` candles.
 * TR = max(high - low, |high - prevClose|, |low - prevClose|)
 */
export function computeATR(candles: Candle[], period: number): number {
  if (candles.length < 2) return 0

  const trs: number[] = []
  for (let i = 1; i < candles.length; i++) {
    const prev = candles[i - 1]!
    const curr = candles[i]!
    const tr = Math.max(
      curr.high - curr.low,
      Math.abs(curr.high - prev.close),
      Math.abs(curr.low - prev.close),
    )
    trs.push(tr)
  }

  const recent = trs.slice(-period)
  return recent.reduce((s, v) => s + v, 0) / recent.length
}

/**
 * Detect whether the latest candle's close breaks above/below
 * the highest high / lowest low of the prior `lookback` candles.
 */
export function detectBreakout(candles: Candle[], lookback: number): 'UP' | 'DOWN' | null {
  if (candles.length < lookback + 1) return null

  const lastCandle = candles[candles.length - 1]!
  const lookbackCandles = candles.slice(-(lookback + 1), -1)

  let highestHigh = -Infinity
  let lowestLow = Infinity

  for (const c of lookbackCandles) {
    if (c.high > highestHigh) highestHigh = c.high
    if (c.low < lowestLow) lowestLow = c.low
  }

  if (lastCandle.close > highestHigh) return 'UP'
  if (lastCandle.close < lowestLow) return 'DOWN'
  return null
}

/**
 * Check whether the latest candle's volume exceeds threshold * average volume
 * of the prior candles.
 */
export function volumeConfirmed(candles: Candle[], threshold: number): boolean {
  if (candles.length < 2) return false

  const lastCandle = candles[candles.length - 1]!
  const priorCandles = candles.slice(0, -1)
  const avgVolume = priorCandles.reduce((s, c) => s + c.volume, 0) / priorCandles.length

  return lastCandle.volume > threshold * avgVolume
}

export class MomentumBreakout extends BaseStrategy {
  readonly name = 'momentum-breakout'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, candles, config } = ctx
    const symbol = ticker.symbol

    const atrPeriod = typeof config.params['atr_period'] === 'number' ? config.params['atr_period'] : 14
    const lookbackPeriod = typeof config.params['lookback_period'] === 'number' ? config.params['lookback_period'] : 20
    const volThreshold = typeof config.params['volume_threshold'] === 'number' ? config.params['volume_threshold'] : 2.0
    const atrMultiplier = typeof config.params['atr_multiplier'] === 'number' ? config.params['atr_multiplier'] : 2.0
    const orderSize = typeof config.params['order_size'] === 'number' ? config.params['order_size'] : 0.1
    const maxPosition = typeof config.params['max_position'] === 'number' ? config.params['max_position'] : 1.0

    // Calculate net position for the symbol
    const netPosition = positions
      .filter((p) => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    // Detect breakout direction
    const breakout = detectBreakout(candles, lookbackPeriod)

    // No breakout → HOLD
    if (breakout === null) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'MomentumBreakout: no breakout detected',
      }]
    }

    // Volume must confirm
    if (!volumeConfirmed(candles, volThreshold)) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'MomentumBreakout: breakout detected but volume not confirmed',
      }]
    }

    const atr = computeATR(candles, atrPeriod)
    const entryPrice = ticker.lastPrice

    if (breakout === 'UP') {
      // BUY signal: check max position
      if (netPosition >= maxPosition) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: 'MomentumBreakout: max long position reached',
        }]
      }

      return [{
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'IOC',
        confidence: 70,
        reason: `MomentumBreakout: UP breakout confirmed, ATR=${atr.toFixed(4)}`,
        stopLoss: entryPrice - atr * atrMultiplier,
      }]
    }

    // breakout === 'DOWN'
    if (netPosition <= -maxPosition) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'MomentumBreakout: max short position reached',
      }]
    }

    return [{
      action: 'SELL',
      symbol,
      size: orderSize,
      orderType: 'IOC',
      confidence: 70,
      reason: `MomentumBreakout: DOWN breakout confirmed, ATR=${atr.toFixed(4)}`,
      stopLoss: entryPrice + atr * atrMultiplier,
    }]
  }
}
