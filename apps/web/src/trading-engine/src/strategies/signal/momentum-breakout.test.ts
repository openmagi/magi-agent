import { MomentumBreakout, computeATR, detectBreakout, volumeConfirmed } from './momentum-breakout.js'
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

function makeCandles(
  count: number,
  opts: { close?: number; high?: number; low?: number; volume?: number } = {}
): Candle[] {
  const { close = 3450, high = 3455, low = 3445, volume = 100 } = opts
  return Array.from({ length: count }, (_, i) => ({
    timestamp: Date.now() - (count - i) * 60_000,
    open: close - 5,
    high,
    low,
    close,
    volume,
  }))
}

/** Creates candles that trend upwards then break out at the end */
function makeBreakoutCandles(direction: 'UP' | 'DOWN', lookback: number): Candle[] {
  const base = 3450
  // Build lookback candles in a range
  const rangeCandles: Candle[] = Array.from({ length: lookback + 5 }, (_, i) => ({
    timestamp: Date.now() - (lookback + 5 - i) * 60_000,
    open: base - 2,
    high: base + 10,
    low: base - 10,
    close: base,
    volume: 100,
  }))

  if (direction === 'UP') {
    // Last candle breaks above the highest high with high volume
    rangeCandles.push({
      timestamp: Date.now(),
      open: base + 5,
      high: base + 20,
      low: base + 5,
      close: base + 15, // above highest high (base + 10)
      volume: 500, // high volume
    })
  } else {
    // Last candle breaks below the lowest low with high volume
    rangeCandles.push({
      timestamp: Date.now(),
      open: base - 5,
      high: base - 5,
      low: base - 20,
      close: base - 15, // below lowest low (base - 10)
      volume: 500, // high volume
    })
  }

  return rangeCandles
}

function makeBalance(total = 10_000): Balance {
  return { currency: 'USDT', available: total * 0.75, total, unrealizedPnl: 0 }
}

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'momentum-breakout',
    symbols: ['ETH-PERP'],
    params: {
      atr_period: 14,
      lookback_period: 20,
      volume_threshold: 2.0,
      atr_multiplier: 2.0,
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

describe('MomentumBreakout', () => {
  describe('computeATR', () => {
    it('should compute ATR from candle high/low/close data', () => {
      const candles: Candle[] = [
        { timestamp: 1, open: 100, high: 110, low: 90, close: 105, volume: 100 },
        { timestamp: 2, open: 105, high: 115, low: 95, close: 100, volume: 100 },
        { timestamp: 3, open: 100, high: 112, low: 92, close: 108, volume: 100 },
      ]
      const atr = computeATR(candles, 14)
      // TR for candle[1]: max(115-95, |115-105|, |95-105|) = max(20, 10, 10) = 20
      // TR for candle[2]: max(112-92, |112-100|, |92-100|) = max(20, 12, 8) = 20
      // ATR = (20 + 20) / 2 = 20
      expect(atr).toBe(20)
    })

    it('should return 0 when fewer than 2 candles', () => {
      const single: Candle[] = [
        { timestamp: 1, open: 100, high: 110, low: 90, close: 105, volume: 100 },
      ]
      expect(computeATR(single, 14)).toBe(0)
      expect(computeATR([], 14)).toBe(0)
    })
  })

  describe('detectBreakout', () => {
    it('should detect UP breakout when close > highest high of lookback', () => {
      const candles = makeBreakoutCandles('UP', 20)
      const result = detectBreakout(candles, 20)
      expect(result).toBe('UP')
    })

    it('should detect DOWN breakout when close < lowest low of lookback', () => {
      const candles = makeBreakoutCandles('DOWN', 20)
      const result = detectBreakout(candles, 20)
      expect(result).toBe('DOWN')
    })

    it('should return null when price is within range', () => {
      // All candles have same range, last one is within it
      const candles = makeCandles(25)
      const result = detectBreakout(candles, 20)
      expect(result).toBeNull()
    })
  })

  describe('volumeConfirmed', () => {
    it('should return true when latest volume > threshold * average', () => {
      const candles = makeCandles(20, { volume: 100 })
      // Add a candle with very high volume
      candles.push({
        timestamp: Date.now(),
        open: 3450, high: 3455, low: 3445, close: 3450,
        volume: 500, // 5x average → above 2.0 threshold
      })
      expect(volumeConfirmed(candles, 2.0)).toBe(true)
    })

    it('should return false when volume is below threshold', () => {
      const candles = makeCandles(20, { volume: 100 })
      // Add a candle with normal volume
      candles.push({
        timestamp: Date.now(),
        open: 3450, high: 3455, low: 3445, close: 3450,
        volume: 150, // 1.5x average → below 2.0 threshold
      })
      expect(volumeConfirmed(candles, 2.0)).toBe(false)
    })
  })

  describe('onTick', () => {
    it('should emit BUY on upward breakout with volume confirmation', () => {
      const strategy = new MomentumBreakout()
      const candles = makeBreakoutCandles('UP', 20)
      const lastCandle = candles[candles.length - 1]!
      const ctx = makeCtx({ candles, ticker: makeTicker(lastCandle.close) })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')
      expect(buy).toBeDefined()
      expect(buy!.symbol).toBe('ETH-PERP')
      expect(buy!.size).toBe(0.1)
    })

    it('should emit SELL on downward breakout with volume confirmation', () => {
      const strategy = new MomentumBreakout()
      const candles = makeBreakoutCandles('DOWN', 20)
      const lastCandle = candles[candles.length - 1]!
      const ctx = makeCtx({ candles, ticker: makeTicker(lastCandle.close) })

      const decisions = strategy.onTick(ctx)
      const sell = decisions.find(d => d.action === 'SELL')
      expect(sell).toBeDefined()
      expect(sell!.symbol).toBe('ETH-PERP')
    })

    it('should emit HOLD when no breakout detected', () => {
      const strategy = new MomentumBreakout()
      const candles = makeCandles(30)
      const ctx = makeCtx({ candles })

      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should emit HOLD when breakout detected but volume not confirmed', () => {
      const strategy = new MomentumBreakout()
      // Make candles where the last one breaks out in price but with low volume
      const base = 3450
      const rangeCandles: Candle[] = Array.from({ length: 25 }, (_, i) => ({
        timestamp: Date.now() - (25 - i) * 60_000,
        open: base - 2,
        high: base + 10,
        low: base - 10,
        close: base,
        volume: 100,
      }))
      rangeCandles.push({
        timestamp: Date.now(),
        open: base + 5,
        high: base + 20,
        low: base + 5,
        close: base + 15,
        volume: 100, // Same as average → no volume confirmation
      })
      const ctx = makeCtx({ candles: rangeCandles, ticker: makeTicker(base + 15) })

      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should set stopLoss at entry - ATR * atr_multiplier for BUY', () => {
      const strategy = new MomentumBreakout()
      const candles = makeBreakoutCandles('UP', 20)
      const lastCandle = candles[candles.length - 1]!
      const ctx = makeCtx({ candles, ticker: makeTicker(lastCandle.close) })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')!
      const atr = computeATR(candles, 14)
      expect(buy.stopLoss).toBeCloseTo(lastCandle.close - atr * 2.0, 5)
    })

    it('should set stopLoss at entry + ATR * atr_multiplier for SELL', () => {
      const strategy = new MomentumBreakout()
      const candles = makeBreakoutCandles('DOWN', 20)
      const lastCandle = candles[candles.length - 1]!
      const ctx = makeCtx({ candles, ticker: makeTicker(lastCandle.close) })

      const decisions = strategy.onTick(ctx)
      const sell = decisions.find(d => d.action === 'SELL')!
      const atr = computeATR(candles, 14)
      expect(sell.stopLoss).toBeCloseTo(lastCandle.close + atr * 2.0, 5)
    })

    it('should respect max_position limit', () => {
      const strategy = new MomentumBreakout()
      const candles = makeBreakoutCandles('UP', 20)
      const lastCandle = candles[candles.length - 1]!
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
      const ctx = makeCtx({ candles, positions, ticker: makeTicker(lastCandle.close) })

      const decisions = strategy.onTick(ctx)
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should use IOC order type for directional entries', () => {
      const strategy = new MomentumBreakout()
      const candles = makeBreakoutCandles('UP', 20)
      const lastCandle = candles[candles.length - 1]!
      const ctx = makeCtx({ candles, ticker: makeTicker(lastCandle.close) })

      const decisions = strategy.onTick(ctx)
      const buy = decisions.find(d => d.action === 'BUY')!
      expect(buy.orderType).toBe('IOC')
    })
  })
})
