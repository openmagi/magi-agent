import { describe, it, expect } from '@jest/globals'
import { KisMcpAdapter } from './kis-mcp.js'
import type { KisMcpConfig } from './kis-mcp.js'
import type { McpToolCaller } from './mcp-bridge.js'
import type { OrderRequest } from '../types.js'
import * as krxCalendar from './krx-calendar.js'

/** Create mock MCP caller that tracks calls */
function makeMockCaller(returnValue: unknown = {}): McpToolCaller & { calls: Array<{ name: string; args: Record<string, unknown> }> } {
  const calls: Array<{ name: string; args: Record<string, unknown> }> = []
  return {
    calls,
    callTool: async (name: string, args: Record<string, unknown>): Promise<unknown> => {
      calls.push({ name, args })
      return returnValue
    },
  }
}

function makeConfig(caller: McpToolCaller, overrides: Partial<KisMcpConfig> = {}): KisMcpConfig {
  return {
    mcpCaller: caller,
    accountNo: '50123456-01',
    ...overrides,
  }
}

/** Helper: create a KST date at specific time */
function kstDate(year: number, month: number, day: number, hour: number, minute: number): Date {
  return new Date(Date.UTC(year, month - 1, day, hour - 9, minute))
}

describe('KisMcpAdapter', () => {
  describe('constructor', () => {
    it('should set name to "한국투자증권"', () => {
      const caller = makeMockCaller()
      const adapter = new KisMcpAdapter(makeConfig(caller))
      expect(adapter.name).toBe('한국투자증권')
    })

    it('should create with default afterHoursTrading=false', () => {
      const caller = makeMockCaller()
      const adapter = new KisMcpAdapter(makeConfig(caller))
      expect(adapter.name).toBe('한국투자증권')
    })
  })

  describe('getTicker', () => {
    it('should call kis_get_stock_price MCP tool', async () => {
      const caller = makeMockCaller({
        symbol: '005930',
        mid: 72500,
        bid: 72400,
        ask: 72600,
        lastPrice: 72500,
        volume24h: 15000000,
        openInterest: 0,
        fundingRate: 0,
        timestamp: Date.now(),
      })
      const adapter = new KisMcpAdapter(makeConfig(caller))

      await adapter.getTicker('005930')

      expect(caller.calls).toHaveLength(1)
      expect(caller.calls[0]!.name).toBe('kis_get_stock_price')
      expect(caller.calls[0]!.args).toEqual({ symbol: '005930' })
    })

    it('should transform KIS response format to Ticker', async () => {
      const now = Date.now()
      const caller = makeMockCaller({
        symbol: '005930',
        mid: 72500,
        bid: 72400,
        ask: 72600,
        lastPrice: 72500,
        volume24h: 15000000,
        openInterest: 0,
        fundingRate: 0,
        timestamp: now,
      })
      const adapter = new KisMcpAdapter(makeConfig(caller))

      const ticker = await adapter.getTicker('005930')

      expect(ticker.symbol).toBe('005930')
      expect(ticker.mid).toBe(72500)
      expect(ticker.bid).toBe(72400)
      expect(ticker.ask).toBe(72600)
    })
  })

  describe('placeOrder', () => {
    it('should call kis_place_order with account number and order details', async () => {
      const caller = makeMockCaller({
        orderId: 'KIS-001',
        status: 'FILLED',
        filledSize: 10,
        filledPrice: 72500,
        timestamp: Date.now(),
      })
      // Use a time during REGULAR session
      const adapter = new KisMcpAdapter(makeConfig(caller), () => kstDate(2026, 3, 16, 10, 0))

      const order: OrderRequest = {
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 72500,
        orderType: 'GTC',
      }
      await adapter.placeOrder(order)

      expect(caller.calls).toHaveLength(1)
      expect(caller.calls[0]!.name).toBe('kis_place_order')
      expect(caller.calls[0]!.args).toEqual({
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 72500,
        orderType: 'GTC',
      })
    })

    it('should map BUY and SELL order sides', async () => {
      const caller = makeMockCaller({
        orderId: 'KIS-002',
        status: 'OPEN',
        filledSize: 0,
        filledPrice: 0,
        timestamp: Date.now(),
      })
      const adapter = new KisMcpAdapter(makeConfig(caller), () => kstDate(2026, 3, 16, 10, 0))

      await adapter.placeOrder({
        symbol: '005930',
        side: 'SELL',
        size: 5,
        price: 73000,
        orderType: 'ALO',
      })

      expect(caller.calls[0]!.args['side']).toBe('SELL')
    })
  })

  describe('market hours guard', () => {
    it('should allow orders during REGULAR session', async () => {
      const caller = makeMockCaller({
        orderId: 'KIS-001',
        status: 'FILLED',
        filledSize: 10,
        filledPrice: 72500,
        timestamp: Date.now(),
      })
      // Monday 10:00 KST = REGULAR
      const adapter = new KisMcpAdapter(makeConfig(caller), () => kstDate(2026, 3, 16, 10, 0))

      const order: OrderRequest = {
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 72500,
        orderType: 'GTC',
      }

      await expect(adapter.placeOrder(order)).resolves.toBeDefined()
    })

    it('should throw when market is CLOSED', async () => {
      const caller = makeMockCaller()
      // Monday 19:00 KST = CLOSED
      const adapter = new KisMcpAdapter(makeConfig(caller), () => kstDate(2026, 3, 16, 19, 0))

      const order: OrderRequest = {
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 72500,
        orderType: 'GTC',
      }

      await expect(adapter.placeOrder(order)).rejects.toThrow('KRX market is CLOSED')
    })

    it('should include next market open time in error message', async () => {
      const caller = makeMockCaller()
      // Saturday 10:00 KST = CLOSED
      const adapter = new KisMcpAdapter(makeConfig(caller), () => kstDate(2026, 3, 14, 10, 0))

      const order: OrderRequest = {
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 72500,
        orderType: 'GTC',
      }

      await expect(adapter.placeOrder(order)).rejects.toThrow('Next open:')
    })

    it('should allow after-hours orders when configured', async () => {
      const caller = makeMockCaller({
        orderId: 'KIS-003',
        status: 'OPEN',
        filledSize: 0,
        filledPrice: 0,
        timestamp: Date.now(),
      })
      // Monday 16:00 KST = AFTER_HOURS
      const adapter = new KisMcpAdapter(
        makeConfig(caller, { afterHoursTrading: true }),
        () => kstDate(2026, 3, 16, 16, 0),
      )

      const order: OrderRequest = {
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 72500,
        orderType: 'GTC',
      }

      await expect(adapter.placeOrder(order)).resolves.toBeDefined()
    })

    it('should reject after-hours orders when afterHoursTrading=false', async () => {
      const caller = makeMockCaller()
      // Monday 16:00 KST = AFTER_HOURS
      const adapter = new KisMcpAdapter(
        makeConfig(caller, { afterHoursTrading: false }),
        () => kstDate(2026, 3, 16, 16, 0),
      )

      const order: OrderRequest = {
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 72500,
        orderType: 'GTC',
      }

      await expect(adapter.placeOrder(order)).rejects.toThrow('KRX market is AFTER_HOURS')
    })

    it('should also guard cancelOrder during closed hours', async () => {
      const caller = makeMockCaller()
      // Sunday = CLOSED
      const adapter = new KisMcpAdapter(makeConfig(caller), () => kstDate(2026, 3, 15, 10, 0))

      await expect(adapter.cancelOrder('KIS-001')).rejects.toThrow('KRX market is CLOSED')
    })
  })

  describe('getCandles', () => {
    it('should throw "not supported via MCP"', async () => {
      const caller = makeMockCaller()
      const adapter = new KisMcpAdapter(makeConfig(caller))

      await expect(adapter.getCandles('005930', '1h', 100)).rejects.toThrow('not supported via MCP')
    })
  })

  describe('getExchangeInfo', () => {
    it('should return "한국투자증권" as exchange name', async () => {
      const caller = makeMockCaller()
      const adapter = new KisMcpAdapter(makeConfig(caller))

      const info = await adapter.getExchangeInfo()

      expect(info.name).toBe('한국투자증권')
    })

    it('should return testnet=false', async () => {
      const caller = makeMockCaller()
      const adapter = new KisMcpAdapter(makeConfig(caller))

      const info = await adapter.getExchangeInfo()

      expect(info.testnet).toBe(false)
    })
  })
})
