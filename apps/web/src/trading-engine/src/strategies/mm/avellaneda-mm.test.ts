import { AvellanedaMM } from './avellaneda-mm.js'
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

/** Generate candles with controlled log-return stddev */
function makeCandles(closePrices: number[]): Candle[] {
  return closePrices.map((close, i) => ({
    timestamp: Date.now() - (closePrices.length - i) * 60_000,
    open: close * 0.999,
    high: close * 1.002,
    low: close * 0.998,
    close,
    volume: 100,
  }))
}

/** Candles with very small variation → quiet vol */
function makeQuietCandles(): Candle[] {
  const base = 3450
  return Array.from({ length: 20 }, (_, i) => ({
    timestamp: Date.now() - i * 60_000,
    open: base,
    high: base + 0.1,
    low: base - 0.1,
    close: base + (i % 2 === 0 ? 0.05 : -0.05),
    volume: 100,
  }))
}

/** Candles with high variation → volatile vol */
function makeVolatileCandles(): Candle[] {
  const base = 3450
  return Array.from({ length: 20 }, (_, i) => ({
    timestamp: Date.now() - i * 60_000,
    open: base,
    high: base + 50,
    low: base - 50,
    close: base + (i % 2 === 0 ? 40 : -40),
    volume: 1000,
  }))
}

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'avellaneda-mm',
    symbols: ['ETH-PERP'],
    params: {
      gamma: 0.1,
      order_size: 0.1,
      max_position: 1.0,
      time_horizon: 1,
      ...params,
    },
  }
}

const mockAdapter = {
  name: 'mock',
  getTicker: async () => makeTicker(3450),
  getOrderBook: async () => makeOrderBook(),
  getCandles: async () => makeQuietCandles(),
  getBalances: async () => [{ currency: 'USDT', available: 7500, total: 10_000, unrealizedPnl: 0 }],
  getPositions: async () => [],
  placeOrder: async () => ({ orderId: '1', status: 'FILLED' as const, filledSize: 0.1, filledPrice: 3450, timestamp: Date.now() }),
  cancelOrder: async () => {},
  cancelAllOrders: async () => {},
  setStopLoss: async () => ({ orderId: '2', status: 'OPEN' as const, filledSize: 0, filledPrice: 0, timestamp: Date.now() }),
  getOpenOrders: async () => [],
  getExchangeInfo: async () => ({ name: 'mock', testnet: true, supportedSymbols: ['ETH-PERP'], minOrderSizes: {}, tickSizes: {} }),
}

function makeCtx(
  positions: Position[] = [],
  params: Record<string, number | string | boolean> = {},
  candles: Candle[] = makeQuietCandles(),
): TickContext {
  return {
    adapter: mockAdapter,
    positions,
    balances: [{ currency: 'USDT', available: 7500, total: 10_000, unrealizedPnl: 0 }],
    ticker: makeTicker(3450),
    orderBook: makeOrderBook(),
    candles,
    config: makeConfig(params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

describe('AvellanedaMM', () => {
  it('should compute reservation price adjusted for inventory (skewed below mid when long)', () => {
    // With long position and gamma > 0, reservation_price < mid
    const strategy = new AvellanedaMM()
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

    const decisions = strategy.onTick(makeCtx(positions, { gamma: 0.5, order_size: 0.1, max_position: 2.0 }))
    const buy = decisions.find(d => d.action === 'BUY')!

    // reservation_price = mid - q * gamma * sigma^2 * T → with q=0.5, gamma=0.5, skewed down
    // bid = reservation_price - spread/2, so bid < mid - spread/2
    // We just verify bid price is lower than mid
    expect(buy).toBeDefined()
    // stopLoss stores the quoted price; it should be below mid
    expect(buy.stopLoss).toBeLessThan(3450)
  })

  it('should emit both BUY and SELL when no position', () => {
    const strategy = new AvellanedaMM()
    const decisions = strategy.onTick(makeCtx())

    const buy = decisions.find(d => d.action === 'BUY')
    const sell = decisions.find(d => d.action === 'SELL')
    expect(buy).toBeDefined()
    expect(sell).toBeDefined()
  })

  it('should produce larger inventory skew with higher gamma when sigma is meaningful', () => {
    // Higher gamma = stronger risk aversion = bigger skew of reservation price.
    // The A-S formula shifts reservation_price by q * gamma * sigma^2 * T.
    // At high sigma, higher gamma causes a measurably larger shift.
    // We use candles with sigma ≈ 1.0 (normalized) by passing close prices
    // that vary by the full price range each step.
    const strategy = new AvellanedaMM()

    // Create candles where sigma ≈ 1.0 by large alternating swings
    // log(6900/3450) ≈ 0.693, log(3450/6900) ≈ -0.693 → sigma ≈ 0.693
    const highSigmaCandles = makeCandles([
      3450, 6900, 3450, 6900, 3450, 6900, 3450, 6900, 3450, 6900,
      3450, 6900, 3450, 6900, 3450, 6900, 3450, 6900, 3450, 6900,
    ])

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

    const lowGamma = strategy.onTick(makeCtx(longPos, { gamma: 0.1, order_size: 0.1, max_position: 3.0 }, highSigmaCandles))
    const highGamma = strategy.onTick(makeCtx(longPos, { gamma: 5.0, order_size: 0.1, max_position: 3.0 }, highSigmaCandles))

    const bidLow = lowGamma.find(d => d.action === 'BUY')!.stopLoss!
    const bidHigh = highGamma.find(d => d.action === 'BUY')!.stopLoss!

    // Higher gamma causes a larger reservation price shift downward for long inventory
    // So bid should be lower with high gamma
    expect(bidHigh).toBeLessThan(bidLow)
  })

  it('should skew quotes based on position (long position → lower reservation price)', () => {
    const strategy = new AvellanedaMM()
    const candles = makeQuietCandles()

    // No position
    const noPos = strategy.onTick(makeCtx([], { gamma: 0.5, order_size: 0.1, max_position: 3.0 }, candles))
    // Long position
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
    const withLong = strategy.onTick(makeCtx(longPos, { gamma: 0.5, order_size: 0.1, max_position: 3.0 }, candles))

    const noBid = noPos.find(d => d.action === 'BUY')!.stopLoss!
    const longBid = withLong.find(d => d.action === 'BUY')!.stopLoss!

    // Long inventory pushes reservation price down → bid price lower
    expect(longBid).toBeLessThan(noBid)
  })

  it('should amplify spread during high volatility (volatile candles → wider spread)', () => {
    const strategy = new AvellanedaMM()
    const params = { gamma: 0.1, order_size: 0.1, max_position: 2.0 }

    const quietDecisions = strategy.onTick(makeCtx([], params, makeQuietCandles()))
    const volatileDecisions = strategy.onTick(makeCtx([], params, makeVolatileCandles()))

    const qBid = quietDecisions.find(d => d.action === 'BUY')!.stopLoss!
    const qAsk = quietDecisions.find(d => d.action === 'SELL')!.stopLoss!
    const vBid = volatileDecisions.find(d => d.action === 'BUY')!.stopLoss!
    const vAsk = volatileDecisions.find(d => d.action === 'SELL')!.stopLoss!

    const quietSpread = qAsk - qBid
    const volatileSpread = vAsk - vBid

    expect(volatileSpread).toBeGreaterThan(quietSpread)
  })

  it('should apply drawdown amplifier (spread × 1.5) when unrealizedPnl < 0', () => {
    const strategy = new AvellanedaMM()
    const candles = makeQuietCandles()
    const params = { gamma: 0.1, order_size: 0.1, max_position: 2.0 }

    // Position with negative PnL
    const losingPos: Position[] = [{
      symbol: 'ETH-PERP',
      side: 'LONG',
      size: 0.5,
      entryPrice: 3500,
      markPrice: 3450,
      unrealizedPnl: -25,
      leverage: 10,
      liquidationPrice: 3000,
    }]

    // Position with positive PnL (same size to isolate spread effect)
    const winningPos: Position[] = [{
      symbol: 'ETH-PERP',
      side: 'LONG',
      size: 0.5,
      entryPrice: 3400,
      markPrice: 3450,
      unrealizedPnl: 25,
      leverage: 10,
      liquidationPrice: 3000,
    }]

    const losing = strategy.onTick(makeCtx(losingPos, params, candles))
    const winning = strategy.onTick(makeCtx(winningPos, params, candles))

    const loseBid = losing.find(d => d.action === 'BUY')!.stopLoss!
    const loseAsk = losing.find(d => d.action === 'SELL')!.stopLoss!
    const winBid = winning.find(d => d.action === 'BUY')!.stopLoss!
    const winAsk = winning.find(d => d.action === 'SELL')!.stopLoss!

    const loseSpread = loseAsk - loseBid
    const winSpread = winAsk - winBid

    expect(loseSpread).toBeGreaterThan(winSpread)
  })

  it('should use ALO order type', () => {
    const strategy = new AvellanedaMM()
    const decisions = strategy.onTick(makeCtx())
    decisions.filter(d => d.action !== 'HOLD').forEach(d => {
      expect(d.orderType).toBe('ALO')
    })
  })

  it('should use configured order_size', () => {
    const strategy = new AvellanedaMM()
    const decisions = strategy.onTick(makeCtx([], { gamma: 0.1, order_size: 0.5, max_position: 2.0 }))
    decisions.filter(d => d.action !== 'HOLD').forEach(d => {
      expect(d.size).toBe(0.5)
    })
  })
})
