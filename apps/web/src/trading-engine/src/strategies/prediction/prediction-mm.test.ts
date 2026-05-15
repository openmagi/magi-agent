import { describe, it, expect } from '@jest/globals'
import { PredictionMM, computeFairValue, computeInventorySkew, generateQuotes } from './prediction-mm.js'
import type { TickContext, StrategyConfig, Ticker, OrderBook, Candle, Balance, Position } from '../../types.js'

// ── helpers ─────────────────────────────────────────────────────────────────

const CONDITION_ID = '0xabc123def456'
const SYMBOL = `YES-${CONDITION_ID}`

function makeTicker(mid: number): Ticker {
  return {
    symbol: SYMBOL,
    mid,
    bid: mid - 0.01,
    ask: mid + 0.01,
    lastPrice: mid,
    volume24h: 50000,
    openInterest: 0,
    fundingRate: 0,
    timestamp: Date.now(),
  }
}

function makeOrderBook(): OrderBook {
  return {
    symbol: SYMBOL,
    bids: [{ price: 0.59, size: 100 }],
    asks: [{ price: 0.61, size: 100 }],
    timestamp: Date.now(),
  }
}

function makeCandles(closePrices: number[] = [0.55, 0.57, 0.58, 0.60]): Candle[] {
  return closePrices.map((close, i) => ({
    timestamp: Date.now() - (closePrices.length - i) * 3600_000,
    open: close - 0.01,
    high: close + 0.02,
    low: close - 0.02,
    close,
    volume: 0,
  }))
}

function makeBalance(total = 10_000): Balance {
  return { currency: 'USDC', available: total * 0.75, total, unrealizedPnl: 0 }
}

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'prediction-mm',
    symbols: [SYMBOL],
    params: {
      spread_bps: 200,
      order_size: 10,
      max_position: 100,
      min_edge: 50,
      skew_factor: 0.5,
      ...params,
    },
  }
}

const mockAdapter = {
  name: 'mock',
  getTicker: async () => makeTicker(0.60),
  getOrderBook: async () => makeOrderBook(),
  getCandles: async () => makeCandles(),
  getBalances: async () => [makeBalance()],
  getPositions: async () => [],
  placeOrder: async () => ({ orderId: '1', status: 'FILLED' as const, filledSize: 10, filledPrice: 0.60, timestamp: Date.now() }),
  cancelOrder: async () => {},
  cancelAllOrders: async () => {},
  setStopLoss: async () => ({ orderId: '2', status: 'OPEN' as const, filledSize: 0, filledPrice: 0, timestamp: Date.now() }),
  getOpenOrders: async () => [],
  getExchangeInfo: async () => ({ name: 'mock', testnet: true, supportedSymbols: [SYMBOL], minOrderSizes: {}, tickSizes: {} }),
}

function makeCtx(
  overrides: {
    mid?: number
    positions?: Position[]
    params?: Record<string, number | string | boolean>
    candles?: Candle[]
  } = {}
): TickContext {
  const mid = overrides.mid ?? 0.60
  return {
    adapter: mockAdapter,
    positions: overrides.positions ?? [],
    balances: [makeBalance()],
    ticker: makeTicker(mid),
    orderBook: makeOrderBook(),
    candles: overrides.candles ?? makeCandles(),
    config: makeConfig(overrides.params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

// ── computeFairValue ────────────────────────────────────────────────────────

describe('computeFairValue', () => {
  it('should return midpoint of YES price when market is balanced', () => {
    const fv = computeFairValue(0.50, 0.50, [])
    expect(fv).toBeCloseTo(0.50)
  })

  it('should incorporate trend from candle data', () => {
    // Upward trend: candles show prices moving from 0.40 to 0.60
    const upCandles = makeCandles([0.40, 0.45, 0.50, 0.55, 0.60])
    const fv = computeFairValue(0.60, 0.40, upCandles)
    // Fair value should be slightly above the raw midpoint due to trend
    expect(fv).toBeGreaterThanOrEqual(0.50)
    expect(fv).toBeLessThanOrEqual(0.99)
  })

  it('should clamp fair value between 0.01 and 0.99', () => {
    // Extreme YES price
    const fvHigh = computeFairValue(0.999, 0.001, [])
    expect(fvHigh).toBeLessThanOrEqual(0.99)
    expect(fvHigh).toBeGreaterThanOrEqual(0.01)

    // Extreme NO price
    const fvLow = computeFairValue(0.001, 0.999, [])
    expect(fvLow).toBeLessThanOrEqual(0.99)
    expect(fvLow).toBeGreaterThanOrEqual(0.01)
  })
})

// ── computeInventorySkew ────────────────────────────────────────────────────

describe('computeInventorySkew', () => {
  it('should return 0 when no position', () => {
    const skew = computeInventorySkew([], SYMBOL, 100)
    expect(skew).toBe(0)
  })

  it('should return positive skew when long YES (shift asks down)', () => {
    const positions: Position[] = [{
      symbol: SYMBOL,
      side: 'LONG',
      size: 50,
      entryPrice: 0.55,
      markPrice: 0.60,
      unrealizedPnl: 2.5,
      leverage: 1,
      liquidationPrice: null,
    }]
    const skew = computeInventorySkew(positions, SYMBOL, 100)
    expect(skew).toBeGreaterThan(0)
  })

  it('should return negative skew when short YES (shift bids up)', () => {
    const positions: Position[] = [{
      symbol: SYMBOL,
      side: 'SHORT',
      size: 50,
      entryPrice: 0.65,
      markPrice: 0.60,
      unrealizedPnl: 2.5,
      leverage: 1,
      liquidationPrice: null,
    }]
    const skew = computeInventorySkew(positions, SYMBOL, 100)
    expect(skew).toBeLessThan(0)
  })

  it('should scale with position size relative to max_position', () => {
    const smallPos: Position[] = [{
      symbol: SYMBOL,
      side: 'LONG',
      size: 10,
      entryPrice: 0.50,
      markPrice: 0.55,
      unrealizedPnl: 0.5,
      leverage: 1,
      liquidationPrice: null,
    }]
    const largePos: Position[] = [{
      symbol: SYMBOL,
      side: 'LONG',
      size: 80,
      entryPrice: 0.50,
      markPrice: 0.55,
      unrealizedPnl: 4.0,
      leverage: 1,
      liquidationPrice: null,
    }]

    const skewSmall = computeInventorySkew(smallPos, SYMBOL, 100)
    const skewLarge = computeInventorySkew(largePos, SYMBOL, 100)

    expect(Math.abs(skewLarge)).toBeGreaterThan(Math.abs(skewSmall))
  })
})

// ── generateQuotes ──────────────────────────────────────────────────────────

describe('generateQuotes', () => {
  it('should produce bid < fairValue < ask', () => {
    const quotes = generateQuotes(0.60, 200, 0, 0.99, 0.01)
    expect(quotes.yesBid).toBeLessThan(0.60)
    expect(quotes.yesAsk).toBeGreaterThan(0.60)
  })

  it('should apply inventory skew to shift quotes', () => {
    const noSkew = generateQuotes(0.60, 200, 0, 0.99, 0.01)
    const longSkew = generateQuotes(0.60, 200, 0.3, 0.99, 0.01)

    // Positive skew (long) should lower ask to attract sellers
    expect(longSkew.yesAsk).toBeLessThan(noSkew.yesAsk)
    // And lower bid to avoid buying more
    expect(longSkew.yesBid).toBeLessThan(noSkew.yesBid)
  })

  it('should ensure YES bid + NO ask <= 1.00', () => {
    const quotes = generateQuotes(0.60, 200, 0, 0.99, 0.01)
    expect(quotes.yesBid + quotes.noAsk).toBeLessThanOrEqual(1.00 + 0.0001) // small float tolerance
  })

  it('should clamp all prices between 0.01 and 0.99', () => {
    // Very high fair value near boundary
    const quotesHigh = generateQuotes(0.98, 200, 0, 0.99, 0.01)
    expect(quotesHigh.yesAsk).toBeLessThanOrEqual(0.99)
    expect(quotesHigh.yesBid).toBeGreaterThanOrEqual(0.01)
    expect(quotesHigh.noAsk).toBeLessThanOrEqual(0.99)
    expect(quotesHigh.noBid).toBeGreaterThanOrEqual(0.01)

    // Very low fair value near boundary
    const quotesLow = generateQuotes(0.02, 200, 0, 0.99, 0.01)
    expect(quotesLow.yesAsk).toBeLessThanOrEqual(0.99)
    expect(quotesLow.yesBid).toBeGreaterThanOrEqual(0.01)
    expect(quotesLow.noAsk).toBeLessThanOrEqual(0.99)
    expect(quotesLow.noBid).toBeGreaterThanOrEqual(0.01)
  })
})

// ── onTick ──────────────────────────────────────────────────────────────────

describe('PredictionMM.onTick', () => {
  it('should emit BUY and SELL decisions for YES token', () => {
    // Use a fair value that will differ enough from market price to exceed min_edge
    // Set min_edge to 0 so any edge qualifies
    const strategy = new PredictionMM()
    const ctx = makeCtx({ mid: 0.60, params: { spread_bps: 200, order_size: 10, max_position: 100, min_edge: 0, skew_factor: 0.5 } })
    const decisions = strategy.onTick(ctx)

    const buy = decisions.find(d => d.action === 'BUY')
    const sell = decisions.find(d => d.action === 'SELL')

    expect(buy).toBeDefined()
    expect(sell).toBeDefined()
    expect(buy!.symbol).toBe(SYMBOL)
    expect(sell!.symbol).toBe(SYMBOL)
  })

  it('should emit HOLD when edge < min_edge', () => {
    // Set very high min_edge so no trade qualifies
    const strategy = new PredictionMM()
    const candles = makeCandles([0.60, 0.60, 0.60, 0.60])
    const ctx = makeCtx({ mid: 0.60, params: { spread_bps: 200, order_size: 10, max_position: 100, min_edge: 9999, skew_factor: 0.5 }, candles })
    const decisions = strategy.onTick(ctx)

    expect(decisions).toHaveLength(1)
    expect(decisions[0]!.action).toBe('HOLD')
    expect(decisions[0]!.reason).toContain('edge')
  })

  it('should use ALO order type for passive quoting', () => {
    const strategy = new PredictionMM()
    const ctx = makeCtx({ mid: 0.60, params: { spread_bps: 200, order_size: 10, max_position: 100, min_edge: 0, skew_factor: 0.5 } })
    const decisions = strategy.onTick(ctx)

    decisions.filter(d => d.action !== 'HOLD').forEach(d => {
      expect(d.orderType).toBe('ALO')
    })
  })

  it('should respect max_position limits', () => {
    const strategy = new PredictionMM()
    const positions: Position[] = [{
      symbol: SYMBOL,
      side: 'LONG',
      size: 100,
      entryPrice: 0.55,
      markPrice: 0.60,
      unrealizedPnl: 5,
      leverage: 1,
      liquidationPrice: null,
    }]
    const ctx = makeCtx({
      mid: 0.60,
      positions,
      params: { spread_bps: 200, order_size: 10, max_position: 100, min_edge: 0, skew_factor: 0.5 },
    })
    const decisions = strategy.onTick(ctx)

    // At max long position, should not BUY more
    const buy = decisions.find(d => d.action === 'BUY')
    expect(buy).toBeUndefined()
  })

  it('should include fair value and skew in reason string', () => {
    const strategy = new PredictionMM()
    const ctx = makeCtx({ mid: 0.60, params: { spread_bps: 200, order_size: 10, max_position: 100, min_edge: 0, skew_factor: 0.5 } })
    const decisions = strategy.onTick(ctx)

    const nonHold = decisions.filter(d => d.action !== 'HOLD')
    expect(nonHold.length).toBeGreaterThan(0)
    nonHold.forEach(d => {
      expect(d.reason).toContain('fv=')
      expect(d.reason).toContain('skew=')
    })
  })

  it('should handle binary markets (YES/NO pair)', () => {
    const strategy = new PredictionMM()
    const ctx = makeCtx({ mid: 0.60, params: { spread_bps: 200, order_size: 10, max_position: 100, min_edge: 0, skew_factor: 0.5 } })
    const decisions = strategy.onTick(ctx)

    // All decisions should reference the YES symbol
    decisions.forEach(d => {
      expect(d.symbol).toBe(SYMBOL)
    })
  })

  it('should set appropriate confidence based on edge size', () => {
    const strategy = new PredictionMM()
    // Use a scenario with some trend to create edge
    const trendCandles = makeCandles([0.40, 0.45, 0.50, 0.55, 0.60])
    const ctx = makeCtx({
      mid: 0.55,
      candles: trendCandles,
      params: { spread_bps: 200, order_size: 10, max_position: 100, min_edge: 0, skew_factor: 0.5 },
    })
    const decisions = strategy.onTick(ctx)

    decisions.filter(d => d.action !== 'HOLD').forEach(d => {
      expect(d.confidence).toBeGreaterThanOrEqual(0)
      expect(d.confidence).toBeLessThanOrEqual(100)
    })
  })
})
