import { FundingArb } from './funding-arb.js'
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
    fundingRate: 0.0001, // 0.01% = 1bps
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
    name: 'funding-arb',
    symbols: ['ETH-PERP'],
    params: {
      min_spread: 0.0001,   // 1bps minimum funding spread
      order_size: 0.5,
      max_position: 2.0,
      peer_funding_rate: 0.00005, // 0.5bps on peer exchange
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

describe('FundingArb', () => {
  const strategy = new FundingArb()

  it('should have name "funding-arb"', () => {
    expect(strategy.name).toBe('funding-arb')
  })

  describe('spread computation', () => {
    it('should compute funding spread between primary and peer', () => {
      // primary = 0.0001 (1bps), peer = 0.00005 (0.5bps)
      // spread = 0.0001 - 0.00005 = 0.00005 (0.5bps)
      // abs(spread) < min_spread (1bps) => HOLD (spread too small with default threshold)
      // Use a lower threshold to trigger entry
      const ctx = makeCtx([], {
        min_spread: 0.00004, // lower threshold so 0.5bps spread qualifies
        order_size: 0.5,
        max_position: 2.0,
        peer_funding_rate: 0.00005,
      })
      const decisions = strategy.onTick(ctx)
      // With spread > threshold, should NOT be HOLD
      expect(decisions.some(d => d.action !== 'HOLD')).toBe(true)
    })

    it('should return HOLD when spread is below min_spread threshold', () => {
      // primary = 0.0001, peer = 0.00009 => spread = 0.00001 < min_spread=0.0001
      const ctx = makeCtx([], {
        min_spread: 0.0001,
        peer_funding_rate: 0.00009,
      })
      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })
  })

  describe('entry logic', () => {
    it('should emit SELL when primary funding is higher than peer (positive spread)', () => {
      // primary = 0.0003 (3bps), peer = 0.0001 (1bps), spread = 0.0002 > min_spread=0.0001
      // Primary funding is higher => we are paying more on primary => short primary
      const ctx = makeCtx(
        [],
        { min_spread: 0.0001, order_size: 0.5, max_position: 2.0, peer_funding_rate: 0.0001 },
        { fundingRate: 0.0003 },
      )
      const decisions = strategy.onTick(ctx)
      const sell = decisions.find(d => d.action === 'SELL')
      expect(sell).toBeDefined()
      expect(sell!.symbol).toBe('ETH-PERP')
      expect(sell!.size).toBe(0.5)
    })

    it('should emit BUY when primary funding is lower than peer (negative spread)', () => {
      // primary = -0.0002 (-2bps), peer = 0.0001 (1bps), spread = -0.0003
      // Primary funding is lower (negative = getting paid) => long primary
      const ctx = makeCtx(
        [],
        { min_spread: 0.0001, order_size: 0.5, max_position: 2.0, peer_funding_rate: 0.0001 },
        { fundingRate: -0.0002 },
      )
      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')
      expect(buy).toBeDefined()
      expect(buy!.symbol).toBe('ETH-PERP')
      expect(buy!.size).toBe(0.5)
    })

    it('should not enter when already at max position (long side)', () => {
      const positions: Position[] = [{
        symbol: 'ETH-PERP',
        side: 'LONG',
        size: 2.0,
        entryPrice: 3400,
        markPrice: 3450,
        unrealizedPnl: 50,
        leverage: 10,
        liquidationPrice: 3000,
      }]
      // negative spread => would want to BUY, but already at max long
      const ctx = makeCtx(
        positions,
        { min_spread: 0.0001, order_size: 0.5, max_position: 2.0, peer_funding_rate: 0.0001 },
        { fundingRate: -0.0002 },
      )
      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should not enter when already at max position (short side)', () => {
      const positions: Position[] = [{
        symbol: 'ETH-PERP',
        side: 'SHORT',
        size: 2.0,
        entryPrice: 3500,
        markPrice: 3450,
        unrealizedPnl: 50,
        leverage: 10,
        liquidationPrice: 4000,
      }]
      // positive spread => would want to SELL, but already at max short
      const ctx = makeCtx(
        positions,
        { min_spread: 0.0001, order_size: 0.5, max_position: 2.0, peer_funding_rate: 0.0001 },
        { fundingRate: 0.0003 },
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
        { min_spread: 0.0001, order_size: 0.5, max_position: 2.0, peer_funding_rate: 0.0001 },
        { fundingRate: 0.0003 },
      )
      const decisions = strategy.onTick(ctx)
      decisions.filter((d: StrategyDecision) => d.action !== 'HOLD').forEach((d: StrategyDecision) => {
        expect(d.orderType).toBe('GTC')
      })
    })

    it('should include spread and direction in reason string', () => {
      const ctx = makeCtx(
        [],
        { min_spread: 0.0001, order_size: 0.5, max_position: 2.0, peer_funding_rate: 0.0001 },
        { fundingRate: 0.0003 },
      )
      const decisions = strategy.onTick(ctx)
      const action = decisions.find((d: StrategyDecision) => d.action !== 'HOLD')
      expect(action).toBeDefined()
      expect(action!.reason).toContain('funding')
    })

    it('should have confidence between 0 and 100', () => {
      const ctx = makeCtx(
        [],
        { min_spread: 0.0001, order_size: 0.5, max_position: 2.0, peer_funding_rate: 0.0001 },
        { fundingRate: 0.0003 },
      )
      const decisions = strategy.onTick(ctx)
      decisions.forEach((d: StrategyDecision) => {
        expect(d.confidence).toBeGreaterThanOrEqual(0)
        expect(d.confidence).toBeLessThanOrEqual(100)
      })
    })
  })

  describe('missing peer funding rate', () => {
    it('should return HOLD when peer_funding_rate is not provided', () => {
      const config = makeConfig({})
      // Remove peer_funding_rate
      delete (config.params as Record<string, unknown>)['peer_funding_rate']
      const ctx: TickContext = {
        adapter: mockAdapter,
        positions: [],
        balances: [makeBalance()],
        ticker: makeTicker({ fundingRate: 0.0003 }),
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
