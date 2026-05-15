import { GridMM } from './grid-mm.js'
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
    timestamp: Date.now() - (20 - i) * 60_000,
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
    name: 'grid-mm',
    symbols: ['ETH-PERP'],
    params: {
      grid_levels: 5,
      grid_spacing_bps: 20,
      size_per_level: 0.05,
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
  placeOrder: async () => ({ orderId: '1', status: 'FILLED' as const, filledSize: 0.05, filledPrice: 3450, timestamp: Date.now() }),
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
  } = {},
): TickContext {
  return {
    adapter: mockAdapter,
    positions: overrides.positions ?? [],
    balances: [makeBalance()],
    ticker: overrides.ticker ?? makeTicker(3450),
    orderBook: makeOrderBook(),
    candles: makeCandles(),
    config: makeConfig(overrides.params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('GridMM', () => {
  describe('grid computation', () => {
    it('should place grid_levels orders on each side', () => {
      const strategy = new GridMM()
      const decisions = strategy.onTick(makeCtx())

      const buys = decisions.filter(d => d.action === 'BUY')
      const sells = decisions.filter(d => d.action === 'SELL')

      // 5 levels on each side
      expect(buys).toHaveLength(5)
      expect(sells).toHaveLength(5)
    })

    it('should space orders at equal intervals', () => {
      const strategy = new GridMM()
      const mid = 3450
      const spacingBps = 20
      const decisions = strategy.onTick(makeCtx())

      const buys = decisions.filter(d => d.action === 'BUY').sort((a, b) => b.stopLoss! - a.stopLoss!)
      const sells = decisions.filter(d => d.action === 'SELL').sort((a, b) => a.stopLoss! - b.stopLoss!)

      // Verify bid levels: mid * (1 - i * spacing/10000)
      for (let i = 0; i < buys.length; i++) {
        const level = i + 1
        const expectedPrice = mid * (1 - level * spacingBps / 10_000)
        expect(buys[i]!.stopLoss).toBeCloseTo(expectedPrice, 2)
      }

      // Verify ask levels: mid * (1 + i * spacing/10000)
      for (let i = 0; i < sells.length; i++) {
        const level = i + 1
        const expectedPrice = mid * (1 + level * spacingBps / 10_000)
        expect(sells[i]!.stopLoss).toBeCloseTo(expectedPrice, 2)
      }
    })

    it('should use configured size_per_level for each order', () => {
      const strategy = new GridMM()
      const decisions = strategy.onTick(makeCtx({ params: { size_per_level: 0.08 } }))

      decisions.filter(d => d.action !== 'HOLD').forEach(d => {
        expect(d.size).toBe(0.08)
      })
    })

    it('should skip levels where total position would exceed max_position', () => {
      const strategy = new GridMM()
      // With max_position=0.15 and size_per_level=0.05, only 3 levels fit per side
      const decisions = strategy.onTick(makeCtx({
        params: { grid_levels: 5, size_per_level: 0.05, max_position: 0.15 },
      }))

      const buys = decisions.filter(d => d.action === 'BUY')
      const sells = decisions.filter(d => d.action === 'SELL')

      expect(buys).toHaveLength(3)
      expect(sells).toHaveLength(3)
    })
  })

  describe('position limits', () => {
    it('should reduce grid levels on buy side when near max long', () => {
      const strategy = new GridMM()
      // Net long 0.4, max_position 0.5, size_per_level 0.05
      // Remaining buy capacity = 0.1 -> 2 levels
      const decisions = strategy.onTick(makeCtx({
        positions: [{
          symbol: 'ETH-PERP',
          side: 'LONG',
          size: 0.4,
          entryPrice: 3400,
          markPrice: 3450,
          unrealizedPnl: 20,
          leverage: 10,
          liquidationPrice: 3000,
        }],
        params: { grid_levels: 5, size_per_level: 0.05, max_position: 0.5 },
      }))

      const buys = decisions.filter(d => d.action === 'BUY')
      expect(buys).toHaveLength(2)
    })

    it('should reduce grid levels on sell side when near max short', () => {
      const strategy = new GridMM()
      const decisions = strategy.onTick(makeCtx({
        positions: [{
          symbol: 'ETH-PERP',
          side: 'SHORT',
          size: 0.4,
          entryPrice: 3500,
          markPrice: 3450,
          unrealizedPnl: 25,
          leverage: 10,
          liquidationPrice: 4000,
        }],
        params: { grid_levels: 5, size_per_level: 0.05, max_position: 0.5 },
      }))

      const sells = decisions.filter(d => d.action === 'SELL')
      expect(sells).toHaveLength(2)
    })

    it('should emit HOLD when max position reached on both sides', () => {
      const strategy = new GridMM()
      const decisions = strategy.onTick(makeCtx({
        positions: [
          {
            symbol: 'ETH-PERP',
            side: 'LONG',
            size: 1.0,
            entryPrice: 3400,
            markPrice: 3450,
            unrealizedPnl: 50,
            leverage: 10,
            liquidationPrice: 3000,
          },
        ],
        params: { max_position: 0.5 },
      }))

      // Net position = 1.0, max = 0.5 -> exceeded on both sides
      // Cannot BUY (would increase to 1.05) or SELL beyond max short
      // Actually net long is 1.0, max_position=0.5:
      // buy: 1.0 >= 0.5 -> no buys
      // sell: -(-1.0) = net is +1.0 > -0.5, so can still sell
      // Let's use a scenario where both sides are blocked
      const decisions2 = strategy.onTick(makeCtx({
        positions: [
          {
            symbol: 'ETH-PERP',
            side: 'LONG',
            size: 1.0,
            entryPrice: 3400,
            markPrice: 3450,
            unrealizedPnl: 50,
            leverage: 10,
            liquidationPrice: 3000,
          },
          {
            symbol: 'ETH-PERP',
            side: 'SHORT',
            size: 1.0,
            entryPrice: 3500,
            markPrice: 3450,
            unrealizedPnl: 50,
            leverage: 10,
            liquidationPrice: 4000,
          },
        ],
        params: { max_position: 0.0, size_per_level: 0.05 },
      }))

      const hold = decisions2.find(d => d.action === 'HOLD')
      expect(hold).toBeDefined()
      expect(hold!.reason).toContain('max position')
    })
  })

  describe('order properties', () => {
    it('should use ALO order type for all grid orders', () => {
      const strategy = new GridMM()
      const decisions = strategy.onTick(makeCtx())
      decisions.filter(d => d.action !== 'HOLD').forEach(d => {
        expect(d.orderType).toBe('ALO')
      })
    })

    it('should include level number in reason string', () => {
      const strategy = new GridMM()
      const decisions = strategy.onTick(makeCtx())
      const buy1 = decisions.filter(d => d.action === 'BUY').sort((a, b) => b.stopLoss! - a.stopLoss!)[0]!
      expect(buy1.reason).toContain('L1')
      expect(buy1.reason).toContain('GridMM')
    })

    it('should assign decreasing confidence for farther levels', () => {
      const strategy = new GridMM()
      const decisions = strategy.onTick(makeCtx())
      const buys = decisions.filter(d => d.action === 'BUY').sort((a, b) => b.stopLoss! - a.stopLoss!)

      for (let i = 1; i < buys.length; i++) {
        expect(buys[i]!.confidence).toBeLessThan(buys[i - 1]!.confidence)
      }
    })
  })
})
