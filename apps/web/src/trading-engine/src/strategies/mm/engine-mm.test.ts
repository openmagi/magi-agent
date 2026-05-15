import { EngineMM } from './engine-mm.js'
import type { TickContext, StrategyConfig, Ticker, OrderBook, OrderBookLevel, Candle, Balance, Position } from '../../types.js'

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

function makeOrderBook(
  bids: OrderBookLevel[] = [{ price: 3449, size: 2.0 }],
  asks: OrderBookLevel[] = [{ price: 3451, size: 1.0 }],
): OrderBook {
  return {
    symbol: 'ETH-PERP',
    bids,
    asks,
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
    name: 'engine-mm',
    symbols: ['ETH-PERP'],
    params: {
      base_spread_bps: 10,
      order_size: 0.1,
      max_position: 1.0,
      w_micro: 0.4,
      w_vwap: 0.2,
      w_ofi: 0.2,
      w_mean_rev: 0.2,
      ofi_sensitivity: 0.5,
      mean_rev_period: 20,
      ...params,
    },
  }
}

const mockAdapter = {
  name: 'mock',
  getTicker: async () => makeTicker(3450),
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
    candles?: Candle[]
    orderBook?: OrderBook
    ticker?: Ticker
  } = {},
): TickContext {
  return {
    adapter: mockAdapter,
    positions: overrides.positions ?? [],
    balances: [makeBalance()],
    ticker: overrides.ticker ?? makeTicker(3450),
    orderBook: overrides.orderBook ?? makeOrderBook(),
    candles: overrides.candles ?? makeCandles(),
    config: makeConfig(overrides.params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('EngineMM', () => {
  describe('fair value computation', () => {
    it('should compute micro-price from order book top levels', () => {
      // microPrice = (bid * askSize + ask * bidSize) / (bidSize + askSize)
      // bid=3449, ask=3451, bidSize=2.0, askSize=1.0
      // microPrice = (3449*1.0 + 3451*2.0) / (2.0+1.0) = (3449+6902)/3 = 10351/3 = 3450.333...
      const strategy = new EngineMM()
      const ob = makeOrderBook(
        [{ price: 3449, size: 2.0 }],
        [{ price: 3451, size: 1.0 }],
      )
      // With w_micro=1.0 and all other weights=0, fair value should approximate microPrice
      const decisions = strategy.onTick(makeCtx({
        orderBook: ob,
        params: { w_micro: 1.0, w_vwap: 0, w_ofi: 0, w_mean_rev: 0 },
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      const sell = decisions.find(d => d.action === 'SELL')!
      // Fair value = microPrice = 3450.333
      // bid and ask should be symmetric around fair value
      const impliedFairValue = (buy.stopLoss! + sell.stopLoss!) / 2
      expect(impliedFairValue).toBeCloseTo(3450.333, 1)
    })

    it('should compute VWAP deviation from candle data', () => {
      // Candles with increasing prices -> VWAP below mid -> fair value pulled down
      const strategy = new EngineMM()
      const risingCandles = makeCandles([
        3400, 3410, 3420, 3430, 3440, 3450, 3460, 3470, 3480, 3490,
        3400, 3410, 3420, 3430, 3440, 3450, 3460, 3470, 3480, 3490,
      ])
      // VWAP is volume-weighted avg of closes (all equal volume=100)
      // VWAP = avg(closes) = (3400+3410+...+3490)*2/20 = 3445
      // With w_vwap=1.0, fair value pulled toward VWAP direction
      const decisions = strategy.onTick(makeCtx({
        candles: risingCandles,
        params: { w_micro: 0, w_vwap: 1.0, w_ofi: 0, w_mean_rev: 0 },
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      const sell = decisions.find(d => d.action === 'SELL')!
      const impliedFairValue = (buy.stopLoss! + sell.stopLoss!) / 2
      // VWAP = 3445, mid = 3450, so fair value should be pulled below mid
      expect(impliedFairValue).toBeLessThan(3450)
    })

    it('should compute order flow imbalance (OFI)', () => {
      // Higher bid sizes relative to ask sizes -> buy pressure -> shift fair value up
      const strategy = new EngineMM()
      const buyPressureOB = makeOrderBook(
        [{ price: 3449, size: 10.0 }],  // large bid
        [{ price: 3451, size: 1.0 }],   // small ask
      )
      const sellPressureOB = makeOrderBook(
        [{ price: 3449, size: 1.0 }],   // small bid
        [{ price: 3451, size: 10.0 }],  // large ask
      )
      const buyDecisions = strategy.onTick(makeCtx({
        orderBook: buyPressureOB,
        params: { w_micro: 0, w_vwap: 0, w_ofi: 1.0, w_mean_rev: 0 },
      }))
      const sellDecisions = strategy.onTick(makeCtx({
        orderBook: sellPressureOB,
        params: { w_micro: 0, w_vwap: 0, w_ofi: 1.0, w_mean_rev: 0 },
      }))

      const buyFV = (buyDecisions.find(d => d.action === 'BUY')!.stopLoss! + buyDecisions.find(d => d.action === 'SELL')!.stopLoss!) / 2
      const sellFV = (sellDecisions.find(d => d.action === 'BUY')!.stopLoss! + sellDecisions.find(d => d.action === 'SELL')!.stopLoss!) / 2

      // Buy pressure should push fair value higher than sell pressure
      expect(buyFV).toBeGreaterThan(sellFV)
    })

    it('should compute mean reversion target from SMA', () => {
      // If SMA(closes) < mid, mean reversion pulls fair value down
      const strategy = new EngineMM()
      const lowCandles = makeCandles(Array.from({ length: 20 }, () => 3400))
      // SMA = 3400, mid = 3450 -> meanRevTarget below mid
      const decisions = strategy.onTick(makeCtx({
        candles: lowCandles,
        params: { w_micro: 0, w_vwap: 0, w_ofi: 0, w_mean_rev: 1.0 },
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      const sell = decisions.find(d => d.action === 'SELL')!
      const impliedFairValue = (buy.stopLoss! + sell.stopLoss!) / 2
      // Fair value should be pulled toward SMA=3400
      expect(impliedFairValue).toBeLessThan(3450)
    })

    it('should blend 4 signals with configurable weights', () => {
      const strategy = new EngineMM()
      // With equal weights and symmetric order book, fair value should be close to mid
      const decisions = strategy.onTick(makeCtx({
        orderBook: makeOrderBook(
          [{ price: 3449.5, size: 1.0 }],
          [{ price: 3450.5, size: 1.0 }],
        ),
      }))

      const buy = decisions.find(d => d.action === 'BUY')!
      const sell = decisions.find(d => d.action === 'SELL')!
      const impliedFairValue = (buy.stopLoss! + sell.stopLoss!) / 2
      // With symmetric OB and flat candles, fair value near mid
      expect(impliedFairValue).toBeCloseTo(3450, 0)
    })
  })

  describe('dynamic spread', () => {
    it('should widen spread when OFI is high (directional pressure)', () => {
      const strategy = new EngineMM()
      const balancedOB = makeOrderBook(
        [{ price: 3449, size: 1.0 }],
        [{ price: 3451, size: 1.0 }],
      )
      const imbalancedOB = makeOrderBook(
        [{ price: 3449, size: 10.0 }],
        [{ price: 3451, size: 1.0 }],
      )

      const balanced = strategy.onTick(makeCtx({ orderBook: balancedOB }))
      const imbalanced = strategy.onTick(makeCtx({ orderBook: imbalancedOB }))

      const bSpread = balanced.find(d => d.action === 'SELL')!.stopLoss! - balanced.find(d => d.action === 'BUY')!.stopLoss!
      const iSpread = imbalanced.find(d => d.action === 'SELL')!.stopLoss! - imbalanced.find(d => d.action === 'BUY')!.stopLoss!

      expect(iSpread).toBeGreaterThan(bSpread)
    })

    it('should widen spread in high volatility', () => {
      const strategy = new EngineMM()
      const quietCandles = makeCandles(Array.from({ length: 20 }, () => 3450))
      const volatileCandles = makeCandles([
        3450, 3500, 3400, 3500, 3400, 3500, 3400, 3500, 3400, 3500,
        3450, 3500, 3400, 3500, 3400, 3500, 3400, 3500, 3400, 3500,
      ])

      const quiet = strategy.onTick(makeCtx({ candles: quietCandles }))
      const volatile_ = strategy.onTick(makeCtx({ candles: volatileCandles }))

      const qSpread = quiet.find(d => d.action === 'SELL')!.stopLoss! - quiet.find(d => d.action === 'BUY')!.stopLoss!
      const vSpread = volatile_.find(d => d.action === 'SELL')!.stopLoss! - volatile_.find(d => d.action === 'BUY')!.stopLoss!

      expect(vSpread).toBeGreaterThan(qSpread)
    })

    it('should narrow spread when market is quiet and balanced', () => {
      const strategy = new EngineMM()
      const quietCandles = makeCandles(Array.from({ length: 20 }, () => 3450))
      const balancedOB = makeOrderBook(
        [{ price: 3449.5, size: 1.0 }],
        [{ price: 3450.5, size: 1.0 }],
      )

      const decisions = strategy.onTick(makeCtx({
        candles: quietCandles,
        orderBook: balancedOB,
      }))

      const spread = decisions.find(d => d.action === 'SELL')!.stopLoss! - decisions.find(d => d.action === 'BUY')!.stopLoss!
      // Base spread at 10bps of 3450 = 3.45
      // In quiet, balanced conditions, spread should be close to base
      expect(spread).toBeLessThan(5)
      expect(spread).toBeGreaterThan(0)
    })
  })

  describe('quoting', () => {
    it('should emit BUY and SELL around fair value', () => {
      const strategy = new EngineMM()
      const decisions = strategy.onTick(makeCtx())

      const buy = decisions.find(d => d.action === 'BUY')
      const sell = decisions.find(d => d.action === 'SELL')
      expect(buy).toBeDefined()
      expect(sell).toBeDefined()
      expect(buy!.symbol).toBe('ETH-PERP')
      expect(sell!.symbol).toBe('ETH-PERP')
    })

    it('should respect max_position limits', () => {
      const strategy = new EngineMM()
      const longPos: Position[] = [{
        symbol: 'ETH-PERP',
        side: 'LONG',
        size: 1.0,
        entryPrice: 3400,
        markPrice: 3450,
        unrealizedPnl: 50,
        leverage: 10,
        liquidationPrice: 3000,
      }]

      const decisions = strategy.onTick(makeCtx({ positions: longPos }))
      const buy = decisions.find(d => d.action === 'BUY')
      const sell = decisions.find(d => d.action === 'SELL')

      expect(buy).toBeUndefined()
      expect(sell).toBeDefined()
    })

    it('should use ALO order type', () => {
      const strategy = new EngineMM()
      const decisions = strategy.onTick(makeCtx())
      decisions.filter(d => d.action !== 'HOLD').forEach(d => {
        expect(d.orderType).toBe('ALO')
      })
    })

    it('should include fair value components in reason string', () => {
      const strategy = new EngineMM()
      const decisions = strategy.onTick(makeCtx())
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('EngineMM')
      expect(buy.reason).toContain('fv=')
    })

    it('should have confidence between 0 and 100', () => {
      const strategy = new EngineMM()
      const decisions = strategy.onTick(makeCtx())
      decisions.forEach(d => {
        expect(d.confidence).toBeGreaterThanOrEqual(0)
        expect(d.confidence).toBeLessThanOrEqual(100)
      })
    })
  })
})
