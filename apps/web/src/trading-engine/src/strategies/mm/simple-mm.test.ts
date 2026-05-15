import { SimpleMM } from './simple-mm.js'
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
    name: 'simple-mm',
    symbols: ['ETH-PERP'],
    params: {
      spread_bps: 10,
      order_size: 0.1,
      max_position: 1.0,
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

function makeCtx(positions: Position[] = [], params: Record<string, number | string | boolean> = {}): TickContext {
  return {
    adapter: mockAdapter,
    positions,
    balances: [makeBalance()],
    ticker: makeTicker(3450),
    orderBook: makeOrderBook(),
    candles: makeCandles(),
    config: makeConfig(params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('SimpleMM', () => {
  it('should quote both bid and ask around mid price', () => {
    const strategy = new SimpleMM()
    const decisions = strategy.onTick(makeCtx())

    const buy = decisions.find(d => d.action === 'BUY')
    const sell = decisions.find(d => d.action === 'SELL')

    expect(buy).toBeDefined()
    expect(sell).toBeDefined()
    expect(buy!.symbol).toBe('ETH-PERP')
    expect(sell!.symbol).toBe('ETH-PERP')
  })

  it('should place bid and ask at correct prices given spread_bps=10', () => {
    // mid=3450, spread_bps=10 → halfSpread = 3450 * 10/10000/2 = 1.725
    // bid = 3448.275, ask = 3451.725
    const strategy = new SimpleMM()
    const decisions = strategy.onTick(makeCtx())

    const buy = decisions.find(d => d.action === 'BUY')!
    const sell = decisions.find(d => d.action === 'SELL')!

    const halfSpread = 3450 * 10 / 10_000 / 2
    expect(buy.stopLoss).toBeCloseTo(3450 - halfSpread, 5)
    expect(sell.stopLoss).toBeCloseTo(3450 + halfSpread, 5)
  })

  it('should use configured order_size', () => {
    const strategy = new SimpleMM()
    const decisions = strategy.onTick(makeCtx([], { spread_bps: 10, order_size: 0.25, max_position: 1.0 }))

    decisions.filter(d => d.action !== 'HOLD').forEach(d => {
      expect(d.size).toBe(0.25)
    })
  })

  it('should use ALO order type for all orders', () => {
    const strategy = new SimpleMM()
    const decisions = strategy.onTick(makeCtx())

    decisions.filter(d => d.action !== 'HOLD').forEach(d => {
      expect(d.orderType).toBe('ALO')
    })
  })

  it('should return HOLD on BUY side when net long position exceeds max_position', () => {
    const strategy = new SimpleMM()
    const positions: Position[] = [{
      symbol: 'ETH-PERP',
      side: 'LONG',
      size: 1.0,
      entryPrice: 3400,
      markPrice: 3450,
      unrealizedPnl: 25,
      leverage: 10,
      liquidationPrice: 3000,
    }]

    const decisions = strategy.onTick(makeCtx(positions))
    const buy = decisions.find(d => d.action === 'BUY')
    const sell = decisions.find(d => d.action === 'SELL')

    // at max long: no BUY, still can SELL
    expect(buy).toBeUndefined()
    expect(sell).toBeDefined()
  })

  it('should return HOLD on SELL side when net short position exceeds max_position', () => {
    const strategy = new SimpleMM()
    const positions: Position[] = [{
      symbol: 'ETH-PERP',
      side: 'SHORT',
      size: 1.0,
      entryPrice: 3500,
      markPrice: 3450,
      unrealizedPnl: 25,
      leverage: 10,
      liquidationPrice: 4000,
    }]

    const decisions = strategy.onTick(makeCtx(positions))
    const sell = decisions.find(d => d.action === 'SELL')
    const buy = decisions.find(d => d.action === 'BUY')

    // at max short: no SELL, still can BUY
    expect(sell).toBeUndefined()
    expect(buy).toBeDefined()
  })

  it('should use custom spread_bps from config', () => {
    const strategy = new SimpleMM()
    const spreadBps = 20
    const mid = 3450
    const halfSpread = mid * spreadBps / 10_000 / 2

    const decisions = strategy.onTick(makeCtx([], { spread_bps: spreadBps, order_size: 0.1, max_position: 1.0 }))
    const buy = decisions.find(d => d.action === 'BUY')!
    const sell = decisions.find(d => d.action === 'SELL')!

    expect(buy.stopLoss).toBeCloseTo(mid - halfSpread, 5)
    expect(sell.stopLoss).toBeCloseTo(mid + halfSpread, 5)
  })

  it('should have confidence between 0 and 100', () => {
    const strategy = new SimpleMM()
    const decisions = strategy.onTick(makeCtx())

    decisions.forEach(d => {
      expect(d.confidence).toBeGreaterThanOrEqual(0)
      expect(d.confidence).toBeLessThanOrEqual(100)
    })
  })
})
