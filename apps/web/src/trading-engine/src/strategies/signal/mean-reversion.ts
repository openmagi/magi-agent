import type { TickContext, StrategyDecision, Candle } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'

/**
 * Compute Simple Moving Average of the last `period` values.
 */
export function computeSMA(values: number[], period: number): number {
  if (values.length === 0) return 0
  const slice = values.slice(-period)
  return slice.reduce((s, v) => s + v, 0) / slice.length
}

/**
 * Compute population standard deviation of values around a given mean.
 */
export function computeStdDev(values: number[], mean: number): number {
  if (values.length <= 1) return 0
  const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / values.length
  return Math.sqrt(variance)
}

/**
 * Compute Bollinger Bands from candle close prices.
 */
export function computeBollingerBands(
  candles: Candle[],
  period: number,
  multiplier: number,
): { sma: number; upper: number; lower: number; stddev: number } {
  const closes = candles.map((c) => c.close)
  const recentCloses = closes.slice(-period)
  const sma = computeSMA(recentCloses, period)
  const stddev = computeStdDev(recentCloses, sma)
  const upper = sma + multiplier * stddev
  const lower = sma - multiplier * stddev

  return { sma, upper, lower, stddev }
}

export class MeanReversion extends BaseStrategy {
  readonly name = 'mean-reversion'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, candles, config } = ctx
    const symbol = ticker.symbol
    const price = ticker.mid

    const smaPeriod = typeof config.params['sma_period'] === 'number' ? config.params['sma_period'] : 20
    const bbMultiplier = typeof config.params['bb_multiplier'] === 'number' ? config.params['bb_multiplier'] : 2.0
    const orderSize = typeof config.params['order_size'] === 'number' ? config.params['order_size'] : 0.1
    const maxPosition = typeof config.params['max_position'] === 'number' ? config.params['max_position'] : 1.0
    const minDeviationPct = typeof config.params['min_deviation_pct'] === 'number' ? config.params['min_deviation_pct'] : 0.5

    // Calculate net position for the symbol
    const netPosition = positions
      .filter((p) => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    // Need enough candles for computation
    if (candles.length < smaPeriod) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'MeanReversion: insufficient candle data',
      }]
    }

    const bands = computeBollingerBands(candles, smaPeriod, bbMultiplier)

    // Check if price is below lower band (BUY signal)
    if (price < bands.lower) {
      const deviationPct = ((bands.lower - price) / bands.lower) * 100

      // Check minimum deviation threshold
      if (deviationPct < minDeviationPct) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: `MeanReversion: deviation ${deviationPct.toFixed(2)}% below min ${minDeviationPct}%`,
        }]
      }

      // Check max position
      if (netPosition >= maxPosition) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: 'MeanReversion: max long position reached',
        }]
      }

      // Scale confidence based on deviation magnitude (50-90)
      const confidence = Math.min(90, Math.round(50 + deviationPct * 10))

      return [{
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'GTC',
        confidence,
        reason: `MeanReversion: price below lower BB by ${deviationPct.toFixed(2)}%, SMA=${bands.sma.toFixed(2)}`,
        stopLoss: bands.lower - bands.stddev,
        takeProfit: bands.sma,
      }]
    }

    // Check if price is above upper band (SELL signal)
    if (price > bands.upper) {
      const deviationPct = ((price - bands.upper) / bands.upper) * 100

      // Check minimum deviation threshold
      if (deviationPct < minDeviationPct) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: `MeanReversion: deviation ${deviationPct.toFixed(2)}% below min ${minDeviationPct}%`,
        }]
      }

      // Check max position
      if (netPosition <= -maxPosition) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: 'MeanReversion: max short position reached',
        }]
      }

      // Scale confidence based on deviation magnitude (50-90)
      const confidence = Math.min(90, Math.round(50 + deviationPct * 10))

      return [{
        action: 'SELL',
        symbol,
        size: orderSize,
        orderType: 'GTC',
        confidence,
        reason: `MeanReversion: price above upper BB by ${deviationPct.toFixed(2)}%, SMA=${bands.sma.toFixed(2)}`,
        stopLoss: bands.upper + bands.stddev,
        takeProfit: bands.sma,
      }]
    }

    // Price within bands → HOLD
    return [{
      action: 'HOLD',
      symbol,
      size: 0,
      orderType: 'GTC',
      confidence: 0,
      reason: `MeanReversion: price within bands [${bands.lower.toFixed(2)}, ${bands.upper.toFixed(2)}]`,
    }]
  }
}
