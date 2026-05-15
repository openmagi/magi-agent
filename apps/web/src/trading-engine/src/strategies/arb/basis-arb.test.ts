import { BasisArb } from './basis-arb.js'
import type { TickContext, StrategyConfig, StrategyDecision, Ticker, OrderBook, Candle, Balance, Position } from '../../types.js'

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

function makeCandles(): Candle[] {
  return Array.from({ length: 20 }, (_, i) => ({
    timestamp: Date.now() - i * 60_000,
    open: 3450,
    high: 3455,
    low: 3445,
    close: 3450,
    volume: 100,
  }))
}

function makeBalance(total = 10_000): Balance {
  return { currency: 'USDT', available: total * 0.75, total, unrealizedPnl: 0 }
}

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'basis-arb',
    symbols: ['ETH-PERP'],
    params: {
      min_basis_bps: 20,     // 20bps minimum basis
      order_size: 0.5,
      max_position: 2.0,
      spot_price: 3440,      // spot below perp = contango
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
  placeOrder: async () => ({ orderId: '1', status: 'FILLED' as const, filledSize: 0.5, filledPrice: 3450, timestamp: Date.now() }),
  cancelOrder: async () => {},
  cancelAllOrders: async () => {},
  setStopLoss: async () => ({ orderId: '2', status: 'OPEN' as const, filledSize: 0, filledPrice: 0, timestamp: Date.now() }),
  getOpenOrders: async () => [],
  getExchangeInfo: async () => ({ name: 'mock', testnet: true, supportedSymbols: ['ETH-PERP'], minOrderSizes: {}, tickSizes: {} }),
}

function makeCtx(
  positions: Position[] = [],
  params: Record<string, number | string | boolean> = {},
  tickerOverrides: Partial<Ticker> = {},
): TickContext {
  return {
    adapter: mockAdapter,
    positions,
    balances: [makeBalance()],
    ticker: makeTicker(tickerOverrides),
    orderBook: makeOrderBook(),
    candles: makeCandles(),
    config: makeConfig(params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('BasisArb', () => {
  const strategy = new BasisArb()

  it('should have name "basis-arb"', () => {
    expect(strategy.name).toBe('basis-arb')
  })

  describe('basis computation', () => {
    it('should compute basis as (perp - spot) / spot * 10000 in bps', () => {
      // perp mid = 3450, spot = 3440
      // basis = (3450 - 3440) / 3440 * 10000 = 29.07 bps (contango)
      // 29.07 > min_basis_bps=20 => should enter short
      const ctx = makeCtx([], { spot_price: 3440, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 })
      const decisions = strategy.onTick(ctx)
      const sell = decisions.find(d => d.action === 'SELL')
      expect(sell).toBeDefined()
    })

    it('should return HOLD when basis is below min_basis_bps', () => {
      // perp mid = 3450, spot = 3449 => basis = 1/3449*10000 = 2.9bps < 20bps
      const ctx = makeCtx([], { spot_price: 3449, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 })
      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })
  })

  describe('contango entry (perp > spot)', () => {
    it('should emit SELL (short perp) when basis exceeds min_basis_bps', () => {
      // perp=3465, spot=3440 => basis = 25/3440*10000 = 72.7bps > 20bps
      const ctx = makeCtx(
        [],
        { spot_price: 3440, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 },
        { mid: 3465 },
      )
      const decisions = strategy.onTick(ctx)
      const sell = decisions.find(d => d.action === 'SELL')
      expect(sell).toBeDefined()
      expect(sell!.symbol).toBe('ETH-PERP')
      expect(sell!.size).toBe(0.5)
    })

    it('should not enter when basis is positive but below threshold', () => {
      // perp=3441, spot=3440 => basis = 1/3440*10000 = 2.9bps < 20bps
      const ctx = makeCtx(
        [],
        { spot_price: 3440, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 },
        { mid: 3441 },
      )
      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })
  })

  describe('backwardation entry (perp < spot)', () => {
    it('should emit BUY (long perp) when negative basis exceeds min_basis_bps', () => {
      // perp=3430, spot=3450 => basis = (3430-3450)/3450*10000 = -57.97bps
      // abs(-57.97) > 20 => long perp
      const ctx = makeCtx(
        [],
        { spot_price: 3450, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 },
        { mid: 3430 },
      )
      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')
      expect(buy).toBeDefined()
      expect(buy!.symbol).toBe('ETH-PERP')
      expect(buy!.size).toBe(0.5)
    })
  })

  describe('position limits', () => {
    it('should respect max_position on long side', () => {
      const positions: Position[] = [{
        symbol: 'ETH-PERP',
        side: 'LONG',
        size: 2.0,
        entryPrice: 3400,
        markPrice: 3430,
        unrealizedPnl: 30,
        leverage: 10,
        liquidationPrice: 3000,
      }]
      // backwardation => would want BUY, but at max long
      const ctx = makeCtx(
        positions,
        { spot_price: 3450, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 },
        { mid: 3430 },
      )
      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should respect max_position on short side', () => {
      const positions: Position[] = [{
        symbol: 'ETH-PERP',
        side: 'SHORT',
        size: 2.0,
        entryPrice: 3500,
        markPrice: 3465,
        unrealizedPnl: 35,
        leverage: 10,
        liquidationPrice: 4000,
      }]
      // contango => would want SELL, but at max short
      const ctx = makeCtx(
        positions,
        { spot_price: 3440, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 },
        { mid: 3465 },
      )
      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })
  })

  describe('order properties', () => {
    it('should use GTC order type', () => {
      const ctx = makeCtx(
        [],
        { spot_price: 3440, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 },
        { mid: 3465 },
      )
      const decisions = strategy.onTick(ctx)
      decisions.filter((d: StrategyDecision) => d.action !== 'HOLD').forEach((d: StrategyDecision) => {
        expect(d.orderType).toBe('GTC')
      })
    })

    it('should include basis info in reason string', () => {
      const ctx = makeCtx(
        [],
        { spot_price: 3440, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 },
        { mid: 3465 },
      )
      const decisions = strategy.onTick(ctx)
      const action = decisions.find((d: StrategyDecision) => d.action !== 'HOLD')
      expect(action).toBeDefined()
      expect(action!.reason).toContain('basis')
    })

    it('should have confidence between 0 and 100', () => {
      const ctx = makeCtx(
        [],
        { spot_price: 3440, min_basis_bps: 20, order_size: 0.5, max_position: 2.0 },
        { mid: 3465 },
      )
      const decisions = strategy.onTick(ctx)
      decisions.forEach((d: StrategyDecision) => {
        expect(d.confidence).toBeGreaterThanOrEqual(0)
        expect(d.confidence).toBeLessThanOrEqual(100)
      })
    })
  })

  describe('missing spot price', () => {
    it('should return HOLD when spot_price is not provided', () => {
      const config = makeConfig({})
      delete (config.params as Record<string, unknown>)['spot_price']
      const ctx: TickContext = {
        adapter: mockAdapter,
        positions: [],
        balances: [makeBalance()],
        ticker: makeTicker({ mid: 3465 }),
        orderBook: makeOrderBook(),
        candles: makeCandles(),
        config,
        tickNumber: 1,
        timestamp: Date.now(),
      }
      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })
  })
})
