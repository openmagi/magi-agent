import { describe, it, expect, jest } from '@jest/globals'
import { AlpacaAdapter } from './alpaca.js'
import type { OrderRequest } from '../types.js'

// ── helpers ─────────────────────────────────────────────────────────────────

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

function makeAdapter(fetchMock: FetchMock, paper = true): AlpacaAdapter {
  const adapter = new AlpacaAdapter({
    apiKey: 'PK_TEST_123',
    apiSecret: 'SK_TEST_456',
    paper,
  })
  ;(adapter as unknown as { _fetch: FetchMock })._fetch = fetchMock
  return adapter
}

function getCallUrl(fetchMock: FetchMock, callIndex = 0): string {
  const call = fetchMock.mock.calls[callIndex] as unknown[]
  return call[0] as string
}

function getCallInit(fetchMock: FetchMock, callIndex = 0): RequestInit {
  const call = fetchMock.mock.calls[callIndex] as unknown[]
  return call[1] as RequestInit
}

// ── constructor ──────────────────────────────────────────────────────────────

describe('AlpacaAdapter constructor', () => {
  it('should use paper URL when paper=true', () => {
    const fetch = makeFetch({})
    const adapter = makeAdapter(fetch, true)
    expect(adapter.name).toBe('Alpaca')
    // Verify by calling a trading endpoint
    void adapter.getBalances().catch(() => {/* ignore */})
    const url = getCallUrl(fetch)
    expect(url).toContain('paper-api.alpaca.markets')
  })

  it('should use live URL when paper=false', () => {
    const fetch = makeFetch({})
    const adapter = makeAdapter(fetch, false)
    void adapter.getBalances().catch(() => {/* ignore */})
    const url = getCallUrl(fetch)
    expect(url).toContain('api.alpaca.markets')
    expect(url).not.toContain('paper-api')
  })

  it('should default to iex data feed', () => {
    const adapter = new AlpacaAdapter({
      apiKey: 'PK',
      apiSecret: 'SK',
      paper: true,
    })
    // dataFeed is private; verify via a data endpoint call
    const fetch = makeFetch({ bars: {} })
    ;(adapter as unknown as { _fetch: FetchMock })._fetch = fetch
    void adapter.getCandles('AAPL', '1d', 1).catch(() => {/* ignore */})
    const url = getCallUrl(fetch)
    expect(url).toContain('feed=iex')
  })
})

// ── authentication ──────────────────────────────────────────────────────────

describe('AlpacaAdapter authentication', () => {
  it('should include APCA-API-KEY-ID header', async () => {
    const account = { equity: '10000', cash: '5000', buying_power: '5000', unrealized_pl: '100' }
    const fetch = makeFetch(account)
    const adapter = makeAdapter(fetch)
    await adapter.getBalances()

    const init = getCallInit(fetch)
    const headers = init.headers as Record<string, string>
    expect(headers['APCA-API-KEY-ID']).toBe('PK_TEST_123')
  })

  it('should include APCA-API-SECRET-KEY header', async () => {
    const account = { equity: '10000', cash: '5000', buying_power: '5000', unrealized_pl: '100' }
    const fetch = makeFetch(account)
    const adapter = makeAdapter(fetch)
    await adapter.getBalances()

    const init = getCallInit(fetch)
    const headers = init.headers as Record<string, string>
    expect(headers['APCA-API-SECRET-KEY']).toBe('SK_TEST_456')
  })
})

// ── getTicker ────────────────────────────────────────────────────────────────

describe('AlpacaAdapter.getTicker', () => {
  const snapshotResponse = {
    latestTrade: { p: 189.5, s: 100, t: '2026-03-14T15:00:00Z' },
    latestQuote: { bp: 189.45, bs: 200, ap: 189.55, as: 150, t: '2026-03-14T15:00:00Z' },
    minuteBar: { o: 189.0, h: 190.0, l: 188.5, c: 189.5, v: 50000, t: '2026-03-14T15:00:00Z' },
    dailyBar: { o: 188.0, h: 191.0, l: 187.5, c: 189.5, v: 12000000, t: '2026-03-14T00:00:00Z' },
    prevDailyBar: { o: 187.0, h: 189.0, l: 186.0, c: 188.0, v: 10000000, t: '2026-03-13T00:00:00Z' },
  }

  it('should fetch snapshot and map to Ticker', async () => {
    const fetch = makeFetch(snapshotResponse)
    const adapter = makeAdapter(fetch)
    const ticker = await adapter.getTicker('AAPL')

    expect(ticker.symbol).toBe('AAPL')
    expect(ticker.lastPrice).toBeCloseTo(189.5)
    expect(typeof ticker.timestamp).toBe('number')
  })

  it('should set fundingRate and openInterest to 0', async () => {
    const fetch = makeFetch(snapshotResponse)
    const adapter = makeAdapter(fetch)
    const ticker = await adapter.getTicker('AAPL')

    expect(ticker.fundingRate).toBe(0)
    expect(ticker.openInterest).toBe(0)
  })

  it('should compute mid from latest trade price', async () => {
    const fetch = makeFetch(snapshotResponse)
    const adapter = makeAdapter(fetch)
    const ticker = await adapter.getTicker('AAPL')

    // mid = (bid + ask) / 2 = (189.45 + 189.55) / 2 = 189.50
    expect(ticker.mid).toBeCloseTo(189.5)
  })

  it('should set bid/ask from latest quote', async () => {
    const fetch = makeFetch(snapshotResponse)
    const adapter = makeAdapter(fetch)
    const ticker = await adapter.getTicker('AAPL')

    expect(ticker.bid).toBeCloseTo(189.45)
    expect(ticker.ask).toBeCloseTo(189.55)
  })

  it('should include volume from daily bar', async () => {
    const fetch = makeFetch(snapshotResponse)
    const adapter = makeAdapter(fetch)
    const ticker = await adapter.getTicker('AAPL')

    expect(ticker.volume24h).toBe(12000000)
  })
})

// ── getOrderBook ─────────────────────────────────────────────────────────────

describe('AlpacaAdapter.getOrderBook', () => {
  it('should return top-of-book quote as single-level OrderBook', async () => {
    const quoteResponse = {
      quote: {
        bp: 189.45, bs: 200, ap: 189.55, as: 150, t: '2026-03-14T15:00:00Z',
      },
    }
    const fetch = makeFetch(quoteResponse)
    const adapter = makeAdapter(fetch)
    const book = await adapter.getOrderBook('AAPL')

    expect(book.symbol).toBe('AAPL')
    expect(book.bids).toHaveLength(1)
    expect(book.asks).toHaveLength(1)
    expect(typeof book.timestamp).toBe('number')
  })

  it('should include bid/ask price and size from quote', async () => {
    const quoteResponse = {
      quote: {
        bp: 189.45, bs: 200, ap: 189.55, as: 150, t: '2026-03-14T15:00:00Z',
      },
    }
    const fetch = makeFetch(quoteResponse)
    const adapter = makeAdapter(fetch)
    const book = await adapter.getOrderBook('AAPL')

    expect(book.bids[0]).toEqual({ price: 189.45, size: 200 })
    expect(book.asks[0]).toEqual({ price: 189.55, size: 150 })
  })
})

// ── getCandles ───────────────────────────────────────────────────────────────

describe('AlpacaAdapter.getCandles', () => {
  it('should fetch bars with correct timeframe mapping', async () => {
    const barsResponse = {
      bars: [
        { t: '2026-03-14T09:30:00Z', o: 189.0, h: 190.0, l: 188.5, c: 189.5, v: 50000 },
        { t: '2026-03-14T09:31:00Z', o: 189.5, h: 190.5, l: 189.0, c: 190.0, v: 45000 },
      ],
    }
    const fetch = makeFetch(barsResponse)
    const adapter = makeAdapter(fetch)
    const candles = await adapter.getCandles('AAPL', '1m', 2)

    expect(candles).toHaveLength(2)
    const url = getCallUrl(fetch)
    expect(url).toContain('timeframe=1Min')
  })

  it('should map 1m/5m/15m/1h/1d intervals to Alpaca timeframes', async () => {
    const barsResponse = { bars: [] }
    const intervals = [
      ['1m', '1Min'],
      ['5m', '5Min'],
      ['15m', '15Min'],
      ['1h', '1Hour'],
      ['1d', '1Day'],
    ] as const

    for (const [input, expected] of intervals) {
      const fetch = makeFetch(barsResponse)
      const adapter = makeAdapter(fetch)
      await adapter.getCandles('AAPL', input, 10)
      const url = getCallUrl(fetch)
      expect(url).toContain(`timeframe=${expected}`)
    }
  })

  it('should return limit number of candles', async () => {
    const bars = Array.from({ length: 20 }, (_, i) => ({
      t: `2026-03-14T09:${String(i).padStart(2, '0')}:00Z`,
      o: 189 + i, h: 190 + i, l: 188 + i, c: 189.5 + i, v: 50000 + i * 100,
    }))
    const barsResponse = { bars }
    const fetch = makeFetch(barsResponse)
    const adapter = makeAdapter(fetch)
    const candles = await adapter.getCandles('AAPL', '1m', 10)

    expect(candles).toHaveLength(10)
  })

  it('should map OHLCV fields correctly', async () => {
    const barsResponse = {
      bars: [
        { t: '2026-03-14T09:30:00Z', o: 189.0, h: 190.0, l: 188.5, c: 189.5, v: 50000 },
      ],
    }
    const fetch = makeFetch(barsResponse)
    const adapter = makeAdapter(fetch)
    const candles = await adapter.getCandles('AAPL', '1m', 1)

    expect(candles[0]).toEqual({
      timestamp: new Date('2026-03-14T09:30:00Z').getTime(),
      open: 189.0,
      high: 190.0,
      low: 188.5,
      close: 189.5,
      volume: 50000,
    })
  })
})

// ── getBalances ──────────────────────────────────────────────────────────────

describe('AlpacaAdapter.getBalances', () => {
  it('should map account equity/cash to Balance', async () => {
    const account = {
      equity: '25000.50',
      cash: '15000.25',
      buying_power: '15000.25',
      unrealized_pl: '500.75',
    }
    const fetch = makeFetch(account)
    const adapter = makeAdapter(fetch)
    const balances = await adapter.getBalances()

    expect(balances).toHaveLength(1)
    expect(balances[0]!.total).toBeCloseTo(25000.50)
    expect(balances[0]!.available).toBeCloseTo(15000.25)
  })

  it('should set currency to USD', async () => {
    const account = {
      equity: '10000', cash: '5000', buying_power: '5000', unrealized_pl: '0',
    }
    const fetch = makeFetch(account)
    const adapter = makeAdapter(fetch)
    const balances = await adapter.getBalances()

    expect(balances[0]!.currency).toBe('USD')
  })

  it('should calculate unrealizedPnl from account data', async () => {
    const account = {
      equity: '10000', cash: '5000', buying_power: '5000', unrealized_pl: '250.50',
    }
    const fetch = makeFetch(account)
    const adapter = makeAdapter(fetch)
    const balances = await adapter.getBalances()

    expect(balances[0]!.unrealizedPnl).toBeCloseTo(250.50)
  })
})

// ── getPositions ─────────────────────────────────────────────────────────────

describe('AlpacaAdapter.getPositions', () => {
  it('should map stock positions to Position type', async () => {
    const positions = [
      {
        symbol: 'AAPL',
        qty: '10',
        avg_entry_price: '180.00',
        current_price: '189.50',
        unrealized_pl: '95.00',
        market_value: '1895.00',
      },
    ]
    const fetch = makeFetch(positions)
    const adapter = makeAdapter(fetch)
    const result = await adapter.getPositions()

    expect(result).toHaveLength(1)
    expect(result[0]!.symbol).toBe('AAPL')
    expect(result[0]!.entryPrice).toBeCloseTo(180.0)
    expect(result[0]!.markPrice).toBeCloseTo(189.5)
    expect(result[0]!.unrealizedPnl).toBeCloseTo(95.0)
  })

  it('should determine LONG/SHORT from qty sign', async () => {
    const positions = [
      {
        symbol: 'AAPL',
        qty: '10',
        avg_entry_price: '180.00',
        current_price: '189.50',
        unrealized_pl: '95.00',
        market_value: '1895.00',
      },
      {
        symbol: 'TSLA',
        qty: '-5',
        avg_entry_price: '250.00',
        current_price: '245.00',
        unrealized_pl: '25.00',
        market_value: '-1225.00',
      },
    ]
    const fetch = makeFetch(positions)
    const adapter = makeAdapter(fetch)
    const result = await adapter.getPositions()

    expect(result[0]!.side).toBe('LONG')
    expect(result[0]!.size).toBe(10)
    expect(result[1]!.side).toBe('SHORT')
    expect(result[1]!.size).toBe(5)
  })

  it('should set leverage to 1', async () => {
    const positions = [
      {
        symbol: 'AAPL',
        qty: '10',
        avg_entry_price: '180.00',
        current_price: '189.50',
        unrealized_pl: '95.00',
        market_value: '1895.00',
      },
    ]
    const fetch = makeFetch(positions)
    const adapter = makeAdapter(fetch)
    const result = await adapter.getPositions()

    expect(result[0]!.leverage).toBe(1)
  })

  it('should set liquidationPrice to null', async () => {
    const positions = [
      {
        symbol: 'AAPL',
        qty: '10',
        avg_entry_price: '180.00',
        current_price: '189.50',
        unrealized_pl: '95.00',
        market_value: '1895.00',
      },
    ]
    const fetch = makeFetch(positions)
    const adapter = makeAdapter(fetch)
    const result = await adapter.getPositions()

    expect(result[0]!.liquidationPrice).toBeNull()
  })
})

// ── placeOrder ───────────────────────────────────────────────────────────────

describe('AlpacaAdapter.placeOrder', () => {
  it('should submit limit order to Alpaca', async () => {
    const orderResponse = {
      id: 'order-uuid-123',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'AAPL',
      side: 'BUY',
      size: 10,
      price: 189.0,
      orderType: 'GTC',
    }
    await adapter.placeOrder(order)

    const init = getCallInit(fetch)
    const body = JSON.parse(init.body as string) as Record<string, unknown>
    expect(body['type']).toBe('limit')
    expect(body['limit_price']).toBe('189')
  })

  it('should map BUY/SELL to buy/sell', async () => {
    const orderResponse = {
      id: 'order-uuid-456',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }

    for (const side of ['BUY', 'SELL'] as const) {
      const fetch = makeFetch(orderResponse)
      const adapter = makeAdapter(fetch)
      const order: OrderRequest = {
        symbol: 'AAPL',
        side,
        size: 10,
        price: 189.0,
        orderType: 'GTC',
      }
      await adapter.placeOrder(order)

      const init = getCallInit(fetch)
      const body = JSON.parse(init.body as string) as Record<string, unknown>
      expect(body['side']).toBe(side.toLowerCase())
    }
  })

  it('should map ALO to limit order type', async () => {
    const orderResponse = {
      id: 'order-uuid-789',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'AAPL',
      side: 'BUY',
      size: 10,
      price: 189.0,
      orderType: 'ALO',
    }
    await adapter.placeOrder(order)

    const init = getCallInit(fetch)
    const body = JSON.parse(init.body as string) as Record<string, unknown>
    expect(body['type']).toBe('limit')
    expect(body['time_in_force']).toBe('day')
  })

  it('should map GTC to gtc time_in_force', async () => {
    const orderResponse = {
      id: 'order-uuid-abc',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'AAPL',
      side: 'BUY',
      size: 10,
      price: 189.0,
      orderType: 'GTC',
    }
    await adapter.placeOrder(order)

    const init = getCallInit(fetch)
    const body = JSON.parse(init.body as string) as Record<string, unknown>
    expect(body['time_in_force']).toBe('gtc')
  })

  it('should map IOC to ioc time_in_force', async () => {
    const orderResponse = {
      id: 'order-uuid-def',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'AAPL',
      side: 'BUY',
      size: 10,
      price: 189.0,
      orderType: 'IOC',
    }
    await adapter.placeOrder(order)

    const init = getCallInit(fetch)
    const body = JSON.parse(init.body as string) as Record<string, unknown>
    expect(body['time_in_force']).toBe('ioc')
  })

  it('should handle filled response', async () => {
    const orderResponse = {
      id: 'order-uuid-filled',
      status: 'filled',
      filled_qty: '10',
      filled_avg_price: '189.50',
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'AAPL',
      side: 'BUY',
      size: 10,
      price: 189.0,
      orderType: 'IOC',
    }
    const result = await adapter.placeOrder(order)

    expect(result.orderId).toBe('order-uuid-filled')
    expect(result.status).toBe('FILLED')
    expect(result.filledSize).toBe(10)
    expect(result.filledPrice).toBeCloseTo(189.5)
  })

  it('should handle accepted (open) response', async () => {
    const orderResponse = {
      id: 'order-uuid-open',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'AAPL',
      side: 'BUY',
      size: 10,
      price: 189.0,
      orderType: 'GTC',
    }
    const result = await adapter.placeOrder(order)

    expect(result.orderId).toBe('order-uuid-open')
    expect(result.status).toBe('OPEN')
    expect(result.filledSize).toBe(0)
    expect(result.filledPrice).toBe(0)
  })

  it('should handle rejected response', async () => {
    const orderResponse = {
      id: 'order-uuid-rejected',
      status: 'rejected',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'AAPL',
      side: 'BUY',
      size: 10,
      price: 189.0,
      orderType: 'GTC',
    }
    const result = await adapter.placeOrder(order)

    expect(result.orderId).toBe('order-uuid-rejected')
    expect(result.status).toBe('REJECTED')
  })
})

// ── cancelOrder ──────────────────────────────────────────────────────────────

describe('AlpacaAdapter.cancelOrder', () => {
  it('should send DELETE to /v2/orders/{orderId}', async () => {
    const fetch = makeFetch(null, 204)
    const adapter = makeAdapter(fetch)
    await adapter.cancelOrder('order-uuid-999')

    const url = getCallUrl(fetch)
    expect(url).toContain('/v2/orders/order-uuid-999')
    const init = getCallInit(fetch)
    expect(init.method).toBe('DELETE')
  })
})

// ── cancelAllOrders ──────────────────────────────────────────────────────────

describe('AlpacaAdapter.cancelAllOrders', () => {
  it('should send DELETE to /v2/orders with no params', async () => {
    const fetch = makeFetch([], 207)
    const adapter = makeAdapter(fetch)
    await adapter.cancelAllOrders()

    const url = getCallUrl(fetch)
    expect(url).toMatch(/\/v2\/orders$/)
    const init = getCallInit(fetch)
    expect(init.method).toBe('DELETE')
  })

  it('should filter by symbol when provided', async () => {
    // First call: GET open orders; Second call: DELETE each matching order
    const openOrders = [
      { id: 'ord-1', symbol: 'AAPL', side: 'buy', qty: '10', filled_qty: '0', type: 'limit', limit_price: '189', time_in_force: 'gtc', created_at: '2026-03-14T15:00:00Z', status: 'new' },
      { id: 'ord-2', symbol: 'TSLA', side: 'buy', qty: '5', filled_qty: '0', type: 'limit', limit_price: '250', time_in_force: 'gtc', created_at: '2026-03-14T15:00:00Z', status: 'new' },
    ]
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(openOrders))
      .mockResolvedValueOnce(fakeResponse(null, 204)) as unknown as FetchMock
    const adapter = makeAdapter(fetch)
    await adapter.cancelAllOrders('AAPL')

    // Should have fetched open orders and then deleted only the AAPL order
    expect(fetch).toHaveBeenCalledTimes(2)
    const deleteUrl = getCallUrl(fetch, 1)
    expect(deleteUrl).toContain('ord-1')
  })
})

// ── setStopLoss ──────────────────────────────────────────────────────────────

describe('AlpacaAdapter.setStopLoss', () => {
  it('should submit stop order with correct side', async () => {
    const orderResponse = {
      id: 'stop-order-123',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)
    await adapter.setStopLoss('AAPL', 'BUY', 175.0, 10)

    const init = getCallInit(fetch)
    const body = JSON.parse(init.body as string) as Record<string, unknown>
    // SL for BUY (long) position -> sell to close
    expect(body['side']).toBe('sell')
  })

  it('should set stop_price to triggerPrice', async () => {
    const orderResponse = {
      id: 'stop-order-456',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)
    await adapter.setStopLoss('AAPL', 'BUY', 175.0, 10)

    const init = getCallInit(fetch)
    const body = JSON.parse(init.body as string) as Record<string, unknown>
    expect(body['type']).toBe('stop')
    expect(body['stop_price']).toBe('175')
  })

  it('should set reduce_only behavior via qty', async () => {
    const orderResponse = {
      id: 'stop-order-789',
      status: 'accepted',
      filled_qty: '0',
      filled_avg_price: null,
      created_at: '2026-03-14T15:00:00Z',
    }
    const fetch = makeFetch(orderResponse)
    const adapter = makeAdapter(fetch)
    const result = await adapter.setStopLoss('TSLA', 'SELL', 260.0, 5)

    const init = getCallInit(fetch)
    const body = JSON.parse(init.body as string) as Record<string, unknown>
    // SL for SELL (short) position -> buy to close
    expect(body['side']).toBe('buy')
    expect(body['qty']).toBe('5')
    expect(result.orderId).toBe('stop-order-789')
  })
})

// ── getOpenOrders ────────────────────────────────────────────────────────────

describe('AlpacaAdapter.getOpenOrders', () => {
  it('should fetch open orders and map to OpenOrder[]', async () => {
    const rawOrders = [
      {
        id: 'ord-abc',
        symbol: 'AAPL',
        side: 'buy',
        qty: '10',
        filled_qty: '3',
        type: 'limit',
        limit_price: '189.00',
        time_in_force: 'gtc',
        created_at: '2026-03-14T15:00:00Z',
        status: 'partially_filled',
      },
    ]
    const fetch = makeFetch(rawOrders)
    const adapter = makeAdapter(fetch)
    const orders = await adapter.getOpenOrders()

    expect(orders).toHaveLength(1)
    expect(orders[0]!.orderId).toBe('ord-abc')
    expect(orders[0]!.symbol).toBe('AAPL')
    expect(orders[0]!.side).toBe('BUY')
    expect(orders[0]!.price).toBeCloseTo(189.0)
    expect(orders[0]!.size).toBe(10)
    expect(orders[0]!.filledSize).toBe(3)
    expect(orders[0]!.orderType).toBe('GTC')
  })

  it('should filter by symbol when provided', async () => {
    const rawOrders = [
      {
        id: 'ord-1', symbol: 'AAPL', side: 'buy', qty: '10', filled_qty: '0',
        type: 'limit', limit_price: '189.00', time_in_force: 'gtc',
        created_at: '2026-03-14T15:00:00Z', status: 'new',
      },
      {
        id: 'ord-2', symbol: 'TSLA', side: 'sell', qty: '5', filled_qty: '0',
        type: 'limit', limit_price: '250.00', time_in_force: 'ioc',
        created_at: '2026-03-14T15:00:00Z', status: 'new',
      },
    ]
    const fetch = makeFetch(rawOrders)
    const adapter = makeAdapter(fetch)
    const orders = await adapter.getOpenOrders('AAPL')

    expect(orders).toHaveLength(1)
    expect(orders[0]!.symbol).toBe('AAPL')
  })
})

// ── getExchangeInfo ─────────────────────────────────────────────────────────

describe('AlpacaAdapter.getExchangeInfo', () => {
  it('should fetch active assets and map to ExchangeInfo', async () => {
    const assets = [
      { symbol: 'AAPL', status: 'active', tradable: true, min_order_size: '1', min_trade_increment: '1', price_increment: '0.01' },
      { symbol: 'TSLA', status: 'active', tradable: true, min_order_size: '1', min_trade_increment: '1', price_increment: '0.01' },
      { symbol: 'GOOG', status: 'active', tradable: true, min_order_size: '1', min_trade_increment: '1', price_increment: '0.01' },
    ]
    const fetch = makeFetch(assets)
    const adapter = makeAdapter(fetch)
    const info = await adapter.getExchangeInfo()

    expect(info.supportedSymbols).toContain('AAPL')
    expect(info.supportedSymbols).toContain('TSLA')
    expect(info.supportedSymbols).toContain('GOOG')
  })

  it('should set name to "Alpaca"', async () => {
    const fetch = makeFetch([])
    const adapter = makeAdapter(fetch)
    const info = await adapter.getExchangeInfo()

    expect(info.name).toBe('Alpaca')
  })

  it('should report paper/live testnet status', async () => {
    const fetch = makeFetch([])

    const paperAdapter = makeAdapter(fetch, true)
    const paperInfo = await paperAdapter.getExchangeInfo()
    expect(paperInfo.testnet).toBe(true)

    const liveAdapter = makeAdapter(makeFetch([]), false)
    const liveInfo = await liveAdapter.getExchangeInfo()
    expect(liveInfo.testnet).toBe(false)
  })
})

// ── Error handling ───────────────────────────────────────────────────────────

describe('AlpacaAdapter error handling', () => {
  it('should throw on HTTP error status from trading API', async () => {
    const fetch = makeFetch({ message: 'forbidden' }, 403)
    const adapter = makeAdapter(fetch)
    await expect(adapter.getBalances()).rejects.toThrow('Alpaca trading API error 403')
  })

  it('should throw on HTTP error status from data API', async () => {
    const fetch = makeFetch({ message: 'not found' }, 404)
    const adapter = makeAdapter(fetch)
    await expect(adapter.getTicker('INVALID')).rejects.toThrow('Alpaca data API error 404')
  })
})
