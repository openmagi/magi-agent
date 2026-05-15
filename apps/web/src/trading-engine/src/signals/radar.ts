import type { RadarScore, Ticker, Candle } from '../types.js'

/**
 * Calculate RSI (Relative Strength Index) from candle data.
 * Standard formula: 100 - (100 / (1 + avgGain / avgLoss))
 */
export function calculateRSI(candles: Candle[], period: number = 14): number {
  if (candles.length < period + 1) {
    return 50 // Not enough data, return neutral
  }

  const changes: number[] = []
  for (let i = 1; i < candles.length; i++) {
    changes.push(candles[i]!.close - candles[i - 1]!.close)
  }

  // Initial average gain/loss over first `period` changes
  let avgGain = 0
  let avgLoss = 0
  for (let i = 0; i < period; i++) {
    const change = changes[i]!
    if (change > 0) {
      avgGain += change
    } else {
      avgLoss += Math.abs(change)
    }
  }
  avgGain /= period
  avgLoss /= period

  // Smoothed RSI using Wilder's smoothing for remaining changes
  for (let i = period; i < changes.length; i++) {
    const change = changes[i]!
    const gain = change > 0 ? change : 0
    const loss = change < 0 ? Math.abs(change) : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
  }

  if (avgLoss === 0) {
    return avgGain === 0 ? 50 : 100
  }

  const rs = avgGain / avgLoss
  return 100 - 100 / (1 + rs)
}

/**
 * Calculate EMA (Exponential Moving Average).
 * EMA = price * k + prevEMA * (1 - k), where k = 2 / (period + 1)
 */
export function calculateEMA(values: number[], period: number): number {
  if (values.length === 0) {
    return 0
  }

  const k = 2 / (period + 1)

  // Start EMA with first value
  let ema = values[0]!
  for (let i = 1; i < values.length; i++) {
    ema = values[i]! * k + ema * (1 - k)
  }

  return ema
}

type Direction = 'LONG' | 'SHORT'

export class Radar {
  constructor() {}

  scoreSymbol(
    ticker: Ticker,
    candles: Candle[],
    btcTrend: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
  ): RadarScore {
    const rsi = calculateRSI(candles)
    const closes = candles.map((c) => c.close)
    const ema12 = calculateEMA(closes, 12)
    const ema26 = calculateEMA(closes, 26)

    // Determine direction
    const direction = this.determineDirection(rsi, ema12, ema26)

    const marketStructure = this.scoreMarketStructure(ticker)
    const technicals = this.scoreTechnicals(rsi, ema12, ema26, candles, direction)
    const funding = this.scoreFunding(ticker.fundingRate, direction)
    const btcMacro = this.scoreBtcMacro(btcTrend, direction)
    const total = marketStructure + technicals + funding + btcMacro

    return {
      symbol: ticker.symbol,
      total,
      marketStructure,
      technicals,
      funding,
      btcMacro,
      direction,
      timestamp: Date.now(),
    }
  }

  scan(
    tickers: Ticker[],
    candlesMap: Map<string, Candle[]>,
    btcTrend: 'BULLISH' | 'BEARISH' | 'NEUTRAL',
    topN: number = 10
  ): RadarScore[] {
    const scores: RadarScore[] = []

    for (const ticker of tickers) {
      const candles = candlesMap.get(ticker.symbol)
      if (!candles || candles.length === 0) {
        continue
      }
      scores.push(this.scoreSymbol(ticker, candles, btcTrend))
    }

    scores.sort((a, b) => b.total - a.total)
    return scores.slice(0, topN)
  }

  private determineDirection(rsi: number, ema12: number, ema26: number): Direction {
    if (rsi < 40) return 'LONG'
    if (rsi > 60) return 'SHORT'
    // Tie-break: EMA crossover
    return ema12 > ema26 ? 'LONG' : 'SHORT'
  }

  private scoreMarketStructure(ticker: Ticker): number {
    // Volume score (0-50)
    let volumeScore: number
    if (ticker.volume24h > 1e8) volumeScore = 50
    else if (ticker.volume24h > 5e7) volumeScore = 40
    else if (ticker.volume24h > 1e7) volumeScore = 30
    else if (ticker.volume24h > 1e6) volumeScore = 15
    else volumeScore = 0

    // OI score (0-50)
    let oiScore: number
    if (ticker.openInterest > 1e8) oiScore = 50
    else if (ticker.openInterest > 5e7) oiScore = 40
    else if (ticker.openInterest > 1e7) oiScore = 30
    else if (ticker.openInterest > 1e6) oiScore = 15
    else oiScore = 0

    // Spread score (0-40)
    const spreadPct = ((ticker.ask - ticker.bid) / ticker.mid) * 100
    let spreadScore: number
    if (spreadPct < 0.01) spreadScore = 40
    else if (spreadPct < 0.05) spreadScore = 30
    else if (spreadPct < 0.1) spreadScore = 20
    else if (spreadPct < 0.5) spreadScore = 10
    else spreadScore = 0

    return volumeScore + oiScore + spreadScore
  }

  private scoreTechnicals(
    rsi: number,
    ema12: number,
    ema26: number,
    candles: Candle[],
    direction: Direction
  ): number {
    // RSI score (0-40)
    let rsiScore: number
    if (rsi < 30) rsiScore = 40
    else if (rsi < 35) rsiScore = 30
    else if (rsi > 70) rsiScore = 40
    else if (rsi > 65) rsiScore = 30
    else rsiScore = 10

    // EMA crossover score (0-40)
    let emaScore: number
    if (ema12 > ema26) {
      // Bullish crossover
      emaScore = direction === 'LONG' ? 40 : 10
    } else {
      // Bearish crossover
      emaScore = direction === 'SHORT' ? 40 : 10
    }

    // Hourly trend score (0-40)
    let trendScore: number = 15 // default mixed
    if (candles.length >= 4) {
      const last4 = candles.slice(-4)
      const allUp = last4.every((c) => c.close >= c.open)
      const allDown = last4.every((c) => c.close <= c.open)
      if (allUp) trendScore = 40
      else if (allDown) trendScore = 40
    }

    return rsiScore + emaScore + trendScore
  }

  private scoreFunding(fundingRate: number, direction: Direction): number {
    // Rate magnitude (0-40)
    const absFunding = Math.abs(fundingRate)
    let magnitudeScore: number
    if (absFunding > 0.01) magnitudeScore = 40
    else if (absFunding > 0.005) magnitudeScore = 30
    else if (absFunding > 0.001) magnitudeScore = 20
    else magnitudeScore = 5

    // Direction bias (0-40)
    let biasScore: number
    if (fundingRate < 0 && direction === 'LONG') {
      // Negative funding + LONG → shorts paying longs
      biasScore = 40
    } else if (fundingRate > 0 && direction === 'SHORT') {
      // Positive funding + SHORT → longs paying shorts
      biasScore = 40
    } else {
      biasScore = 10
    }

    return magnitudeScore + biasScore
  }

  private scoreBtcMacro(btcTrend: 'BULLISH' | 'BEARISH' | 'NEUTRAL', direction: Direction): number {
    if (btcTrend === 'NEUTRAL') return 30
    if (btcTrend === 'BULLISH' && direction === 'LONG') return 60
    if (btcTrend === 'BEARISH' && direction === 'SHORT') return 60
    // Misaligned
    return 10
  }
}
