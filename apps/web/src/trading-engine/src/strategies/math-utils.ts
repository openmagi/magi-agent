import type { Candle } from '../types.js'

/**
 * Realized volatility: stddev of log returns from candle close prices.
 * Optionally takes a window parameter to slice the most recent N candles.
 */
export function computeSigma(candles: Candle[], window?: number): number {
  const slice = window !== undefined ? candles.slice(-window) : candles
  if (slice.length < 2) return 0.001

  const logReturns: number[] = []
  for (let i = 1; i < slice.length; i++) {
    const prev = slice[i - 1]?.close
    const curr = slice[i]?.close
    if (prev !== undefined && curr !== undefined && prev > 0 && curr > 0) {
      logReturns.push(Math.log(curr / prev))
    }
  }

  if (logReturns.length === 0) return 0.001

  const mean = logReturns.reduce((s, r) => s + r, 0) / logReturns.length
  const variance = logReturns.reduce((s, r) => s + (r - mean) ** 2, 0) / logReturns.length
  return Math.sqrt(variance)
}

/**
 * Type-safe config parameter extraction.
 * Returns the value from params[key] if it is a number, otherwise returns defaultValue.
 */
export function getParam(params: Record<string, number | string | boolean>, key: string, defaultValue: number): number {
  const val = params[key]
  return typeof val === 'number' ? val : defaultValue
}
