import { McpBridgeAdapter } from './mcp-bridge.js'
import type { McpToolCaller, McpToolMapping, McpBridgeConfig } from './mcp-bridge.js'
import type { OrderRequest } from '../types.js'

function makeMapping(): McpToolMapping {
  return {
    getTicker: 'clober_get_ticker',
    getOrderBook: 'clober_get_orderbook',
    getBalances: 'clober_get_balances',
    getPositions: 'clober_get_positions',
    placeOrder: 'clober_place_order',
    cancelOrder: 'clober_cancel_order',
    getOpenOrders: 'clober_get_open_orders',
  }
}

function makeConfig(overrides: Partial<McpBridgeConfig> = {}): McpBridgeConfig {
  return {
    exchangeName: 'clober-testnet',
    toolMapping: makeMapping(),
    supportedSymbols: ['ETH-PERP', 'BTC-PERP'],
    ...overrides,
  }
}

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

describe('McpBridgeAdapter', () => {
  describe('constructor', () => {
    it('should set name from config', () => {
      const caller = makeMockCaller()
      const adapter = new McpBridgeAdapter(caller, makeConfig({ exchangeName: 'fly-trade' }))
      expect(adapter.name).toBe('fly-trade')
    })

    it('should store tool mapping', () => {
      const caller = makeMockCaller()
      const adapter = new McpBridgeAdapter(caller, makeConfig())
      // Verify it works by calling a method (which uses the mapping)
      expect(adapter.name).toBe('clober-testnet')
    })
  })

  describe('getTicker', () => {
    it('should call mapped MCP tool with symbol argument', async () => {
      const caller = makeMockCaller({
        symbol: 'ETH-PERP',
        mid: 3450,
        bid: 3449.5,
        ask: 3450.5,
        lastPrice: 3450,
        volume24h: 1000000,
        openInterest: 500000,
        fundingRate: 0.0001,
        timestamp: Date.now(),
      })
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      await adapter.getTicker('ETH-PERP')

      expect(caller.calls).toHaveLength(1)
      expect(caller.calls[0]!.name).toBe('clober_get_ticker')
      expect(caller.calls[0]!.args).toEqual({ symbol: 'ETH-PERP' })
    })

    it('should transform MCP response to Ticker type', async () => {
      const now = Date.now()
      const caller = makeMockCaller({
        symbol: 'ETH-PERP',
        mid: 3450,
        bid: 3449.5,
        ask: 3450.5,
        lastPrice: 3450,
        volume24h: 1000000,
        openInterest: 500000,
        fundingRate: 0.0001,
        timestamp: now,
      })
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      const ticker = await adapter.getTicker('ETH-PERP')

      expect(ticker.symbol).toBe('ETH-PERP')
      expect(ticker.mid).toBe(3450)
      expect(ticker.bid).toBe(3449.5)
      expect(ticker.ask).toBe(3450.5)
      expect(ticker.lastPrice).toBe(3450)
      expect(ticker.volume24h).toBe(1000000)
      expect(ticker.openInterest).toBe(500000)
      expect(ticker.fundingRate).toBe(0.0001)
    })

    it('should throw on MCP tool error', async () => {
      const caller: McpToolCaller = {
        callTool: async () => {
          throw new Error('MCP tool failed')
        },
      }
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      await expect(adapter.getTicker('ETH-PERP')).rejects.toThrow('MCP tool failed')
    })
  })

  describe('getOrderBook', () => {
    it('should call mapped MCP tool with symbol and depth', async () => {
      const caller = makeMockCaller({
        symbol: 'ETH-PERP',
        bids: [{ price: 3449.5, size: 1.0 }],
        asks: [{ price: 3450.5, size: 1.0 }],
        timestamp: Date.now(),
      })
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      await adapter.getOrderBook('ETH-PERP', 10)

      expect(caller.calls).toHaveLength(1)
      expect(caller.calls[0]!.name).toBe('clober_get_orderbook')
      expect(caller.calls[0]!.args).toEqual({ symbol: 'ETH-PERP', depth: 10 })
    })

    it('should transform response to OrderBook type', async () => {
      const caller = makeMockCaller({
        symbol: 'ETH-PERP',
        bids: [{ price: 3449.5, size: 1.0 }],
        asks: [{ price: 3450.5, size: 2.0 }],
        timestamp: 123456,
      })
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      const ob = await adapter.getOrderBook('ETH-PERP')

      expect(ob.symbol).toBe('ETH-PERP')
      expect(ob.bids).toHaveLength(1)
      expect(ob.bids[0]!.price).toBe(3449.5)
      expect(ob.asks[0]!.size).toBe(2.0)
    })
  })

  describe('placeOrder', () => {
    it('should call mapped MCP tool with order details', async () => {
      const caller = makeMockCaller({
        orderId: 'ord-123',
        status: 'FILLED',
        filledSize: 0.1,
        filledPrice: 3450,
        timestamp: Date.now(),
      })
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      const order: OrderRequest = {
        symbol: 'ETH-PERP',
        side: 'BUY',
        size: 0.1,
        price: 3450,
        orderType: 'GTC',
      }
      await adapter.placeOrder(order)

      expect(caller.calls).toHaveLength(1)
      expect(caller.calls[0]!.name).toBe('clober_place_order')
      expect(caller.calls[0]!.args).toEqual({
        symbol: 'ETH-PERP',
        side: 'BUY',
        size: 0.1,
        price: 3450,
        orderType: 'GTC',
      })
    })

    it('should transform response to OrderResult type', async () => {
      const caller = makeMockCaller({
        orderId: 'ord-123',
        status: 'FILLED',
        filledSize: 0.1,
        filledPrice: 3450,
        timestamp: 123456,
      })
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      const order: OrderRequest = {
        symbol: 'ETH-PERP',
        side: 'BUY',
        size: 0.1,
        price: 3450,
        orderType: 'GTC',
      }
      const result = await adapter.placeOrder(order)

      expect(result.orderId).toBe('ord-123')
      expect(result.status).toBe('FILLED')
      expect(result.filledSize).toBe(0.1)
    })
  })

  describe('cancelOrder', () => {
    it('should call mapped MCP tool with orderId', async () => {
      const caller = makeMockCaller()
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      await adapter.cancelOrder('ord-456')

      expect(caller.calls).toHaveLength(1)
      expect(caller.calls[0]!.name).toBe('clober_cancel_order')
      expect(caller.calls[0]!.args).toEqual({ orderId: 'ord-456' })
    })
  })

  describe('getBalances', () => {
    it('should call mapped MCP tool and return Balance[]', async () => {
      const caller = makeMockCaller([
        { currency: 'USDT', available: 5000, total: 10000, unrealizedPnl: 0 },
      ])
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      const balances = await adapter.getBalances()

      expect(caller.calls).toHaveLength(1)
      expect(caller.calls[0]!.name).toBe('clober_get_balances')
      expect(balances).toHaveLength(1)
      expect(balances[0]!.currency).toBe('USDT')
      expect(balances[0]!.available).toBe(5000)
    })
  })

  describe('getExchangeInfo', () => {
    it('should return configured supported symbols', async () => {
      const caller = makeMockCaller()
      const adapter = new McpBridgeAdapter(caller, makeConfig({
        supportedSymbols: ['ETH-PERP', 'BTC-PERP', 'SOL-PERP'],
      }))

      const info = await adapter.getExchangeInfo()
      expect(info.supportedSymbols).toEqual(['ETH-PERP', 'BTC-PERP', 'SOL-PERP'])
    })

    it('should return exchange name from config', async () => {
      const caller = makeMockCaller()
      const adapter = new McpBridgeAdapter(caller, makeConfig({ exchangeName: 'fly-trade' }))

      const info = await adapter.getExchangeInfo()
      expect(info.name).toBe('fly-trade')
    })
  })

  describe('unsupported methods', () => {
    it('getCandles should throw "not supported via MCP"', async () => {
      const caller = makeMockCaller()
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      await expect(adapter.getCandles('ETH-PERP', '1h', 100)).rejects.toThrow('not supported via MCP')
    })

    it('setStopLoss should throw "not supported via MCP"', async () => {
      const caller = makeMockCaller()
      const adapter = new McpBridgeAdapter(caller, makeConfig())

      await expect(adapter.setStopLoss('ETH-PERP', 'BUY', 3400, 0.1)).rejects.toThrow('not supported via MCP')
    })
  })
})
