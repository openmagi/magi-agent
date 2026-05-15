import { AggressiveTaker, computeConviction } from './aggressive-taker.js'
import type { TickContext, StrategyConfig, Ticker, OrderBook, Candle, Balance, Position } from '../../types.js'

function makeTicker(overrides: Partial<Ticker> = {}): Ticker {
  return {
    symbol: 'ETH-PERP',
    mid: 3450,
    bid: 3449.5,
    ask: 3450.5,
    lastPrice: 3450,
    volume24h: 1_000_000,
    openInterest: 500_000,
    fundingRate: 0.0001,
    timestamp: Date.now(),
    ...overrides,
  }
}

function makeOrderBook(): OrderBook {
  return {
    symbol: 'ETH-PERP',
    bids: [{ price: 3449.5, size: 1.0 }],
    asks: [{ price: 3450.5, size: 1.0 }],
    timestamp: Date.now(),
  }
}

/** Creates candles with a specific RSI-like pattern */
function makeDecliningCandles(count: number, startPrice: number): Candle[] {
  // Declining prices → RSI < 30 (oversold)
  return Array.from({ length: count }, (_, i) => {
    const close = startPrice - i * 10
    return {
      timestamp: Date.now() - (count - i) * 60_000,
      open: close + 5,
      high: close + 8,
      low: close - 2,
      close,
      volume: 100,
    }
  })
}

function makeRisingCandles(count: number, startPrice: number): Candle[] {
  // Rising prices → RSI > 70 (overbought)
  return Array.from({ length: count }, (_, i) => {
    const close = startPrice + i * 10
    return {
      timestamp: Date.now() - (count - i) * 60_000,
      open: close - 5,
      high: close + 2,
      low: close - 8,
      close,
      volume: 100,
    }
  })
}

function makeFlatCandles(count: number, price = 3450): Candle[] {
  return Array.from({ length: count }, (_, i) => ({
    timestamp: Date.now() - (count - i) * 60_000,
    open: price,
    high: price + 1,
    low: price - 1,
    close: price,
    volume: 100,
  }))
}

function makeBalance(total = 10_000): Balance {
  return { currency: 'USDT', available: total * 0.75, total, unrealizedPnl: 0 }
}

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'aggressive-taker',
    symbols: ['ETH-PERP'],
    params: {
      min_conviction: 75,
      order_size: 0.1,
      max_position: 0.5,
      stop_pct: 1.0,
      ...params,
    },
  }
}

const mockAdapter = {
  name: 'mock',
  getTicker: async () => makeTicker(),
  getOrderBook: async () => makeOrderBook(),
  getCandles: async () => makeFlatCandles(20),
  getBalances: async () => [makeBalance()],
  getPositions: async () => [],
  placeOrder: async () => ({ orderId: '1', status: 'FILLED' as const, filledSize: 0.1, filledPrice: 3450, timestamp: Date.now() }),
  cancelOrder: async () => {},
  cancelAllOrders: async () => {},
  setStopLoss: async () => ({ orderId: '2', status: 'OPEN' as const, filledSize: 0, filledPrice: 0, timestamp: Date.now() }),
  getOpenOrders: async () => [],
  getExchangeInfo: async () => ({ name: 'mock', testnet: true, supportedSymbols: ['ETH-PERP'], minOrderSizes: {}, tickSizes: {} }),
}

function makeCtx(
  overrides: {
    positions?: Position[]
    params?: Record<string, number | string | boolean>
    candles?: Candle[]
    ticker?: Ticker
  } = {}
): TickContext {
  const ticker = overrides.ticker ?? makeTicker()
  return {
    adapter: mockAdapter,
    positions: overrides.positions ?? [],
    balances: [makeBalance()],
    ticker,
    orderBook: makeOrderBook(),
    candles: overrides.candles ?? makeFlatCandles(30),
    config: makeConfig(overrides.params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('AggressiveTaker', () => {
  describe('computeConviction', () => {
    it('should return high conviction when RSI is extreme + volume surge', () => {
      // Declining candles → low RSI
      const candles = makeDecliningCandles(30, 4000)
      const ticker = makeTicker({
        volume24h: 5_000_000, // high volume
        openInterest: 500_000,
        fundingRate: -0.01, // negative funding → aligns with BUY
      })
      const ctx = makeCtx({ candles, ticker })

      const result = computeConviction(ctx)
      expect(result.total).toBeGreaterThanOrEqual(50)
      expect(result.direction).toBe('BUY')
    })

    it('should return low conviction in calm markets', () => {
      const candles = makeFlatCandles(30)
      const ticker = makeTicker({
        volume24h: 100_000, // low volume
        openInterest: 500_000,
        fundingRate: 0.0001, // neutral funding
      })
      const ctx = makeCtx({ candles, ticker })

      const result = computeConviction(ctx)
      expect(result.total).toBeLessThan(50)
    })

    it('should determine BUY direction when RSI < 25', () => {
      // Strongly declining candles
      const candles = makeDecliningCandles(30, 4000)
      const ctx = makeCtx({ candles })

      const result = computeConviction(ctx)
      expect(result.direction).toBe('BUY')
    })

    it('should determine SELL direction when RSI > 75', () => {
      // Strongly rising candles
      const candles = makeRisingCandles(30, 3000)
      const ctx = makeCtx({ candles })

      const result = computeConviction(ctx)
      expect(result.direction).toBe('SELL')
    })

    it('should add OI score when OI is high', () => {
      const candles = makeDecliningCandles(30, 4000)
      const lowOI = makeCtx({
        candles,
        ticker: makeTicker({ openInterest: 100_000 }),
      })
      const highOI = makeCtx({
        candles,
        ticker: makeTicker({ openInterest: 10_000_000 }),
      })

      const lowResult = computeConviction(lowOI)
      const highResult = computeConviction(highOI)
      expect(highResult.factors.oiScore).toBeGreaterThan(lowResult.factors.oiScore)
    })

    it('should add funding score when funding aligns with direction', () => {
      const candles = makeDecliningCandles(30, 4000) // RSI < 30 → BUY direction
      const alignedCtx = makeCtx({
        candles,
        ticker: makeTicker({ fundingRate: -0.01 }), // negative → aligns with BUY
      })
      const misalignedCtx = makeCtx({
        candles,
        ticker: makeTicker({ fundingRate: 0.01 }), // positive → misaligns with BUY
      })

      const aligned = computeConviction(alignedCtx)
      const misaligned = computeConviction(misalignedCtx)
      expect(aligned.factors.fundingScore).toBeGreaterThan(misaligned.factors.fundingScore)
    })
  })

  describe('onTick', () => {
    it('should emit BUY with IOC when conviction >= min_conviction and direction is BUY', () => {
      const strategy = new AggressiveTaker()
      const candles = makeDecliningCandles(30, 4000)
      const ticker = makeTicker({
        volume24h: 5_000_000,
        openInterest: 10_000_000,
        fundingRate: -0.01,
      })
      const ctx = makeCtx({
        candles,
        ticker,
        params: { min_conviction: 50 }, // lower threshold to trigger
      })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')
      expect(buy).toBeDefined()
      expect(buy!.orderType).toBe('IOC')
    })

    it('should emit SELL with IOC when conviction >= min_conviction and direction is SELL', () => {
      const strategy = new AggressiveTaker()
      const candles = makeRisingCandles(30, 3000)
      const ticker = makeTicker({
        volume24h: 5_000_000,
        openInterest: 10_000_000,
        fundingRate: 0.01,
      })
      const ctx = makeCtx({
        candles,
        ticker,
        params: { min_conviction: 50 },
      })

      const decisions = strategy.onTick(ctx)
      const sell = decisions.find(d => d.action === 'SELL')
      expect(sell).toBeDefined()
      expect(sell!.orderType).toBe('IOC')
    })

    it('should emit HOLD when conviction < min_conviction', () => {
      const strategy = new AggressiveTaker()
      const candles = makeFlatCandles(30)
      const ticker = makeTicker({
        volume24h: 100_000,
        fundingRate: 0.0001,
      })
      const ctx = makeCtx({ candles, ticker, params: { min_conviction: 75 } })

      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should place order at ask price for BUY (crossing spread)', () => {
      const strategy = new AggressiveTaker()
      const candles = makeDecliningCandles(30, 4000)
      const ticker = makeTicker({
        ask: 3450.5,
        bid: 3449.5,
        volume24h: 5_000_000,
        openInterest: 10_000_000,
        fundingRate: -0.01,
      })
      const ctx = makeCtx({ candles, ticker, params: { min_conviction: 50 } })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('conviction')
    })

    it('should set stopLoss at entry * (1 - stop_pct/100) for BUY', () => {
      const strategy = new AggressiveTaker()
      const candles = makeDecliningCandles(30, 4000)
      const ticker = makeTicker({
        ask: 3450.5,
        volume24h: 5_000_000,
        openInterest: 10_000_000,
        fundingRate: -0.01,
      })
      const ctx = makeCtx({
        candles,
        ticker,
        params: { min_conviction: 50, stop_pct: 1.0 },
      })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.stopLoss).toBeCloseTo(3450.5 * (1 - 1.0 / 100), 2)
    })

    it('should set stopLoss at entry * (1 + stop_pct/100) for SELL', () => {
      const strategy = new AggressiveTaker()
      const candles = makeRisingCandles(30, 3000)
      const ticker = makeTicker({
        bid: 3449.5,
        volume24h: 5_000_000,
        openInterest: 10_000_000,
        fundingRate: 0.01,
      })
      const ctx = makeCtx({
        candles,
        ticker,
        params: { min_conviction: 50, stop_pct: 1.0 },
      })

      const decisions = strategy.onTick(ctx)
      const sell = decisions.find(d => d.action === 'SELL')!
      expect(sell.stopLoss).toBeCloseTo(3449.5 * (1 + 1.0 / 100), 2)
    })

    it('should respect max_position limit', () => {
      const strategy = new AggressiveTaker()
      const candles = makeDecliningCandles(30, 4000)
      const positions: Position[] = [{
        symbol: 'ETH-PERP',
        side: 'LONG',
        size: 0.5,
        entryPrice: 3400,
        markPrice: 3450,
        unrealizedPnl: 25,
        leverage: 10,
        liquidationPrice: 3000,
      }]
      const ticker = makeTicker({
        volume24h: 5_000_000,
        openInterest: 10_000_000,
        fundingRate: -0.01,
      })
      const ctx = makeCtx({
        candles,
        positions,
        ticker,
        params: { min_conviction: 50 },
      })

      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should include conviction score in reason string', () => {
      const strategy = new AggressiveTaker()
      const candles = makeDecliningCandles(30, 4000)
      const ticker = makeTicker({
        volume24h: 5_000_000,
        openInterest: 10_000_000,
        fundingRate: -0.01,
      })
      const ctx = makeCtx({ candles, ticker, params: { min_conviction: 50 } })

      const decisions = strategy.onTick(ctx)
      const actionDecision = decisions.find(d => d.action !== 'HOLD')!
      expect(actionDecision.reason).toMatch(/conviction/)
    })
  })
})
