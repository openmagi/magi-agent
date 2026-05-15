import { jest, describe, beforeEach, it, expect } from '@jest/globals'
import { OrderManager } from './order-manager.js'
import type {
  StrategyDecision,
  Balance,
  Ticker,
  ExchangeInfo,
  OrderResult,
  OrderRequest,
} from '../types.js'

describe('OrderManager', () => {
  let manager: OrderManager
  const mockPlaceOrder = jest.fn<() => Promise<OrderResult>>()
  const mockCancelOrder = jest.fn<() => Promise<void>>()
  const mockCancelAllOrders = jest.fn<() => Promise<void>>()
  const mockSetStopLoss = jest.fn<() => Promise<OrderResult>>()
  const mockGetOpenOrders = jest.fn()
  const mockGetExchangeInfo = jest.fn()

  const mockAdapter = {
    name: 'mock',
    placeOrder: mockPlaceOrder,
    cancelOrder: mockCancelOrder,
    cancelAllOrders: mockCancelAllOrders,
    setStopLoss: mockSetStopLoss,
    getOpenOrders: mockGetOpenOrders,
    getExchangeInfo: mockGetExchangeInfo,
    getTicker: jest.fn(),
    getOrderBook: jest.fn(),
    getCandles: jest.fn(),
    getBalances: jest.fn(),
    getPositions: jest.fn(),
  }

  const mockTicker: Ticker = {
    symbol: 'BTC-USD',
    mid: 50000,
    bid: 49990,
    ask: 50010,
    lastPrice: 50000,
    volume24h: 1000,
    openInterest: 5000,
    fundingRate: 0.0001,
    timestamp: Date.now(),
  }

  const mockExchangeInfo: ExchangeInfo = {
    name: 'mock',
    testnet: true,
    supportedSymbols: ['BTC-USD', 'ETH-USD'],
    minOrderSizes: { 'BTC-USD': 0.001, 'ETH-USD': 0.01 },
    tickSizes: { 'BTC-USD': 1, 'ETH-USD': 0.1 },
  }

  const filledResult: OrderResult = {
    orderId: 'order-123',
    status: 'FILLED',
    filledSize: 0.1,
    filledPrice: 50000,
    timestamp: Date.now(),
  }

  const rejectedResult: OrderResult = {
    orderId: 'order-456',
    status: 'REJECTED',
    filledSize: 0,
    filledPrice: 0,
    timestamp: Date.now(),
  }

  beforeEach(() => {
    manager = new OrderManager()
    jest.clearAllMocks()
  })

  it('should convert BUY decision to order request', async () => {
    const decision: StrategyDecision = {
      action: 'BUY',
      symbol: 'BTC-USD',
      size: 0.1,
      orderType: 'ALO',
      confidence: 0.8,
      reason: 'Strong uptrend',
    }

    mockPlaceOrder.mockResolvedValue(filledResult)

    const result = await manager.executeDecision(decision, mockAdapter as never, mockTicker)

    expect(result).not.toBeNull()
    expect(mockPlaceOrder).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: 'BTC-USD',
        side: 'BUY',
        size: 0.1,
        price: 49990, // bid for BUY (ALO uses bid)
        orderType: 'ALO',
      })
    )
  })

  it('should return null for HOLD decisions', async () => {
    const decision: StrategyDecision = {
      action: 'HOLD',
      symbol: 'BTC-USD',
      size: 0,
      orderType: 'GTC',
      confidence: 0.5,
      reason: 'No signal',
    }

    const result = await manager.executeDecision(decision, mockAdapter as never, mockTicker)

    expect(result).toBeNull()
    expect(mockPlaceOrder).not.toHaveBeenCalled()
  })

  it('should place ALO order and fallback to GTC on rejection', async () => {
    const order: OrderRequest = {
      symbol: 'BTC-USD',
      side: 'BUY',
      size: 0.1,
      price: 50000,
      orderType: 'ALO',
    }

    const gtcResult: OrderResult = {
      orderId: 'order-gtc-789',
      status: 'OPEN',
      filledSize: 0,
      filledPrice: 0,
      timestamp: Date.now(),
    }

    mockPlaceOrder
      .mockResolvedValueOnce(rejectedResult)  // ALO rejected
      .mockResolvedValueOnce(gtcResult)        // GTC succeeds

    const result = await manager.placeWithFallback(order, mockAdapter as never)

    expect(mockPlaceOrder).toHaveBeenCalledTimes(2)
    expect(mockPlaceOrder).toHaveBeenNthCalledWith(1, expect.objectContaining({ orderType: 'ALO' }))
    expect(mockPlaceOrder).toHaveBeenNthCalledWith(2, expect.objectContaining({ orderType: 'GTC' }))
    expect(result).toEqual(gtcResult)
  })

  it('should cancel all orders for a symbol', async () => {
    mockCancelAllOrders.mockResolvedValue(undefined)

    await manager.cancelStopLoss('BTC-USD', mockAdapter as never)

    expect(mockCancelAllOrders).toHaveBeenCalledWith('BTC-USD')
  })

  it('should calculate position size from percentage', () => {
    const balances: Balance[] = [
      { currency: 'USD', available: 8000, total: 10000, unrealizedPnl: 0 },
      { currency: 'BTC', available: 0, total: 0, unrealizedPnl: 0 },
    ]

    // equity = 10000, pct = 10, price = 50000, leverage = 10
    // notional = 10000 * (10/100) = 1000
    // size = (1000 * 10) / 50000 = 0.2
    const size = manager.calcSize(balances, 10, 50000, 10)
    expect(size).toBeCloseTo(0.2, 8)
  })

  it('should enforce minimum order size', () => {
    // size above minimum → returned as-is
    const validSize = manager.enforceMinSize(0.01, 'BTC-USD', mockExchangeInfo)
    expect(validSize).toBe(0.01)
  })

  it('should return null when size below minimum', () => {
    // BTC-USD min = 0.001, so 0.0005 is below minimum
    const tooSmall = manager.enforceMinSize(0.0005, 'BTC-USD', mockExchangeInfo)
    expect(tooSmall).toBeNull()
  })

  it('should set exchange stop loss', async () => {
    const slResult: OrderResult = {
      orderId: 'sl-order-001',
      status: 'OPEN',
      filledSize: 0,
      filledPrice: 0,
      timestamp: Date.now(),
    }

    mockSetStopLoss.mockResolvedValue(slResult)

    const result = await manager.syncStopLoss('BTC-USD', 'BUY', 48000, 0.1, mockAdapter as never)

    expect(mockSetStopLoss).toHaveBeenCalledWith('BTC-USD', 'BUY', 48000, 0.1)
    expect(result).toEqual(slResult)
  })

  it('should use ask price for SELL ALO orders', async () => {
    const decision: StrategyDecision = {
      action: 'SELL',
      symbol: 'BTC-USD',
      size: 0.1,
      orderType: 'ALO',
      confidence: 0.8,
      reason: 'Bearish signal',
    }

    mockPlaceOrder.mockResolvedValue(filledResult)

    await manager.executeDecision(decision, mockAdapter as never, mockTicker)

    expect(mockPlaceOrder).toHaveBeenCalledWith(
      expect.objectContaining({
        side: 'SELL',
        price: 50010, // ask for SELL (ALO posts on ask side)
      })
    )
  })

  it('should use mid price for non-ALO orders', async () => {
    const decision: StrategyDecision = {
      action: 'BUY',
      symbol: 'BTC-USD',
      size: 0.1,
      orderType: 'GTC',
      confidence: 0.8,
      reason: 'Signal',
    }

    mockPlaceOrder.mockResolvedValue(filledResult)

    await manager.executeDecision(decision, mockAdapter as never, mockTicker)

    expect(mockPlaceOrder).toHaveBeenCalledWith(
      expect.objectContaining({
        price: 50000, // mid for GTC
      })
    )
  })
})
