/**
 * Prediction Market Making strategy.
 *
 * Provides liquidity on binary prediction markets (YES/NO outcomes).
 * Fair value is derived from market mid and historical price trend.
 * Quotes are skewed based on current inventory to manage risk.
 *
 * Binary constraint: YES + NO probabilities should sum to ~1.00.
 */

import type { TickContext, StrategyDecision, Position, Candle } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface PredictionQuote {
  yesBid: number
  yesAsk: number
  noBid: number
  noAsk: number
  fairValue: number
  inventorySkew: number
}

// ── Pure functions ────────────────────────────────────────────────────────────

/**
 * Compute fair value from YES/NO prices and historical candle data.
 * Incorporates trend from candles to adjust the midpoint.
 * Clamps result between 0.01 and 0.99.
 */
export function computeFairValue(yesPrice: number, noPrice: number, candles: Candle[]): number {
  // Start with the raw midpoint (which equals yesPrice in a binary market)
  let fv = yesPrice

  // Incorporate trend from candle data if available
  if (candles.length >= 2) {
    const recentCandles = candles.slice(-5)
    const firstCandle = recentCandles[0]!
    const lastCandle = recentCandles[recentCandles.length - 1]!
    const trendDelta = lastCandle.close - firstCandle.close

    // Apply a dampened trend adjustment (10% weight)
    fv += trendDelta * 0.10
  }

  // Clamp between 0.01 and 0.99
  return Math.max(0.01, Math.min(0.99, fv))
}

/**
 * Compute inventory skew based on current positions.
 * Positive skew when long (encourages selling to reduce position).
 * Negative skew when short (encourages buying to reduce position).
 * Scales linearly with position size relative to maxPosition.
 */
export function computeInventorySkew(
  positions: Position[],
  symbol: string,
  maxPosition: number,
): number {
  const netPosition = positions
    .filter((p) => p.symbol === symbol)
    .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

  if (maxPosition === 0) return 0

  // Skew ranges from -1 to +1 based on position ratio
  return netPosition / maxPosition
}

/**
 * Generate bid/ask quotes for YES and NO tokens.
 * Applies spread around fair value with inventory skew.
 * Ensures binary constraint: YES bid + NO ask <= 1.00.
 * Clamps all prices between minPrice and maxPrice.
 */
export function generateQuotes(
  fairValue: number,
  spreadBps: number,
  skew: number,
  maxPrice: number,
  minPrice: number,
): PredictionQuote {
  const halfSpread = fairValue * spreadBps / 10_000 / 2

  // Apply skew: positive skew shifts quotes down (sell bias)
  const skewShift = halfSpread * skew

  const yesBid = clamp(fairValue - halfSpread - skewShift, minPrice, maxPrice)
  const yesAsk = clamp(fairValue + halfSpread - skewShift, minPrice, maxPrice)

  // NO side is the complement
  const noFairValue = 1 - fairValue
  const noBid = clamp(noFairValue - halfSpread + skewShift, minPrice, maxPrice)
  const noAsk = clamp(noFairValue + halfSpread + skewShift, minPrice, maxPrice)

  return {
    yesBid,
    yesAsk,
    noBid,
    noAsk,
    fairValue,
    inventorySkew: skew,
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

// ── Strategy ──────────────────────────────────────────────────────────────────

export class PredictionMM extends BaseStrategy {
  readonly name = 'prediction-mm'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, candles, config } = ctx

    const spreadBps = typeof config.params['spread_bps'] === 'number' ? config.params['spread_bps'] : 200
    const orderSize = typeof config.params['order_size'] === 'number' ? config.params['order_size'] : 10
    const maxPosition = typeof config.params['max_position'] === 'number' ? config.params['max_position'] : 100
    const minEdge = typeof config.params['min_edge'] === 'number' ? config.params['min_edge'] : 50
    const skewFactor = typeof config.params['skew_factor'] === 'number' ? config.params['skew_factor'] : 0.5

    const symbol = ticker.symbol
    const yesPrice = ticker.mid
    const noPrice = 1 - yesPrice

    const fairValue = computeFairValue(yesPrice, noPrice, candles)
    const rawSkew = computeInventorySkew(positions, symbol, maxPosition)
    const skew = rawSkew * skewFactor

    // Check minimum edge
    const edgeBps = Math.abs(fairValue - yesPrice) * 10_000
    if (edgeBps < minEdge) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: `PredictionMM: edge ${edgeBps.toFixed(0)}bps < min ${minEdge}bps`,
      }]
    }

    const quotes = generateQuotes(fairValue, spreadBps, skew, 0.99, 0.01)

    // Calculate net position
    const netPosition = positions
      .filter((p) => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    // Confidence based on edge size (0-100 scale)
    const confidence = Math.min(100, Math.round(edgeBps / 5))

    const decisions: StrategyDecision[] = []
    const reasonBase = `PredictionMM: fv=${fairValue.toFixed(4)} skew=${skew.toFixed(4)}`

    // BUY side: only if not at max long
    if (netPosition < maxPosition) {
      decisions.push({
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'ALO',
        confidence,
        reason: `${reasonBase} bid=${quotes.yesBid.toFixed(4)}`,
      })
    }

    // SELL side: only if not at max short
    if (netPosition > -maxPosition) {
      decisions.push({
        action: 'SELL',
        symbol,
        size: orderSize,
        orderType: 'ALO',
        confidence,
        reason: `${reasonBase} ask=${quotes.yesAsk.toFixed(4)}`,
      })
    }

    if (decisions.length === 0) {
      decisions.push({
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: `PredictionMM: max position reached on both sides`,
      })
    }

    return decisions
  }
}
