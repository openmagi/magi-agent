import { MeanReversion, computeSMA, computeStdDev, computeBollingerBands } from './mean-reversion.js'
import type { TickContext, StrategyConfig, Ticker, OrderBook, Candle, Balance, Position } from '../../types.js'

function makeTicker(mid: number): Ticker {
  return {
    symbol: 'ETH-PERP',
    mid,
    bid: mid - 0.5,
    ask: mid + 0.5,
    lastPrice: mid,
    volume24h: 1_000_000,
    openInterest: 500_000,
    fundingRate: 0.0001,
    timestamp: Date.now(),
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

function makeCandles(count: number, close = 3450): Candle[] {
  return Array.from({ length: count }, (_, i) => ({
    timestamp: Date.now() - (count - i) * 60_000,
    open: close - 5,
    high: close + 10,
    low: close - 10,
    close,
    volume: 100,
  }))
}

/** Creates candles with specific close prices for BB computation */
function makeCandlesWithCloses(closes: number[]): Candle[] {
  return closes.map((close, i) => ({
    timestamp: Date.now() - (closes.length - i) * 60_000,
    open: close - 5,
    high: close + 10,
    low: close - 10,
    close,
    volume: 100,
  }))
}

function makeBalance(total = 10_000): Balance {
  return { currency: 'USDT', available: total * 0.75, total, unrealizedPnl: 0 }
}

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'mean-reversion',
    symbols: ['ETH-PERP'],
    params: {
      sma_period: 20,
      bb_multiplier: 2.0,
      order_size: 0.1,
      max_position: 1.0,
      min_deviation_pct: 0.5,
      ...params,
    },
  }
}

const mockAdapter = {
  name: 'mock',
  getTicker: async () => makeTicker(3450),
  getOrderBook: async () => makeOrderBook(),
  getCandles: async () => makeCandles(20),
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
  const ticker = overrides.ticker ?? makeTicker(3450)
  return {
    adapter: mockAdapter,
    positions: overrides.positions ?? [],
    balances: [makeBalance()],
    ticker,
    orderBook: makeOrderBook(),
    candles: overrides.candles ?? makeCandles(30),
    config: makeConfig(overrides.params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('MeanReversion', () => {
  describe('computeSMA', () => {
    it('should compute simple moving average correctly', () => {
      const values = [10, 20, 30, 40, 50]
      expect(computeSMA(values, 5)).toBe(30) // (10+20+30+40+50)/5
    })

    it('should use last N values when more values than period', () => {
      const values = [5, 10, 20, 30, 40]
      expect(computeSMA(values, 3)).toBeCloseTo(30, 5) // (20+30+40)/3
    })

    it('should return 0 for empty array', () => {
      expect(computeSMA([], 5)).toBe(0)
    })
  })

  describe('computeStdDev', () => {
    it('should compute standard deviation correctly', () => {
      const values = [10, 20, 30, 40, 50]
      const mean = 30
      // variance = ((10-30)^2 + (20-30)^2 + (30-30)^2 + (40-30)^2 + (50-30)^2) / 5
      //          = (400 + 100 + 0 + 100 + 400) / 5 = 200
      // stddev = sqrt(200) = 14.1421...
      expect(computeStdDev(values, mean)).toBeCloseTo(Math.sqrt(200), 5)
    })

    it('should return 0 for single value', () => {
      expect(computeStdDev([42], 42)).toBe(0)
    })
  })

  describe('computeBollingerBands', () => {
    it('should compute SMA, upper, and lower bands', () => {
      // Use uniform close prices for predictable result
      const closes = Array.from({ length: 20 }, () => 100)
      const candles = makeCandlesWithCloses(closes)
      const bands = computeBollingerBands(candles, 20, 2.0)

      expect(bands.sma).toBe(100)
      expect(bands.stddev).toBe(0)
      expect(bands.upper).toBe(100) // sma + 0 = 100
      expect(bands.lower).toBe(100) // sma - 0 = 100
    })

    it('should widen bands with higher multiplier', () => {
      // Mix of prices to get non-zero stddev
      const closes = [100, 102, 98, 101, 99, 103, 97, 100, 102, 98,
                       101, 99, 103, 97, 100, 102, 98, 101, 99, 100]
      const candles = makeCandlesWithCloses(closes)

      const narrow = computeBollingerBands(candles, 20, 1.0)
      const wide = computeBollingerBands(candles, 20, 3.0)

      expect(wide.upper - wide.sma).toBeCloseTo(3 * (narrow.upper - narrow.sma), 5)
      expect(wide.sma - wide.lower).toBeCloseTo(3 * (narrow.sma - narrow.lower), 5)
    })
  })

  describe('onTick', () => {
    it('should emit BUY when price is below lower Bollinger band', () => {
      const strategy = new MeanReversion()
      // Create candles centered around 3450 with some variance
      const closes = Array.from({ length: 25 }, () => 3450)
      closes.push(3450, 3450, 3450, 3450, 3450)
      const candles = makeCandlesWithCloses(closes)

      // Use different close values to get a non-zero stddev
      const mixedCloses = Array.from({ length: 30 }, (_, i) =>
        3450 + (i % 2 === 0 ? 10 : -10)
      )
      const mixedCandles = makeCandlesWithCloses(mixedCloses)
      const bands = computeBollingerBands(mixedCandles, 20, 2.0)

      // Price well below lower band
      const priceBelowBand = bands.lower - 20
      const ctx = makeCtx({ candles: mixedCandles, ticker: makeTicker(priceBelowBand) })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')
      expect(buy).toBeDefined()
      expect(buy!.symbol).toBe('ETH-PERP')
    })

    it('should emit SELL when price is above upper Bollinger band', () => {
      const strategy = new MeanReversion()
      const mixedCloses = Array.from({ length: 30 }, (_, i) =>
        3450 + (i % 2 === 0 ? 10 : -10)
      )
      const mixedCandles = makeCandlesWithCloses(mixedCloses)
      const bands = computeBollingerBands(mixedCandles, 20, 2.0)

      // Price well above upper band
      const priceAboveBand = bands.upper + 20
      const ctx = makeCtx({ candles: mixedCandles, ticker: makeTicker(priceAboveBand) })

      const decisions = strategy.onTick(ctx)
      const sell = decisions.find(d => d.action === 'SELL')
      expect(sell).toBeDefined()
      expect(sell!.symbol).toBe('ETH-PERP')
    })

    it('should emit HOLD when price is within bands', () => {
      const strategy = new MeanReversion()
      // All candles at same price → stddev=0 → bands = sma = price
      const candles = makeCandles(30, 3450)
      const ctx = makeCtx({ candles, ticker: makeTicker(3450) })

      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should set takeProfit at SMA for mean reversion target', () => {
      const strategy = new MeanReversion()
      const mixedCloses = Array.from({ length: 30 }, (_, i) =>
        3450 + (i % 2 === 0 ? 10 : -10)
      )
      const mixedCandles = makeCandlesWithCloses(mixedCloses)
      const bands = computeBollingerBands(mixedCandles, 20, 2.0)

      const priceBelowBand = bands.lower - 20
      const ctx = makeCtx({ candles: mixedCandles, ticker: makeTicker(priceBelowBand) })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.takeProfit).toBeCloseTo(bands.sma, 2)
    })

    it('should set stopLoss beyond the opposite band', () => {
      const strategy = new MeanReversion()
      const mixedCloses = Array.from({ length: 30 }, (_, i) =>
        3450 + (i % 2 === 0 ? 10 : -10)
      )
      const mixedCandles = makeCandlesWithCloses(mixedCloses)
      const bands = computeBollingerBands(mixedCandles, 20, 2.0)

      // BUY signal → stopLoss should be below the lower band (further from entry)
      const priceBelowBand = bands.lower - 20
      const ctx = makeCtx({ candles: mixedCandles, ticker: makeTicker(priceBelowBand) })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')!
      // Stop loss below the lower band by one stddev
      expect(buy.stopLoss).toBeDefined()
      expect(buy.stopLoss!).toBeLessThan(bands.lower)
    })

    it('should scale confidence based on deviation magnitude', () => {
      const strategy = new MeanReversion()
      const mixedCloses = Array.from({ length: 30 }, (_, i) =>
        3450 + (i % 2 === 0 ? 15 : -15)
      )
      const mixedCandles = makeCandlesWithCloses(mixedCloses)
      const bands = computeBollingerBands(mixedCandles, 20, 2.0)

      // Both deviations must exceed min_deviation_pct (0.5%)
      // Use large enough offsets to guarantee exceeding threshold
      const smallDevPrice = bands.lower - 30
      const ctx1 = makeCtx({
        candles: mixedCandles,
        ticker: makeTicker(smallDevPrice),
        params: { min_deviation_pct: 0.1 },
      })
      const d1 = strategy.onTick(ctx1).find(d => d.action === 'BUY')!
      expect(d1).toBeDefined()

      // Large deviation beyond band
      const largeDevPrice = bands.lower - 100
      const ctx2 = makeCtx({
        candles: mixedCandles,
        ticker: makeTicker(largeDevPrice),
        params: { min_deviation_pct: 0.1 },
      })
      const d2 = strategy.onTick(ctx2).find(d => d.action === 'BUY')!
      expect(d2).toBeDefined()

      // Larger deviation should have higher confidence
      expect(d2.confidence).toBeGreaterThan(d1.confidence)
    })

    it('should respect min_deviation_pct threshold', () => {
      const strategy = new MeanReversion()
      const mixedCloses = Array.from({ length: 30 }, (_, i) =>
        3450 + (i % 2 === 0 ? 10 : -10)
      )
      const mixedCandles = makeCandlesWithCloses(mixedCloses)
      const bands = computeBollingerBands(mixedCandles, 20, 2.0)

      // Price just barely below the lower band (within min_deviation_pct)
      const barelyBelow = bands.lower - 0.01
      const ctx = makeCtx({
        candles: mixedCandles,
        ticker: makeTicker(barelyBelow),
        params: { min_deviation_pct: 0.5 },
      })

      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should respect max_position limit', () => {
      const strategy = new MeanReversion()
      const mixedCloses = Array.from({ length: 30 }, (_, i) =>
        3450 + (i % 2 === 0 ? 10 : -10)
      )
      const mixedCandles = makeCandlesWithCloses(mixedCloses)
      const bands = computeBollingerBands(mixedCandles, 20, 2.0)

      const priceBelowBand = bands.lower - 20
      const positions: Position[] = [{
        symbol: 'ETH-PERP',
        side: 'LONG',
        size: 1.0,
        entryPrice: 3400,
        markPrice: 3450,
        unrealizedPnl: 50,
        leverage: 10,
        liquidationPrice: 3000,
      }]
      const ctx = makeCtx({
        candles: mixedCandles,
        positions,
        ticker: makeTicker(priceBelowBand),
      })

      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should use GTC order type for limit entries', () => {
      const strategy = new MeanReversion()
      const mixedCloses = Array.from({ length: 30 }, (_, i) =>
        3450 + (i % 2 === 0 ? 10 : -10)
      )
      const mixedCandles = makeCandlesWithCloses(mixedCloses)
      const bands = computeBollingerBands(mixedCandles, 20, 2.0)

      const priceBelowBand = bands.lower - 20
      const ctx = makeCtx({ candles: mixedCandles, ticker: makeTicker(priceBelowBand) })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.orderType).toBe('GTC')
    })
  })
})
