import { RegimeMM } from './regime-mm.js'
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

function makeBalance(total = 10_000): Balance {
  return { currency: 'USDT', available: total * 0.75, total, unrealizedPnl: 0 }
}

/** Generate candles with controlled close-to-close variation */
function makeFlatCandles(): Candle[] {
  // Very flat: sigma ~ 0 -> LOW regime
  const base = 3450
  return Array.from({ length: 20 }, (_, i) => ({
    timestamp: Date.now() - (20 - i) * 60_000,
    open: base,
    high: base + 0.01,
    low: base - 0.01,
    close: base + (i % 2 === 0 ? 0.001 : -0.001),
    volume: 100,
  }))
}

function makeNormalCandles(): Candle[] {
  // Moderate variation: sigma ~ 0.002 -> NORMAL
  const base = 3450
  return Array.from({ length: 20 }, (_, i) => ({
    timestamp: Date.now() - (20 - i) * 60_000,
    open: base,
    high: base + 10,
    low: base - 10,
    close: base + (i % 2 === 0 ? 7 : -7),
    volume: 100,
  }))
}

function makeHighVolCandles(): Candle[] {
  // sigma ~ 0.008 -> HIGH regime [0.005, 0.015)
  const base = 3450
  const swing = 14
  return Array.from({ length: 20 }, (_, i) => ({
    timestamp: Date.now() - (20 - i) * 60_000,
    open: base,
    high: base + swing + 2,
    low: base - swing - 2,
    close: base + (i % 2 === 0 ? swing : -swing),
    volume: 500,
  }))
}

function makeExtremeCandles(): Candle[] {
  // sigma > 0.015 -> EXTREME regime
  const base = 3450
  const swing = 30
  return Array.from({ length: 20 }, (_, i) => ({
    timestamp: Date.now() - (20 - i) * 60_000,
    open: base,
    high: base + swing + 10,
    low: base - swing - 10,
    close: base + (i % 2 === 0 ? swing : -swing),
    volume: 2000,
  }))
}

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'regime-mm',
    symbols: ['ETH-PERP'],
    params: {
      base_spread_bps: 10,
      order_size: 0.1,
      max_position: 1.0,
      vol_window: 20,
      ...params,
    },
  }
}

const mockAdapter = {
  name: 'mock',
  getTicker: async () => makeTicker(3450),
  getOrderBook: async () => makeOrderBook(),
  getCandles: async () => makeFlatCandles(),
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
  } = {},
): TickContext {
  return {
    adapter: mockAdapter,
    positions: overrides.positions ?? [],
    balances: [makeBalance()],
    ticker: makeTicker(3450),
    orderBook: makeOrderBook(),
    candles: overrides.candles ?? makeFlatCandles(),
    config: makeConfig(overrides.params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('RegimeMM', () => {
  describe('regime classification', () => {
    it('should classify low volatility candles as LOW regime', () => {
      const strategy = new RegimeMM()
      const lowCtx = makeCtx({ candles: makeFlatCandles() })
      // 3 ticks for hysteresis from NORMAL -> LOW
      strategy.onTick(lowCtx)
      strategy.onTick(lowCtx)
      const decisions = strategy.onTick(lowCtx)
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('LOW')
    })

    it('should classify normal volatility candles as NORMAL regime', () => {
      const strategy = new RegimeMM()
      // Need 3 ticks to pass hysteresis from default NORMAL
      // NORMAL is default, so first tick is already NORMAL
      const decisions = strategy.onTick(makeCtx({ candles: makeNormalCandles() }))
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('NORMAL')
    })

    it('should classify high volatility candles as HIGH regime', () => {
      const strategy = new RegimeMM()
      const highCtx = makeCtx({ candles: makeHighVolCandles() })
      // 3 ticks for hysteresis
      strategy.onTick(highCtx)
      strategy.onTick(highCtx)
      const decisions = strategy.onTick(highCtx)
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('HIGH')
    })

    it('should classify extreme volatility candles as EXTREME regime', () => {
      const strategy = new RegimeMM()
      const extremeCtx = makeCtx({ candles: makeExtremeCandles() })
      // 3 ticks for hysteresis
      strategy.onTick(extremeCtx)
      strategy.onTick(extremeCtx)
      const decisions = strategy.onTick(extremeCtx)
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('EXTREME')
    })
  })

  describe('regime hysteresis', () => {
    it('should not switch regime on a single tick', () => {
      const strategy = new RegimeMM()
      // Start in NORMAL (default), give it HIGH vol candles
      const decisions = strategy.onTick(makeCtx({ candles: makeHighVolCandles() }))
      const buy = decisions.find(d => d.action === 'BUY')!
      // Still NORMAL because hysteresis requires 3 ticks
      expect(buy.reason).toContain('NORMAL')
    })

    it('should switch regime after 3 consecutive ticks in new regime', () => {
      const strategy = new RegimeMM()
      const highCtx = makeCtx({ candles: makeHighVolCandles() })
      strategy.onTick(highCtx) // tick 1 in HIGH
      strategy.onTick(highCtx) // tick 2 in HIGH
      const decisions = strategy.onTick(highCtx) // tick 3 -> switch to HIGH
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('HIGH')
    })

    it('should reset counter if regime bounces back', () => {
      const strategy = new RegimeMM()
      const highCtx = makeCtx({ candles: makeHighVolCandles() })
      const normalCtx = makeCtx({ candles: makeNormalCandles() })

      strategy.onTick(highCtx) // tick 1 in HIGH
      strategy.onTick(highCtx) // tick 2 in HIGH
      strategy.onTick(normalCtx) // bounce back to NORMAL -> resets counter
      strategy.onTick(highCtx) // tick 1 in HIGH again (reset)
      const decisions = strategy.onTick(highCtx) // tick 2 in HIGH
      const buy = decisions.find(d => d.action === 'BUY')!
      // Still NORMAL because counter was reset
      expect(buy.reason).toContain('NORMAL')
    })
  })

  describe('regime-specific quoting', () => {
    it('should use tight spread and large size in LOW regime', () => {
      const strategy = new RegimeMM()
      // LOW is immediate from default since LOW needs hysteresis too,
      // but we start from NORMAL. Let's run 3 ticks of LOW to switch.
      const lowCtx = makeCtx({ candles: makeFlatCandles() })
      strategy.onTick(lowCtx)
      strategy.onTick(lowCtx)
      const decisions = strategy.onTick(lowCtx)

      const buy = decisions.find(d => d.action === 'BUY')!
      // In LOW: spreadMultiplier=0.5, sizeMultiplier=2.0
      // base size=0.1, so size=0.2
      expect(buy.size).toBeCloseTo(0.2, 5)
    })

    it('should use base spread and size in NORMAL regime', () => {
      const strategy = new RegimeMM()
      const decisions = strategy.onTick(makeCtx({ candles: makeNormalCandles() }))
      const buy = decisions.find(d => d.action === 'BUY')!
      // NORMAL: spreadMultiplier=1.0, sizeMultiplier=1.0
      expect(buy.size).toBeCloseTo(0.1, 5)
    })

    it('should use wide spread and small size in HIGH regime', () => {
      const strategy = new RegimeMM()
      const highCtx = makeCtx({ candles: makeHighVolCandles() })
      strategy.onTick(highCtx)
      strategy.onTick(highCtx)
      const decisions = strategy.onTick(highCtx)

      const buy = decisions.find(d => d.action === 'BUY')!
      // HIGH: sizeMultiplier=0.5
      expect(buy.size).toBeCloseTo(0.05, 5)
    })

    it('should use very wide spread and minimal size in EXTREME regime', () => {
      const strategy = new RegimeMM()
      const extremeCtx = makeCtx({ candles: makeExtremeCandles() })
      strategy.onTick(extremeCtx)
      strategy.onTick(extremeCtx)
      const decisions = strategy.onTick(extremeCtx)

      const buy = decisions.find(d => d.action === 'BUY')!
      // EXTREME: sizeMultiplier=0.25
      expect(buy.size).toBeCloseTo(0.025, 5)
    })
  })

  describe('inventory skew per regime', () => {
    it('should apply no skew in LOW regime', () => {
      const strategy = new RegimeMM()
      const lowCtx = makeCtx({ candles: makeFlatCandles() })
      strategy.onTick(lowCtx)
      strategy.onTick(lowCtx)

      // No position
      const noPos = strategy.onTick(makeCtx({ candles: makeFlatCandles() }))
      // With position - need a fresh strategy for fair comparison
      const strategy2 = new RegimeMM()
      const lowCtx2Pos = makeCtx({
        candles: makeFlatCandles(),
        positions: [{
          symbol: 'ETH-PERP',
          side: 'LONG',
          size: 0.5,
          entryPrice: 3400,
          markPrice: 3450,
          unrealizedPnl: 25,
          leverage: 10,
          liquidationPrice: 3000,
        }],
      })
      strategy2.onTick(makeCtx({ candles: makeFlatCandles() }))
      strategy2.onTick(makeCtx({ candles: makeFlatCandles() }))
      const withPos = strategy2.onTick(lowCtx2Pos)

      const noBid = noPos.find(d => d.action === 'BUY')!.stopLoss!
      const noAsk = noPos.find(d => d.action === 'SELL')!.stopLoss!
      const posBid = withPos.find(d => d.action === 'BUY')!.stopLoss!
      const posAsk = withPos.find(d => d.action === 'SELL')!.stopLoss!

      const noMid = (noBid + noAsk) / 2
      const posMid = (posBid + posAsk) / 2
      // LOW regime: inventorySkewFactor=0, so midpoints should be essentially the same
      expect(Math.abs(noMid - posMid)).toBeLessThan(0.01)
    })

    it('should apply strong skew in HIGH regime', () => {
      const strategy = new RegimeMM()
      const highCtx = makeCtx({ candles: makeHighVolCandles() })
      strategy.onTick(highCtx)
      strategy.onTick(highCtx)

      // No position
      const noPos = strategy.onTick(makeCtx({ candles: makeHighVolCandles() }))

      // With long position
      const strategy2 = new RegimeMM()
      strategy2.onTick(makeCtx({ candles: makeHighVolCandles() }))
      strategy2.onTick(makeCtx({ candles: makeHighVolCandles() }))
      const withPos = strategy2.onTick(makeCtx({
        candles: makeHighVolCandles(),
        positions: [{
          symbol: 'ETH-PERP',
          side: 'LONG',
          size: 0.5,
          entryPrice: 3400,
          markPrice: 3450,
          unrealizedPnl: 25,
          leverage: 10,
          liquidationPrice: 3000,
        }],
      }))

      const noMid = (noPos.find(d => d.action === 'BUY')!.stopLoss! + noPos.find(d => d.action === 'SELL')!.stopLoss!) / 2
      const posMid = (withPos.find(d => d.action === 'BUY')!.stopLoss! + withPos.find(d => d.action === 'SELL')!.stopLoss!) / 2

      // HIGH regime: inventorySkewFactor=0.7
      // Long position should push fair value down (discourage more longs)
      expect(posMid).toBeLessThan(noMid)
    })
  })

  describe('quoting', () => {
    it('should emit BUY and SELL decisions', () => {
      const strategy = new RegimeMM()
      const decisions = strategy.onTick(makeCtx())
      expect(decisions.find(d => d.action === 'BUY')).toBeDefined()
      expect(decisions.find(d => d.action === 'SELL')).toBeDefined()
    })

    it('should respect max_position limits', () => {
      const strategy = new RegimeMM()
      const decisions = strategy.onTick(makeCtx({
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
      expect(decisions.find(d => d.action === 'BUY')).toBeUndefined()
      expect(decisions.find(d => d.action === 'SELL')).toBeDefined()
    })

    it('should include regime name in reason string', () => {
      const strategy = new RegimeMM()
      const decisions = strategy.onTick(makeCtx())
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.reason).toContain('RegimeMM')
    })
  })
})
