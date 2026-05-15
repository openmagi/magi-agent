import { describe, it, expect, jest } from '@jest/globals'
import { HyperliquidAdapter } from './hyperliquid.js'
import type { OrderRequest } from '../types.js'

// ── helpers ─────────────────────────────────────────────────────────────────

const FAKE_PRIVATE_KEY =
  '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80'
const WALLET_ADDRESS = '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266'

interface FakeResponse {
  ok: boolean
  status: number
  json: () => Promise<unknown>
  text: () => Promise<string>
}

function fakeResponse(body: unknown, status = 200): FakeResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type FetchMock = jest.Mock<(...args: any[]) => Promise<FakeResponse>>

function makeFetch(body: unknown, status = 200): FetchMock {
  return jest.fn<(...args: unknown[]) => Promise<FakeResponse>>().mockResolvedValue(fakeResponse(body, status)) as FetchMock
}

function makeAdapter(fetchMock: FetchMock): HyperliquidAdapter {
  const adapter = new HyperliquidAdapter({
    testnet: true,
    privateKey: FAKE_PRIVATE_KEY,
    walletAddress: WALLET_ADDRESS,
  })
  ;(adapter as unknown as { _fetch: FetchMock })._fetch = fetchMock
  return adapter
}

function getCallBody(fetchMock: FetchMock, callIndex = 0): Record<string, unknown> {
  const call = fetchMock.mock.calls[callIndex] as unknown[]
  const init = call[1] as RequestInit
  return JSON.parse(init.body as string) as Record<string, unknown>
}

// ── getTicker ────────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.getTicker', () => {
  it('returns a Ticker for ETH-PERP', async () => {
    const allMids = { ETH: '3450.5', BTC: '67000.0' }
    const ctxResponse = {
      mids: { ETH: '3450.5' },
      funding: { ETH: { fundingRate: '0.0001' } },
    }
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(allMids))
      .mockResolvedValueOnce(fakeResponse(ctxResponse)) as FetchMock
    const adapter = makeAdapter(fetch)
    const ticker = await adapter.getTicker('ETH-PERP')

    expect(ticker.symbol).toBe('ETH-PERP')
    expect(ticker.mid).toBeCloseTo(3450.5)
    expect(ticker.bid).toBeGreaterThan(0)
    expect(ticker.ask).toBeGreaterThan(ticker.bid)
    expect(ticker.lastPrice).toBeCloseTo(3450.5)
    expect(typeof ticker.timestamp).toBe('number')
  })

  it('throws for unknown symbol', async () => {
    // allMids contains ETH but not AAPL; second call returns empty object
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse({ ETH: '3450.0' }))
      .mockResolvedValueOnce(fakeResponse({})) as FetchMock
    const adapter = makeAdapter(fetch)
    await expect(adapter.getTicker('AAPL-PERP')).rejects.toThrow('Symbol not found')
  })
})

// ── getOrderBook ─────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.getOrderBook', () => {
  it('parses bids and asks from l2Book response', async () => {
    const l2Book = {
      levels: [
        [
          { px: '3449.0', sz: '1.5', n: 3 },
          { px: '3448.0', sz: '2.0', n: 2 },
        ],
        [
          { px: '3451.0', sz: '1.0', n: 1 },
          { px: '3452.0', sz: '3.5', n: 4 },
        ],
      ],
    }
    const fetch = makeFetch(l2Book)
    const adapter = makeAdapter(fetch)
    const book = await adapter.getOrderBook('ETH-PERP', 10)

    expect(book.symbol).toBe('ETH-PERP')
    expect(book.bids).toHaveLength(2)
    expect(book.asks).toHaveLength(2)
    expect(book.bids[0]).toEqual({ price: 3449.0, size: 1.5 })
    expect(book.asks[0]).toEqual({ price: 3451.0, size: 1.0 })
    expect(typeof book.timestamp).toBe('number')
  })

  it('sends the correct coin in the request body', async () => {
    const l2Book = { levels: [[], []] }
    const fetch = makeFetch(l2Book)
    const adapter = makeAdapter(fetch)
    await adapter.getOrderBook('BTC-PERP')

    const body = getCallBody(fetch)
    expect(body['coin']).toBe('BTC')
  })
})

// ── getCandles ───────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.getCandles', () => {
  it('parses candle snapshot correctly', async () => {
    const rawCandles = [
      { t: 1700000000000, o: '3400.0', h: '3500.0', l: '3350.0', c: '3450.0', v: '1000.0' },
      { t: 1700003600000, o: '3450.0', h: '3480.0', l: '3420.0', c: '3460.0', v: '800.0' },
    ]
    const fetch = makeFetch(rawCandles)
    const adapter = makeAdapter(fetch)
    const candles = await adapter.getCandles('ETH-PERP', '1h', 2)

    expect(candles).toHaveLength(2)
    expect(candles[0]).toEqual({
      timestamp: 1700000000000,
      open: 3400.0,
      high: 3500.0,
      low: 3350.0,
      close: 3450.0,
      volume: 1000.0,
    })
  })

  it('requests the correct interval and coin', async () => {
    const fetch = makeFetch([])
    const adapter = makeAdapter(fetch)
    await adapter.getCandles('SOL-PERP', '15m', 50)

    const body = getCallBody(fetch)
    expect(body['coin']).toBe('SOL')
    expect(body['interval']).toBe('15m')
    expect(body['type']).toBe('candleSnapshot')
  })
})

// ── getBalances ──────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.getBalances', () => {
  it('parses clearinghouseState balances', async () => {
    const state = {
      marginSummary: {
        accountValue: '10500.0',
        totalMarginUsed: '500.0',
      },
      crossMaintenanceMarginUsed: '0',
      withdrawable: '10000.0',
      assetPositions: [],
    }
    const fetch = makeFetch(state)
    const adapter = makeAdapter(fetch)
    const balances = await adapter.getBalances()

    expect(balances).toHaveLength(1)
    expect(balances[0]!.currency).toBe('USDC')
    expect(balances[0]!.total).toBeCloseTo(10500.0)
    expect(balances[0]!.available).toBeCloseTo(10000.0)
  })
})

// ── getPositions ─────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.getPositions', () => {
  it('parses positions from clearinghouseState', async () => {
    const state = {
      marginSummary: { accountValue: '10500.0', totalMarginUsed: '500.0' },
      withdrawable: '10000.0',
      assetPositions: [
        {
          position: {
            coin: 'ETH',
            szi: '1.5',
            entryPx: '3400.0',
            positionValue: '5175.0',
            unrealizedPnl: '75.0',
            leverage: { value: '10' },
            liquidationPx: '3200.0',
          },
        },
      ],
    }
    const fetch = makeFetch(state)
    const adapter = makeAdapter(fetch)
    const positions = await adapter.getPositions()

    expect(positions).toHaveLength(1)
    const pos = positions[0]!
    expect(pos.symbol).toBe('ETH-PERP')
    expect(pos.side).toBe('LONG')
    expect(pos.size).toBeCloseTo(1.5)
    expect(pos.entryPrice).toBeCloseTo(3400.0)
    expect(pos.unrealizedPnl).toBeCloseTo(75.0)
    expect(pos.leverage).toBeCloseTo(10)
    expect(pos.liquidationPrice).toBeCloseTo(3200.0)
  })

  it('sets side SHORT when szi is negative', async () => {
    const state = {
      marginSummary: { accountValue: '10000.0', totalMarginUsed: '0' },
      withdrawable: '10000.0',
      assetPositions: [
        {
          position: {
            coin: 'BTC',
            szi: '-0.5',
            entryPx: '67000.0',
            positionValue: '33500.0',
            unrealizedPnl: '-200.0',
            leverage: { value: '5' },
            liquidationPx: null,
          },
        },
      ],
    }
    const fetch = makeFetch(state)
    const adapter = makeAdapter(fetch)
    const positions = await adapter.getPositions()

    expect(positions[0]!.side).toBe('SHORT')
    expect(positions[0]!.size).toBeCloseTo(0.5) // absolute value
    expect(positions[0]!.liquidationPrice).toBeNull()
  })
})

// ── placeOrder ───────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.placeOrder', () => {
  it('places a GTC order and returns OrderResult', async () => {
    const exchangeResponse = {
      status: 'ok',
      response: {
        type: 'order',
        data: {
          statuses: [
            { resting: { oid: 12345 } },
          ],
        },
      },
    }
    const fetch = makeFetch(exchangeResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'ETH-PERP',
      side: 'BUY',
      size: 0.1,
      price: 3400.0,
      orderType: 'GTC',
    }
    const result = await adapter.placeOrder(order)

    expect(result.orderId).toBe('12345')
    expect(result.status).toBe('OPEN')
    expect(result.filledSize).toBe(0)
    expect(typeof result.timestamp).toBe('number')
  })

  it('returns FILLED status when order is filled', async () => {
    const exchangeResponse = {
      status: 'ok',
      response: {
        type: 'order',
        data: {
          statuses: [
            { filled: { oid: 99, totalSz: '0.1', avgPx: '3401.0' } },
          ],
        },
      },
    }
    const fetch = makeFetch(exchangeResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'ETH-PERP',
      side: 'BUY',
      size: 0.1,
      price: 3400.0,
      orderType: 'IOC',
    }
    const result = await adapter.placeOrder(order)

    expect(result.orderId).toBe('99')
    expect(result.status).toBe('FILLED')
    expect(result.filledSize).toBeCloseTo(0.1)
    expect(result.filledPrice).toBeCloseTo(3401.0)
  })

  it('throws when HL returns error status', async () => {
    const errorResponse = { status: 'err', response: 'Order rejected: insufficient margin' }
    const fetch = makeFetch(errorResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'ETH-PERP',
      side: 'BUY',
      size: 100000,
      price: 3400.0,
      orderType: 'GTC',
    }
    await expect(adapter.placeOrder(order)).rejects.toThrow('Order rejected')
  })
})

// ── cancelOrder ──────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.cancelOrder', () => {
  it('sends cancel request with correct orderId', async () => {
    const cancelResponse = {
      status: 'ok',
      response: { type: 'cancel', data: { statuses: ['success'] } },
    }
    const fetch = makeFetch(cancelResponse)
    const adapter = makeAdapter(fetch)
    await adapter.cancelOrder('99999:ETH')

    const body = getCallBody(fetch)
    const action = body['action'] as Record<string, unknown>
    expect(action['type']).toBe('cancel')
    // HL API uses { a: assetIndex, o: orderId }
    const cancels = action['cancels'] as Array<{ a: number; o: number }>
    expect(cancels[0]!['o']).toBe(99999)
  })

  it('resolves without error on success', async () => {
    const cancelResponse = {
      status: 'ok',
      response: { type: 'cancel', data: { statuses: ['success'] } },
    }
    const fetch = makeFetch(cancelResponse)
    const adapter = makeAdapter(fetch)
    await expect(adapter.cancelOrder('123:ETH')).resolves.toBeUndefined()
  })
})

// ── cancelAllOrders ──────────────────────────────────────────────────────────

describe('HyperliquidAdapter.cancelAllOrders', () => {
  it('cancels all open orders when no symbol given', async () => {
    const openOrders = [
      { coin: 'ETH', oid: 1, side: 'B', limitPx: '3400', sz: '0.1', timestamp: 1000, orderType: 'Limit' },
      { coin: 'BTC', oid: 2, side: 'A', limitPx: '67000', sz: '0.01', timestamp: 2000, orderType: 'Limit' },
    ]
    const cancelResponse = {
      status: 'ok',
      response: { type: 'cancel', data: { statuses: ['success', 'success'] } },
    }
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(openOrders))
      .mockResolvedValueOnce(fakeResponse(cancelResponse)) as FetchMock

    const adapter = makeAdapter(fetch)
    await expect(adapter.cancelAllOrders()).resolves.toBeUndefined()

    const cancelBody = getCallBody(fetch, 1)
    const action = cancelBody['action'] as Record<string, unknown>
    const cancels = action['cancels'] as unknown[]
    expect(cancels).toHaveLength(2)
  })

  it('cancels only matching symbol orders when symbol given', async () => {
    const openOrders = [
      { coin: 'ETH', oid: 1, side: 'B', limitPx: '3400', sz: '0.1', timestamp: 1000, orderType: 'Limit' },
      { coin: 'BTC', oid: 2, side: 'A', limitPx: '67000', sz: '0.01', timestamp: 2000, orderType: 'Limit' },
    ]
    const cancelResponse = {
      status: 'ok',
      response: { type: 'cancel', data: { statuses: ['success'] } },
    }
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(openOrders))
      .mockResolvedValueOnce(fakeResponse(cancelResponse)) as FetchMock

    const adapter = makeAdapter(fetch)
    await adapter.cancelAllOrders('ETH-PERP')

    const cancelBody = getCallBody(fetch, 1)
    const action = cancelBody['action'] as Record<string, unknown>
    // HL cancel uses { a: assetIndex, o: orderId }; ETH has asset index 1 in static map
    const cancels = action['cancels'] as Array<{ a: number; o: number }>
    expect(cancels).toHaveLength(1)
    expect(cancels[0]!['o']).toBe(1) // oid of the ETH open order
  })

  it('resolves immediately when no open orders', async () => {
    const fetch = makeFetch([])
    const adapter = makeAdapter(fetch)
    await expect(adapter.cancelAllOrders()).resolves.toBeUndefined()
    expect(fetch).toHaveBeenCalledTimes(1) // only the getOpenOrders call
  })
})

// ── setStopLoss ───────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.setStopLoss', () => {
  it('places a stop market order for the stop loss', async () => {
    const exchangeResponse = {
      status: 'ok',
      response: {
        type: 'order',
        data: {
          statuses: [{ resting: { oid: 55555 } }],
        },
      },
    }
    const fetch = makeFetch(exchangeResponse)
    const adapter = makeAdapter(fetch)
    const result = await adapter.setStopLoss('ETH-PERP', 'BUY', 3300.0, 0.5)

    expect(result.orderId).toBe('55555')

    const body = getCallBody(fetch)
    const action = body['action'] as Record<string, unknown>
    const orders = action['orders'] as Array<Record<string, unknown>>
    const order = orders[0]!
    expect(order['a']).toBeDefined() // asset index
    expect(order['b']).toBe(false) // SL for BUY position → sell
    const t = order['t'] as Record<string, unknown>
    expect(t['trigger']).toBeDefined()
  })
})

// ── getOpenOrders ────────────────────────────────────────────────────────────

describe('HyperliquidAdapter.getOpenOrders', () => {
  it('returns all open orders', async () => {
    const rawOrders = [
      { coin: 'ETH', oid: 1, side: 'B', limitPx: '3400.0', sz: '0.1', timestamp: 1000000, orderType: 'Limit' },
    ]
    const fetch = makeFetch(rawOrders)
    const adapter = makeAdapter(fetch)
    const orders = await adapter.getOpenOrders()

    expect(orders).toHaveLength(1)
    const o = orders[0]!
    expect(o.orderId).toBe('1')
    expect(o.symbol).toBe('ETH-PERP')
    expect(o.side).toBe('BUY')
    expect(o.price).toBeCloseTo(3400.0)
    expect(o.size).toBeCloseTo(0.1)
  })

  it('filters by symbol when provided', async () => {
    const rawOrders = [
      { coin: 'ETH', oid: 1, side: 'B', limitPx: '3400.0', sz: '0.1', timestamp: 1000000, orderType: 'Limit' },
      { coin: 'BTC', oid: 2, side: 'A', limitPx: '67000.0', sz: '0.01', timestamp: 2000000, orderType: 'Limit' },
    ]
    const fetch = makeFetch(rawOrders)
    const adapter = makeAdapter(fetch)
    const orders = await adapter.getOpenOrders('ETH-PERP')

    expect(orders).toHaveLength(1)
    expect(orders[0]!.symbol).toBe('ETH-PERP')
  })
})

// ── getExchangeInfo ──────────────────────────────────────────────────────────

describe('HyperliquidAdapter.getExchangeInfo', () => {
  it('returns exchange info with supported symbols', async () => {
    const meta = {
      universe: [
        { name: 'ETH', szDecimals: 3, maxLeverage: 50 },
        { name: 'BTC', szDecimals: 5, maxLeverage: 100 },
        { name: 'SOL', szDecimals: 1, maxLeverage: 20 },
      ],
    }
    const fetch = makeFetch(meta)
    const adapter = makeAdapter(fetch)
    const info = await adapter.getExchangeInfo()

    expect(info.name).toBe('Hyperliquid')
    expect(info.testnet).toBe(true)
    expect(info.supportedSymbols).toContain('ETH-PERP')
    expect(info.supportedSymbols).toContain('BTC-PERP')
    expect(info.minOrderSizes['ETH-PERP']).toBeGreaterThan(0)
  })
})

// ── Symbol conversion ─────────────────────────────────────────────────────────

describe('Symbol conversion', () => {
  it('converts ETH-PERP to ETH for API calls', async () => {
    const l2Book = { levels: [[], []] }
    const fetch = makeFetch(l2Book)
    const adapter = makeAdapter(fetch)
    await adapter.getOrderBook('ETH-PERP')

    const body = getCallBody(fetch)
    expect(body['coin']).toBe('ETH')
  })

  it('converts coin ETH back to ETH-PERP in responses', async () => {
    const rawOrders = [
      { coin: 'ETH', oid: 1, side: 'B', limitPx: '3400.0', sz: '0.1', timestamp: 1000000, orderType: 'Limit' },
    ]
    const fetch = makeFetch(rawOrders)
    const adapter = makeAdapter(fetch)
    const orders = await adapter.getOpenOrders()
    expect(orders[0]!.symbol).toBe('ETH-PERP')
  })
})

// ── Error handling ────────────────────────────────────────────────────────────

describe('Error handling', () => {
  it('throws on HTTP error status', async () => {
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValue(fakeResponse({ error: 'Internal server error' }, 500)) as FetchMock
    const adapter = makeAdapter(fetch)
    await expect(adapter.getBalances()).rejects.toThrow()
  })

  it('throws on network failure', async () => {
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockRejectedValue(new Error('ECONNREFUSED')) as FetchMock
    const adapter = makeAdapter(fetch)
    await expect(adapter.getBalances()).rejects.toThrow('ECONNREFUSED')
  })
})

// ── Integration tests (skipped) ───────────────────────────────────────────────

describe.skip('Integration (testnet)', () => {
  const adapter = new HyperliquidAdapter({
    testnet: true,
    privateKey: process.env['HL_PRIVATE_KEY'] ?? FAKE_PRIVATE_KEY,
    walletAddress: process.env['HL_WALLET_ADDRESS'] ?? WALLET_ADDRESS,
  })

  it('gets ETH-PERP ticker from testnet', async () => {
    const ticker = await adapter.getTicker('ETH-PERP')
    expect(ticker.mid).toBeGreaterThan(0)
  })

  it('gets order book from testnet', async () => {
    const book = await adapter.getOrderBook('ETH-PERP', 5)
    expect(book.bids.length).toBeGreaterThan(0)
    expect(book.asks.length).toBeGreaterThan(0)
  })

  it('gets exchange info from testnet', async () => {
    const info = await adapter.getExchangeInfo()
    expect(info.supportedSymbols.length).toBeGreaterThan(0)
  })
})
