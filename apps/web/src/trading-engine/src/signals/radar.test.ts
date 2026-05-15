import { Radar, calculateRSI, calculateEMA } from './radar.js'
import type { RadarScore, Ticker, Candle } from '../types.js'

function makeTicker(overrides: Partial<Ticker> = {}): Ticker {
  return {
    symbol: 'BTC-PERP',
    mid: 100_000,
    bid: 99_995,
    ask: 100_005,
    lastPrice: 100_000,
    volume24h: 5e7,
    openInterest: 5e7,
    fundingRate: 0.0001,
    timestamp: Date.now(),
    ...overrides,
  }
}

function makeCandle(close: number, open?: number, volume?: number): Candle {
  const o = open ?? close
  return {
    timestamp: Date.now(),
    open: o,
    high: Math.max(o, close) * 1.001,
    low: Math.min(o, close) * 0.999,
    close,
    volume: volume ?? 1000,
  }
}

function makeCandles(closes: number[]): Candle[] {
  return closes.map((c, i) => ({
    timestamp: Date.now() - (closes.length - i) * 3600_000,
    open: c,
    high: c * 1.001,
    low: c * 0.999,
    close: c,
    volume: 1000,
  }))
}

function makeRisingCandles(count: number, start: number = 100): Candle[] {
  const closes: number[] = []
  for (let i = 0; i < count; i++) {
    closes.push(start + i * 2)
  }
  return makeCandles(closes)
}

function makeFallingCandles(count: number, start: number = 200): Candle[] {
  const closes: number[] = []
  for (let i = 0; i < count; i++) {
    closes.push(start - i * 2)
  }
  return makeCandles(closes)
}

function makeFlatCandles(count: number, price: number = 100): Candle[] {
  return makeCandles(Array(count).fill(price))
}

describe('calculateRSI', () => {
  it('should return ~50 for flat prices', () => {
    const candles = makeFlatCandles(20, 100)
    const rsi = calculateRSI(candles)
    // Flat prices have no gains or losses, RSI should be around 50
    expect(rsi).toBeGreaterThanOrEqual(45)
    expect(rsi).toBeLessThanOrEqual(55)
  })

  it('should return <30 for consistently falling prices', () => {
    const candles = makeFallingCandles(20)
    const rsi = calculateRSI(candles)
    expect(rsi).toBeLessThan(30)
  })

  it('should return >70 for consistently rising prices', () => {
    const candles = makeRisingCandles(20)
    const rsi = calculateRSI(candles)
    expect(rsi).toBeGreaterThan(70)
  })
})

describe('calculateEMA', () => {
  it('should weight recent values more', () => {
    // A sequence ending with higher values should produce higher EMA
    const valuesLow = [10, 10, 10, 10, 10]
    const valuesHigh = [10, 10, 10, 10, 20]

    const emaLow = calculateEMA(valuesLow, 3)
    const emaHigh = calculateEMA(valuesHigh, 3)

    expect(emaHigh).toBeGreaterThan(emaLow)
  })
})

describe('Radar', () => {
  const radar = new Radar()

  describe('scoreSymbol — Market Structure', () => {
    it('should score 0-140 for market structure', () => {
      const ticker = makeTicker({
        volume24h: 2e8,
        openInterest: 2e8,
        bid: 99_999,
        ask: 100_001,
      })
      const candles = makeRisingCandles(30)
      const result = radar.scoreSymbol(ticker, candles, 'BULLISH')

      expect(result.marketStructure).toBeGreaterThanOrEqual(0)
      expect(result.marketStructure).toBeLessThanOrEqual(140)
    })

    it('should give max market structure (140) for high vol, high OI, tight spread', () => {
      const ticker = makeTicker({
        volume24h: 2e8,
        openInterest: 2e8,
        mid: 100_000,
        bid: 99_999,
        ask: 100_001,
        // spreadPct = (100001-99999)/100000 * 100 = 0.002% < 0.01% → 40
      })
      const candles = makeRisingCandles(30)
      const result = radar.scoreSymbol(ticker, candles, 'NEUTRAL')
      // volume > 1e8 → 50, OI > 1e8 → 50, spread < 0.01% → 40
      expect(result.marketStructure).toBe(140)
    })

    it('should give 0 market structure for low vol, low OI, wide spread', () => {
      const ticker = makeTicker({
        volume24h: 100,
        openInterest: 100,
        mid: 100,
        bid: 90,
        ask: 110,
        // spreadPct = (110-90)/100 * 100 = 20% → 0
      })
      const candles = makeFlatCandles(30, 100)
      const result = radar.scoreSymbol(ticker, candles, 'NEUTRAL')
      expect(result.marketStructure).toBe(0)
    })
  })

  describe('scoreSymbol — Technicals', () => {
    it('should score 0-120 for technicals', () => {
      const ticker = makeTicker()
      const candles = makeRisingCandles(30)
      const result = radar.scoreSymbol(ticker, candles, 'NEUTRAL')

      expect(result.technicals).toBeGreaterThanOrEqual(0)
      expect(result.technicals).toBeLessThanOrEqual(120)
    })
  })

  describe('scoreSymbol — Funding', () => {
    it('should score 0-80 for funding', () => {
      const ticker = makeTicker({ fundingRate: 0.02 })
      const candles = makeRisingCandles(30)
      const result = radar.scoreSymbol(ticker, candles, 'NEUTRAL')

      expect(result.funding).toBeGreaterThanOrEqual(0)
      expect(result.funding).toBeLessThanOrEqual(80)
    })
  })

  describe('scoreSymbol — BTC Macro', () => {
    it('should score 0-60 for BTC macro', () => {
      const ticker = makeTicker()
      const candles = makeRisingCandles(30)
      const result = radar.scoreSymbol(ticker, candles, 'BULLISH')

      expect(result.btcMacro).toBeGreaterThanOrEqual(0)
      expect(result.btcMacro).toBeLessThanOrEqual(60)
    })

    it('should give 60 when BTC bullish and direction is LONG', () => {
      // Rising candles → RSI > 60? No, RSI > 70 for rising → SHORT
      // We need RSI < 40 → LONG direction. Use falling candles to get oversold,
      // but then ema12 < ema26 → SHORT... We need RSI < 40 as primary.
      // Actually: RSI < 40 → LONG. Let's use candles that produce RSI ~35 (slightly oversold)
      // and then BTC BULLISH → alignment → 60
      const candles = makeFallingCandles(30)
      const ticker = makeTicker()
      const result = radar.scoreSymbol(ticker, candles, 'BULLISH')
      // Falling candles → RSI < 40 → direction LONG
      // BTC BULLISH + LONG → 60
      expect(result.direction).toBe('LONG')
      expect(result.btcMacro).toBe(60)
    })

    it('should give 30 when BTC is NEUTRAL', () => {
      const ticker = makeTicker()
      const candles = makeRisingCandles(30)
      const result = radar.scoreSymbol(ticker, candles, 'NEUTRAL')
      expect(result.btcMacro).toBe(30)
    })

    it('should give 10 when misaligned (BTC bearish but direction LONG)', () => {
      const candles = makeFallingCandles(30)
      const ticker = makeTicker()
      const result = radar.scoreSymbol(ticker, candles, 'BEARISH')
      // Falling → RSI < 40 → LONG direction
      // BTC BEARISH + LONG → misaligned → 10
      expect(result.direction).toBe('LONG')
      expect(result.btcMacro).toBe(10)
    })
  })

  describe('scoreSymbol — Total', () => {
    it('total should be 0-400', () => {
      const ticker = makeTicker()
      const candles = makeRisingCandles(30)
      const result = radar.scoreSymbol(ticker, candles, 'BULLISH')

      expect(result.total).toBeGreaterThanOrEqual(0)
      expect(result.total).toBeLessThanOrEqual(400)
      expect(result.total).toBe(
        result.marketStructure + result.technicals + result.funding + result.btcMacro
      )
    })
  })

  describe('scoreSymbol — Direction', () => {
    it('should determine LONG direction for oversold RSI', () => {
      const candles = makeFallingCandles(30)
      const ticker = makeTicker()
      const result = radar.scoreSymbol(ticker, candles, 'NEUTRAL')
      // Falling → RSI < 40 → LONG
      expect(result.direction).toBe('LONG')
    })

    it('should determine SHORT direction for overbought RSI', () => {
      const candles = makeRisingCandles(30)
      const ticker = makeTicker()
      const result = radar.scoreSymbol(ticker, candles, 'NEUTRAL')
      // Rising → RSI > 60 → SHORT
      expect(result.direction).toBe('SHORT')
    })
  })

  describe('scan', () => {
    it('should sort by total score descending', () => {
      const tickers = [
        makeTicker({ symbol: 'LOW-PERP', volume24h: 100, openInterest: 100 }),
        makeTicker({ symbol: 'HIGH-PERP', volume24h: 2e8, openInterest: 2e8 }),
        makeTicker({ symbol: 'MID-PERP', volume24h: 1e7, openInterest: 1e7 }),
      ]
      const candlesMap = new Map<string, Candle[]>()
      candlesMap.set('LOW-PERP', makeRisingCandles(30))
      candlesMap.set('HIGH-PERP', makeRisingCandles(30))
      candlesMap.set('MID-PERP', makeRisingCandles(30))

      const results = radar.scan(tickers, candlesMap, 'NEUTRAL')

      // Should be sorted descending by total
      for (let i = 0; i < results.length - 1; i++) {
        expect(results[i]!.total).toBeGreaterThanOrEqual(results[i + 1]!.total)
      }
    })

    it('should return top N results', () => {
      const tickers = [
        makeTicker({ symbol: 'A-PERP', volume24h: 2e8 }),
        makeTicker({ symbol: 'B-PERP', volume24h: 1e8 }),
        makeTicker({ symbol: 'C-PERP', volume24h: 5e7 }),
        makeTicker({ symbol: 'D-PERP', volume24h: 1e7 }),
        makeTicker({ symbol: 'E-PERP', volume24h: 1e6 }),
      ]
      const candlesMap = new Map<string, Candle[]>()
      for (const t of tickers) {
        candlesMap.set(t.symbol, makeRisingCandles(30))
      }

      const results = radar.scan(tickers, candlesMap, 'NEUTRAL', 3)
      expect(results).toHaveLength(3)
    })

    it('should default to top 10 results', () => {
      const tickers: Ticker[] = []
      const candlesMap = new Map<string, Candle[]>()
      for (let i = 0; i < 15; i++) {
        const sym = `SYM${i}-PERP`
        tickers.push(makeTicker({ symbol: sym, volume24h: (15 - i) * 1e7 }))
        candlesMap.set(sym, makeRisingCandles(30))
      }

      const results = radar.scan(tickers, candlesMap, 'NEUTRAL')
      expect(results).toHaveLength(10)
    })
  })
})
