import { LiquidationMM } from './liquidation-mm.js'
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

function makeCandles(closePrices?: number[]): Candle[] {
  const prices = closePrices ?? Array.from({ length: 20 }, () => 3450)
  return prices.map((close, i) => ({
    timestamp: Date.now() - (prices.length - i) * 60_000,
    open: close * 0.999,
    high: close * 1.002,
    low: close * 0.998,
    close,
    volume: 100,
  }))
}

function makeBalance(total = 10_000): Balance {
  return { currency: 'USDT', available: total * 0.75, total, unrealizedPnl: 0 }
}

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'liquidation-mm',
    symbols: ['ETH-PERP'],
    params: {
      liq_distance_pct: 5,
      order_size: 0.1,
      max_position: 1.0,
      funding_threshold: 0.0001,
      oi_surge_threshold: 5,
      spread_bps: 10,
      ...params,
    },
  }
}

const mockAdapter = {
  name: 'mock',
  getTicker: async () => makeTicker(),
  getOrderBook: async () => makeOrderBook(),
  getCandles: async () => makeCandles(),
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
    ticker?: Ticker
    candles?: Candle[]
  } = {},
): TickContext {
  return {
    adapter: mockAdapter,
    positions: overrides.positions ?? [],
    balances: [makeBalance()],
    ticker: overrides.ticker ?? makeTicker(),
    orderBook: makeOrderBook(),
    candles: overrides.candles ?? makeCandles(),
    config: makeConfig(overrides.params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('LiquidationMM', () => {
  describe('liquidation zone estimation', () => {
    it('should estimate liquidation zone below mid when funding is positive', () => {
      // Positive funding = longs pay shorts = long squeeze risk below
      const strategy = new LiquidationMM()
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy).toBeDefined()
      // Bid should be placed below mid, near liquidation zone
      // liqZone = 3450 * (1 - 5/100) = 3277.5
      expect(buy.stopLoss!).toBeLessThan(3450)
      expect(buy.stopLoss!).toBeGreaterThan(3200)
    })

    it('should estimate liquidation zone above mid when funding is negative', () => {
      // Negative funding = shorts pay longs = short squeeze risk above
      const strategy = new LiquidationMM()
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: -0.001 }),
      }))

      const sell = decisions.find(d => d.action === 'SELL')!
      expect(sell).toBeDefined()
      // Ask should be placed above mid, near liquidation zone
      // liqZone = 3450 * (1 + 5/100) = 3622.5
      expect(sell.stopLoss!).toBeGreaterThan(3450)
      expect(sell.stopLoss!).toBeLessThan(3700)
    })

    it('should use liq_distance_pct to compute zone distance', () => {
      const strategy = new LiquidationMM()
      // Different liq_distance_pct should produce different zone distances
      const narrow = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
        params: { liq_distance_pct: 2 },
      }))
      const wide = new LiquidationMM().onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
        params: { liq_distance_pct: 10 },
      }))

      const narrowBid = narrow.find(d => d.action === 'BUY')!.stopLoss!
      const wideBid = wide.find(d => d.action === 'BUY')!.stopLoss!

      // Wider distance -> bid further from mid
      expect(wideBid).toBeLessThan(narrowBid)
    })
  })

  describe('OI-based size scaling', () => {
    it('should increase size when OI is dropping (liquidation cascade)', () => {
      const strategy = new LiquidationMM()
      // First tick sets prevOI
      strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001, openInterest: 500_000 }),
      }))
      // Second tick with significant OI drop
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001, openInterest: 450_000 }), // 10% drop
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      // Size should be scaled up due to OI drop
      expect(buy.size).toBeGreaterThan(0.1)
    })

    it('should use base size when OI is stable', () => {
      const strategy = new LiquidationMM()
      // First tick sets prevOI
      strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001, openInterest: 500_000 }),
      }))
      // Second tick with stable OI
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001, openInterest: 498_000 }), // <1% change
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.size).toBeCloseTo(0.1, 1)
    })
  })

  describe('volatility buffer', () => {
    it('should widen buffer from liquidation zone in high volatility', () => {
      const strategy1 = new LiquidationMM()
      const strategy2 = new LiquidationMM()

      const quietCandles = makeCandles(Array.from({ length: 20 }, () => 3450))
      const volatileCandles = makeCandles([
        3450, 3500, 3400, 3500, 3400, 3500, 3400, 3500, 3400, 3500,
        3450, 3500, 3400, 3500, 3400, 3500, 3400, 3500, 3400, 3500,
      ])

      const quiet = strategy1.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
        candles: quietCandles,
      }))
      const volatile_ = strategy2.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
        candles: volatileCandles,
      }))

      const quietBid = quiet.find(d => d.action === 'BUY')!.stopLoss!
      const volBid = volatile_.find(d => d.action === 'BUY')!.stopLoss!

      // In high vol, buffer is wider -> bid further from liq zone (higher, closer to mid)
      expect(volBid).toBeGreaterThan(quietBid)
    })

    it('should use tight buffer in low volatility', () => {
      const strategy = new LiquidationMM()
      const quietCandles = makeCandles(Array.from({ length: 20 }, () => 3450))

      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
        candles: quietCandles,
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      // In quiet market, bid should be close to liquidation zone
      // liqZone = 3450 * 0.95 = 3277.5
      expect(buy.stopLoss!).toBeLessThan(3350)
    })
  })

  describe('quoting', () => {
    it('should emit BUY near lower liquidation zone (long squeeze)', () => {
      const strategy = new LiquidationMM()
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
      }))

      const buy = decisions.find(d => d.action === 'BUY')
      expect(buy).toBeDefined()
      expect(buy!.stopLoss!).toBeLessThan(3450)
    })

    it('should emit SELL near upper liquidation zone (short squeeze)', () => {
      const strategy = new LiquidationMM()
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: -0.001 }),
      }))

      const sell = decisions.find(d => d.action === 'SELL')
      expect(sell).toBeDefined()
      expect(sell!.stopLoss!).toBeGreaterThan(3450)
    })

    it('should emit both BUY and SELL when funding is near zero', () => {
      const strategy = new LiquidationMM()
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.00001 }), // below threshold
      }))

      // When funding is near zero, place orders on both sides with base spread
      const buy = decisions.find(d => d.action === 'BUY')
      const sell = decisions.find(d => d.action === 'SELL')
      expect(buy).toBeDefined()
      expect(sell).toBeDefined()
    })

    it('should respect max_position limits', () => {
      const strategy = new LiquidationMM()
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
        positions: [{
          symbol: 'ETH-PERP',
          side: 'LONG',
          size: 1.0,
          entryPrice: 3400,
          markPrice: 3450,
          unrealizedPnl: 50,
          leverage: 10,
          liquidationPrice: 3000,
        }],
      }))

      const buy = decisions.find(d => d.action === 'BUY')
      expect(buy).toBeUndefined()
    })

    it('should use GTC order type (need to sit in book)', () => {
      const strategy = new LiquidationMM()
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
      }))

      decisions.filter(d => d.action !== 'HOLD').forEach(d => {
        expect(d.orderType).toBe('GTC')
      })
    })

    it('should include liquidation zone and funding in reason string', () => {
      const strategy = new LiquidationMM()
      const decisions = strategy.onTick(makeCtx({
        ticker: makeTicker({ fundingRate: 0.001 }),
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('LiquidationMM')
      expect(buy.reason).toContain('funding')
    })
  })
})
