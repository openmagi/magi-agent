import { describe, it, expect, beforeEach, jest } from '@jest/globals'
import { LlmCustom } from './llm-custom.js'
import type { TickContext, StrategyConfig, Ticker, OrderBook, Candle, Balance, Position } from '../types.js'

// --- Mock fetch ---
type FetchFn = typeof fetch
const mockFetch = jest.fn<FetchFn>()

function makeTicker(mid = 3450): Ticker {
  return {
    symbol: 'ETH-PERP',
    mid,
    bid: mid - 0.5,
    ask: mid + 0.5,
    lastPrice: mid,
    volume24h: 1_200_000_000,
    openInterest: 500_000_000,
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

function makeConfig(params: Record<string, number | string | boolean> = {}): StrategyConfig {
  return {
    name: 'llm-custom',
    symbols: ['ETH-PERP'],
    strategyPrompt: 'You are a trading assistant. Make conservative decisions.',
    params: {
      max_position_pct: 10,
      max_leverage: 10,
      llm_endpoint: 'http://chat-proxy/v1/chat/completions',
      ...params,
    },
  }
}

const mockAdapter = {
  name: 'mock',
  getTicker: async () => makeTicker(),
  getOrderBook: async () => makeOrderBook(),
  getCandles: async () => makeCandles(),
  getBalances: async () => [{ currency: 'USDT', available: 7500, total: 10_000, unrealizedPnl: 25 }],
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
  balances: Balance[] = [{ currency: 'USDT', available: 7500, total: 10_000, unrealizedPnl: 25 }],
  params: Record<string, number | string | boolean> = {},
): TickContext {
  return {
    adapter: mockAdapter,
    positions,
    balances,
    ticker: makeTicker(),
    orderBook: makeOrderBook(),
    candles: makeCandles(),
    config: makeConfig(params),
    tickNumber: 1,
    timestamp: Date.now(),
  }
}

function makeStrategy(): LlmCustom {
  return new LlmCustom(mockFetch as unknown as typeof fetch)
}

function mockLlmResponse(content: object): void {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      choices: [{ message: { content: JSON.stringify(content) } }],
    }),
  } as unknown as Response)
}

function mockLlmError(): void {
  mockFetch.mockRejectedValueOnce(new Error('Network error'))
}

beforeEach(() => {
  mockFetch.mockClear()
})

describe('LlmCustom', () => {
  describe('buildMarketSnapshot', () => {
    it('should include symbol, prices, and volume in snapshot', () => {
      const strategy = makeStrategy()
      const ctx = makeCtx()
      const snapshot = strategy.buildMarketSnapshot(ctx)

      expect(snapshot).toContain('ETH-PERP')
      expect(snapshot).toContain('3,450')
      expect(snapshot).toContain('Bid')
      expect(snapshot).toContain('Ask')
    })

    it('should include position details and PnL when position exists', () => {
      const strategy = makeStrategy()
      const positions: Position[] = [{
        symbol: 'ETH-PERP',
        side: 'LONG',
        size: 0.5,
        entryPrice: 3400,
        markPrice: 3450,
        unrealizedPnl: 25.25,
        leverage: 10,
        liquidationPrice: 3000,
      }]
      const ctx = makeCtx(positions)
      const snapshot = strategy.buildMarketSnapshot(ctx)

      expect(snapshot).toContain('LONG')
      expect(snapshot).toContain('0.5')
      expect(snapshot).toContain('3,400')
      expect(snapshot).toContain('25.25')
    })

    it('should include account equity and available balance', () => {
      const strategy = makeStrategy()
      const balances: Balance[] = [{ currency: 'USDT', available: 7500, total: 10_000, unrealizedPnl: 25.25 }]
      const snapshot = strategy.buildMarketSnapshot(makeCtx([], balances))

      expect(snapshot).toContain('10,000')
      expect(snapshot).toContain('7,500')
    })
  })

  describe('onTick', () => {
    it('should parse structured JSON response into StrategyDecision', async () => {
      const strategy = makeStrategy()
      mockLlmResponse({
        action: 'BUY',
        symbol: 'ETH-PERP',
        size: 0.1,
        reason: 'RSI oversold',
        stopLoss: 3400,
        takeProfit: 3600,
      })

      const decisions = await strategy.onTick(makeCtx())
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('BUY')
      expect(decisions[0]!.symbol).toBe('ETH-PERP')
      expect(decisions[0]!.size).toBe(0.1)
      expect(decisions[0]!.reason).toContain('RSI oversold')
      expect(decisions[0]!.stopLoss).toBe(3400)
      expect(decisions[0]!.takeProfit).toBe(3600)
    })

    it('should return HOLD on LLM fetch error', async () => {
      const strategy = makeStrategy()
      mockLlmError()

      const decisions = await strategy.onTick(makeCtx())
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should return HOLD when LLM returns non-OK response', async () => {
      const strategy = makeStrategy()
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ error: 'Internal error' }),
      } as unknown as Response)

      const decisions = await strategy.onTick(makeCtx())
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should return HOLD when LLM returns malformed JSON', async () => {
      const strategy = makeStrategy()
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          choices: [{ message: { content: 'not valid json at all' } }],
        }),
      } as unknown as Response)

      const decisions = await strategy.onTick(makeCtx())
      expect(decisions).toHaveLength(1)
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should reject decisions exceeding maxPositionPct (size > 10% of equity at price)', async () => {
      const strategy = makeStrategy()
      // equity = 10_000, price = 3450, 10% = 1000 USDT → max size = 1000/3450 ≈ 0.29
      // Request size = 5.0 → worth 17250 USDT >> 1000 USDT limit
      mockLlmResponse({
        action: 'BUY',
        symbol: 'ETH-PERP',
        size: 5.0,
        reason: 'All in',
      })

      const decisions = await strategy.onTick(makeCtx())
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should reject all-in orders (size > 50% of equity at current price)', async () => {
      const strategy = makeStrategy()
      // equity = 10_000, price = 3450, 50% = 5000 USDT → max all-in size ≈ 1.45
      // Request size = 2.0 → worth 6900 USDT > 5000 USDT
      mockLlmResponse({
        action: 'BUY',
        symbol: 'ETH-PERP',
        size: 2.0,
        reason: 'Half in',
      })

      const decisions = await strategy.onTick(
        makeCtx([], [{ currency: 'USDT', available: 7500, total: 10_000, unrealizedPnl: 25 }], { max_position_pct: 50 })
      )
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should accept valid small orders within limits', async () => {
      const strategy = makeStrategy()
      mockLlmResponse({
        action: 'BUY',
        symbol: 'ETH-PERP',
        size: 0.02,  // ~69 USDT at 3450, < 10% of 10k
        reason: 'Small entry',
      })

      const decisions = await strategy.onTick(makeCtx())
      expect(decisions[0]!.action).toBe('BUY')
    })

    it('should return HOLD on action=HOLD from LLM', async () => {
      const strategy = makeStrategy()
      mockLlmResponse({
        action: 'HOLD',
        symbol: 'ETH-PERP',
        size: 0,
        reason: 'Market unclear',
      })

      const decisions = await strategy.onTick(makeCtx())
      expect(decisions[0]!.action).toBe('HOLD')
    })

    it('should use GTC order type for LLM decisions', async () => {
      const strategy = makeStrategy()
      mockLlmResponse({
        action: 'SELL',
        symbol: 'ETH-PERP',
        size: 0.05,
        reason: 'Momentum reversal',
      })

      const decisions = await strategy.onTick(makeCtx())
      const decision = decisions.find(d => d.action === 'SELL')
      expect(decision).toBeDefined()
      expect(decision!.orderType).toBe('GTC')
    })

    it('should include confidence in returned decision', async () => {
      const strategy = makeStrategy()
      mockLlmResponse({
        action: 'BUY',
        symbol: 'ETH-PERP',
        size: 0.05,
        reason: 'Trend following',
      })

      const decisions = await strategy.onTick(makeCtx())
      expect(decisions[0]!.confidence).toBeGreaterThanOrEqual(0)
      expect(decisions[0]!.confidence).toBeLessThanOrEqual(100)
    })
  })
})
