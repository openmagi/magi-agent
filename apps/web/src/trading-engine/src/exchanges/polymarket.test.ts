import { describe, it, expect, jest } from '@jest/globals'
import { PolymarketAdapter } from './polymarket.js'
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

const CONDITION_ID = '0xabc123def456'
const YES_TOKEN_ID = '0xyes_token_111'
const NO_TOKEN_ID = '0xno_token_222'

const MARKET_RESPONSE = {
  condition_id: CONDITION_ID,
  question: 'Will ETH reach $10k by end of 2026?',
  tokens: [
    { token_id: YES_TOKEN_ID, outcome: 'Yes' },
    { token_id: NO_TOKEN_ID, outcome: 'No' },
  ],
  active: true,
  minimum_order_size: '5',
  minimum_tick_size: '0.01',
}

function makeAdapter(fetchMock?: FetchMock): PolymarketAdapter {
  const adapter = new PolymarketAdapter({
    apiUrl: 'https://clob.polymarket.com',
    privateKey: '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80',
    walletAddress: '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266',
    chainId: 137,
    apiKey: 'test-key',
    apiSecret: 'dGVzdC1zZWNyZXQ=',
    apiPassphrase: 'test-passphrase',
  })
  if (fetchMock) {
    ;(adapter as unknown as { _fetch: FetchMock })._fetch = fetchMock
  }
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

// ── symbol parsing ──────────────────────────────────────────────────────────

describe('PolymarketAdapter.parseSymbol', () => {
  it('should parse YES-<conditionId> into token lookup', async () => {
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE)) // resolveMarket
      .mockResolvedValueOnce(fakeResponse({ price: '0.65' })) // price
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE)) as FetchMock // market info for volume

    const adapter = makeAdapter(fetchMock)
    const ticker = await adapter.getTicker(`YES-${CONDITION_ID}`)

    expect(ticker.symbol).toBe(`YES-${CONDITION_ID}`)
    expect(ticker.mid).toBeCloseTo(0.65)
  })

  it('should parse NO-<conditionId> into token lookup', async () => {
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse({ price: '0.35' }))
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const ticker = await adapter.getTicker(`NO-${CONDITION_ID}`)

    expect(ticker.symbol).toBe(`NO-${CONDITION_ID}`)
    expect(ticker.mid).toBeCloseTo(0.35)
  })

  it('should throw on invalid symbol format', async () => {
    const adapter = makeAdapter(makeFetch({}))
    await expect(adapter.getTicker('INVALID')).rejects.toThrow('Invalid Polymarket symbol')
    await expect(adapter.getTicker('MAYBE-abc')).rejects.toThrow('Invalid Polymarket symbol')
  })
})

// ── getTicker ───────────────────────────────────────────────────────────────

describe('PolymarketAdapter.getTicker', () => {
  it('should fetch price and map to Ticker with probability as mid/bid/ask', async () => {
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse({ price: '0.72' }))
      .mockResolvedValueOnce(fakeResponse({ ...MARKET_RESPONSE, volume_num_24hr: 50000 })) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const ticker = await adapter.getTicker(`YES-${CONDITION_ID}`)

    expect(ticker.mid).toBeCloseTo(0.72)
    expect(ticker.bid).toBeLessThan(ticker.mid)
    expect(ticker.ask).toBeGreaterThan(ticker.mid)
    expect(ticker.lastPrice).toBeCloseTo(0.72)
    expect(typeof ticker.timestamp).toBe('number')
  })

  it('should set volume24h from market volume', async () => {
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse({ price: '0.55' }))
      .mockResolvedValueOnce(fakeResponse({ ...MARKET_RESPONSE, volume_num_24hr: 75000 })) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const ticker = await adapter.getTicker(`YES-${CONDITION_ID}`)

    expect(ticker.volume24h).toBe(75000)
  })

  it('should set fundingRate to 0 (no funding in prediction markets)', async () => {
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse({ price: '0.50' }))
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const ticker = await adapter.getTicker(`YES-${CONDITION_ID}`)

    expect(ticker.fundingRate).toBe(0)
  })
})

// ── getOrderBook ────────────────────────────────────────────────────────────

describe('PolymarketAdapter.getOrderBook', () => {
  it('should fetch book and map to OrderBook levels', async () => {
    const bookResponse = {
      bids: [
        { price: '0.60', size: '100' },
        { price: '0.59', size: '200' },
      ],
      asks: [
        { price: '0.62', size: '150' },
        { price: '0.63', size: '250' },
      ],
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(bookResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const book = await adapter.getOrderBook(`YES-${CONDITION_ID}`)

    expect(book.symbol).toBe(`YES-${CONDITION_ID}`)
    expect(book.bids).toHaveLength(2)
    expect(book.asks).toHaveLength(2)
    expect(book.bids[0]).toEqual({ price: 0.60, size: 100 })
    expect(book.asks[0]).toEqual({ price: 0.62, size: 150 })
  })

  it('should map buy/sell to bids/asks', async () => {
    const bookResponse = {
      bids: [{ price: '0.55', size: '50' }],
      asks: [{ price: '0.57', size: '80' }],
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(bookResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const book = await adapter.getOrderBook(`YES-${CONDITION_ID}`)

    expect(book.bids[0]!.price).toBe(0.55)
    expect(book.asks[0]!.price).toBe(0.57)
  })

  it('should limit depth to requested amount', async () => {
    const bookResponse = {
      bids: [
        { price: '0.60', size: '100' },
        { price: '0.59', size: '200' },
        { price: '0.58', size: '300' },
      ],
      asks: [
        { price: '0.62', size: '150' },
        { price: '0.63', size: '250' },
        { price: '0.64', size: '350' },
      ],
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(bookResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const book = await adapter.getOrderBook(`YES-${CONDITION_ID}`, 2)

    expect(book.bids).toHaveLength(2)
    expect(book.asks).toHaveLength(2)
  })
})

// ── getBalances ─────────────────────────────────────────────────────────────

describe('PolymarketAdapter.getBalances', () => {
  it('should return USDC balance from Polymarket account', async () => {
    const balanceResponse = [
      { asset_type: 'USDC', balance: '5000.50' },
    ]
    const fetchMock = makeFetch(balanceResponse)
    const adapter = makeAdapter(fetchMock)
    const balances = await adapter.getBalances()

    expect(balances).toHaveLength(1)
    expect(balances[0]!.currency).toBe('USDC')
    expect(balances[0]!.available).toBeCloseTo(5000.50)
    expect(balances[0]!.total).toBeCloseTo(5000.50)
    expect(balances[0]!.unrealizedPnl).toBe(0)
  })
})

// ── getPositions ────────────────────────────────────────────────────────────

describe('PolymarketAdapter.getPositions', () => {
  it('should map token positions to Position type', async () => {
    const positionsResponse = [
      {
        asset: YES_TOKEN_ID,
        condition_id: CONDITION_ID,
        size: '50',
        avg_price: '0.60',
        cur_price: '0.72',
        outcome: 'Yes',
      },
    ]
    const fetchMock = makeFetch(positionsResponse)
    const adapter = makeAdapter(fetchMock)
    const positions = await adapter.getPositions()

    expect(positions).toHaveLength(1)
    expect(positions[0]!.symbol).toContain(CONDITION_ID)
    expect(positions[0]!.side).toBe('LONG')
    expect(positions[0]!.size).toBe(50)
  })

  it('should calculate unrealizedPnl from current price vs entry', async () => {
    const positionsResponse = [
      {
        asset: YES_TOKEN_ID,
        condition_id: CONDITION_ID,
        size: '100',
        avg_price: '0.50',
        cur_price: '0.70',
        outcome: 'Yes',
      },
    ]
    const fetchMock = makeFetch(positionsResponse)
    const adapter = makeAdapter(fetchMock)
    const positions = await adapter.getPositions()

    // PnL = (0.70 - 0.50) * 100 = 20
    expect(positions[0]!.unrealizedPnl).toBeCloseTo(20)
  })

  it('should set leverage to 1 (no leverage in prediction markets)', async () => {
    const positionsResponse = [
      {
        asset: YES_TOKEN_ID,
        condition_id: CONDITION_ID,
        size: '25',
        avg_price: '0.40',
        cur_price: '0.45',
        outcome: 'Yes',
      },
    ]
    const fetchMock = makeFetch(positionsResponse)
    const adapter = makeAdapter(fetchMock)
    const positions = await adapter.getPositions()

    expect(positions[0]!.leverage).toBe(1)
    expect(positions[0]!.liquidationPrice).toBeNull()
  })
})

// ── placeOrder ──────────────────────────────────────────────────────────────

describe('PolymarketAdapter.placeOrder', () => {
  it('should submit order to CLOB API', async () => {
    const orderResponse = {
      id: 'order-123',
      status: 'MATCHED',
      size: '10',
      price: '0.65',
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(orderResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const order: OrderRequest = {
      symbol: `YES-${CONDITION_ID}`,
      side: 'BUY',
      size: 10,
      price: 0.65,
      orderType: 'GTC',
    }
    const result = await adapter.placeOrder(order)

    expect(result.orderId).toBe('order-123')
  })

  it('should map OrderRequest to Polymarket order format', async () => {
    const orderResponse = {
      id: 'order-456',
      status: 'LIVE',
      size: '0',
      price: '0.55',
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(orderResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const order: OrderRequest = {
      symbol: `YES-${CONDITION_ID}`,
      side: 'BUY',
      size: 20,
      price: 0.55,
      orderType: 'GTC',
    }
    await adapter.placeOrder(order)

    // Check that the POST was sent to /order endpoint
    const url = getCallUrl(fetchMock, 1)
    expect(url).toContain('/order')

    const init = getCallInit(fetchMock, 1)
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body as string) as Record<string, unknown>
    expect(body['token_id']).toBe(YES_TOKEN_ID)
    expect(body['price']).toBe('0.55')
    expect(body['size']).toBe('20')
    expect(body['side']).toBe('BUY')
  })

  it('should handle FILLED status response', async () => {
    const orderResponse = {
      id: 'order-789',
      status: 'MATCHED',
      size: '10',
      price: '0.70',
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(orderResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const result = await adapter.placeOrder({
      symbol: `YES-${CONDITION_ID}`,
      side: 'BUY',
      size: 10,
      price: 0.70,
      orderType: 'IOC',
    })

    expect(result.status).toBe('FILLED')
    expect(result.filledSize).toBe(10)
    expect(result.filledPrice).toBeCloseTo(0.70)
  })

  it('should handle OPEN (resting) status response', async () => {
    const orderResponse = {
      id: 'order-resting',
      status: 'LIVE',
      size: '0',
      price: '0.45',
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(orderResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const result = await adapter.placeOrder({
      symbol: `YES-${CONDITION_ID}`,
      side: 'SELL',
      size: 15,
      price: 0.45,
      orderType: 'GTC',
    })

    expect(result.status).toBe('OPEN')
    expect(result.filledSize).toBe(0)
  })
})

// ── cancelOrder ─────────────────────────────────────────────────────────────

describe('PolymarketAdapter.cancelOrder', () => {
  it('should send DELETE request with orderId', async () => {
    const fetchMock = makeFetch({ success: true })
    const adapter = makeAdapter(fetchMock)
    await adapter.cancelOrder('order-123')

    const url = getCallUrl(fetchMock)
    expect(url).toContain('/order/order-123')
    const init = getCallInit(fetchMock)
    expect(init.method).toBe('DELETE')
  })
})

// ── getOpenOrders ───────────────────────────────────────────────────────────

describe('PolymarketAdapter.getOpenOrders', () => {
  it('should fetch and map open orders', async () => {
    const ordersResponse = [
      {
        id: 'order-1',
        token_id: YES_TOKEN_ID,
        condition_id: CONDITION_ID,
        side: 'BUY',
        price: '0.55',
        original_size: '20',
        size_matched: '5',
        type: 'GTC',
        created_at: 1700000000000,
        outcome: 'Yes',
      },
    ]
    const fetchMock = makeFetch(ordersResponse)
    const adapter = makeAdapter(fetchMock)
    const orders = await adapter.getOpenOrders()

    expect(orders).toHaveLength(1)
    expect(orders[0]!.orderId).toBe('order-1')
    expect(orders[0]!.side).toBe('BUY')
    expect(orders[0]!.price).toBeCloseTo(0.55)
    expect(orders[0]!.size).toBe(20)
    expect(orders[0]!.filledSize).toBe(5)
  })

  it('should filter by conditionId when symbol provided', async () => {
    const otherConditionId = '0xother_condition'
    const ordersResponse = [
      {
        id: 'order-1',
        token_id: YES_TOKEN_ID,
        condition_id: CONDITION_ID,
        side: 'BUY',
        price: '0.55',
        original_size: '20',
        size_matched: '0',
        type: 'GTC',
        created_at: 1700000000000,
        outcome: 'Yes',
      },
      {
        id: 'order-2',
        token_id: '0xother_token',
        condition_id: otherConditionId,
        side: 'SELL',
        price: '0.80',
        original_size: '10',
        size_matched: '0',
        type: 'GTC',
        created_at: 1700000001000,
        outcome: 'Yes',
      },
    ]
    const fetchMock = makeFetch(ordersResponse)
    const adapter = makeAdapter(fetchMock)
    const orders = await adapter.getOpenOrders(`YES-${CONDITION_ID}`)

    expect(orders).toHaveLength(1)
    expect(orders[0]!.orderId).toBe('order-1')
  })
})

// ── getExchangeInfo ─────────────────────────────────────────────────────────

describe('PolymarketAdapter.getExchangeInfo', () => {
  it('should list active markets as supported symbols', async () => {
    const marketsResponse = [
      {
        condition_id: CONDITION_ID,
        question: 'Will ETH reach $10k?',
        tokens: [
          { token_id: YES_TOKEN_ID, outcome: 'Yes' },
          { token_id: NO_TOKEN_ID, outcome: 'No' },
        ],
        active: true,
        minimum_order_size: '5',
        minimum_tick_size: '0.01',
      },
      {
        condition_id: '0xother',
        question: 'Will BTC reach $200k?',
        tokens: [
          { token_id: '0xt3', outcome: 'Yes' },
          { token_id: '0xt4', outcome: 'No' },
        ],
        active: true,
        minimum_order_size: '10',
        minimum_tick_size: '0.01',
      },
    ]
    const fetchMock = makeFetch(marketsResponse)
    const adapter = makeAdapter(fetchMock)
    const info = await adapter.getExchangeInfo()

    expect(info.name).toBe('Polymarket')
    expect(info.testnet).toBe(false)
    // Each market produces YES- and NO- symbols
    expect(info.supportedSymbols).toContain(`YES-${CONDITION_ID}`)
    expect(info.supportedSymbols).toContain(`NO-${CONDITION_ID}`)
    expect(info.supportedSymbols).toContain('YES-0xother')
    expect(info.supportedSymbols).toContain('NO-0xother')
  })

  it('should set minOrderSizes and tickSizes for prediction markets', async () => {
    const marketsResponse = [
      {
        condition_id: CONDITION_ID,
        question: 'Test market',
        tokens: [
          { token_id: YES_TOKEN_ID, outcome: 'Yes' },
          { token_id: NO_TOKEN_ID, outcome: 'No' },
        ],
        active: true,
        minimum_order_size: '5',
        minimum_tick_size: '0.01',
      },
    ]
    const fetchMock = makeFetch(marketsResponse)
    const adapter = makeAdapter(fetchMock)
    const info = await adapter.getExchangeInfo()

    expect(info.minOrderSizes[`YES-${CONDITION_ID}`]).toBe(5)
    expect(info.minOrderSizes[`NO-${CONDITION_ID}`]).toBe(5)
    expect(info.tickSizes[`YES-${CONDITION_ID}`]).toBe(0.01)
    expect(info.tickSizes[`NO-${CONDITION_ID}`]).toBe(0.01)
  })
})

// ── getCandles ──────────────────────────────────────────────────────────────

describe('PolymarketAdapter.getCandles', () => {
  it('should fetch price history and map to Candle format', async () => {
    const historyResponse = {
      history: [
        { t: 1700000000, p: 0.55 },
        { t: 1700003600, p: 0.58 },
        { t: 1700007200, p: 0.56 },
        { t: 1700010800, p: 0.60 },
      ],
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(historyResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    const candles = await adapter.getCandles(`YES-${CONDITION_ID}`, '1h', 2)

    expect(candles).toHaveLength(2)
    const candle = candles[0]!
    expect(typeof candle.timestamp).toBe('number')
    expect(typeof candle.open).toBe('number')
    expect(typeof candle.high).toBe('number')
    expect(typeof candle.low).toBe('number')
    expect(typeof candle.close).toBe('number')
    expect(candle.volume).toBe(0) // prediction markets have no per-candle volume
  })
})

// ── setStopLoss ─────────────────────────────────────────────────────────────

describe('PolymarketAdapter.setStopLoss', () => {
  it('should throw "not supported for prediction markets"', async () => {
    const adapter = makeAdapter(makeFetch({}))
    await expect(
      adapter.setStopLoss(`YES-${CONDITION_ID}`, 'BUY', 0.30, 10)
    ).rejects.toThrow('not supported for prediction markets')
  })
})

// ── HMAC auth ───────────────────────────────────────────────────────────────

describe('PolymarketAdapter.HMAC auth', () => {
  it('should generate L1 API headers with timestamp and signature', async () => {
    const fetchMock = makeFetch([{ asset_type: 'USDC', balance: '1000' }])
    const adapter = makeAdapter(fetchMock)
    await adapter.getBalances()

    const init = getCallInit(fetchMock)
    const headers = init.headers as Record<string, string>
    expect(headers['POLY-ADDRESS']).toBeDefined()
    expect(headers['POLY-SIGNATURE']).toBeDefined()
    expect(headers['POLY-TIMESTAMP']).toBeDefined()
    expect(headers['POLY-NONCE']).toBeDefined()
  })

  it('should generate L2 API headers for order signing', async () => {
    const orderResponse = {
      id: 'order-auth-test',
      status: 'LIVE',
      size: '0',
      price: '0.50',
    }
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(MARKET_RESPONSE))
      .mockResolvedValueOnce(fakeResponse(orderResponse)) as FetchMock

    const adapter = makeAdapter(fetchMock)
    await adapter.placeOrder({
      symbol: `YES-${CONDITION_ID}`,
      side: 'BUY',
      size: 10,
      price: 0.50,
      orderType: 'GTC',
    })

    // The order POST should include auth headers
    const init = getCallInit(fetchMock, 1)
    const headers = init.headers as Record<string, string>
    expect(headers['POLY-ADDRESS']).toBeDefined()
    expect(headers['POLY-SIGNATURE']).toBeDefined()
    expect(headers['POLY-TIMESTAMP']).toBeDefined()
  })
})

// ── cancelAllOrders ─────────────────────────────────────────────────────────

describe('PolymarketAdapter.cancelAllOrders', () => {
  it('should cancel all open orders', async () => {
    const ordersResponse = [
      { id: 'order-1', token_id: YES_TOKEN_ID, condition_id: CONDITION_ID, side: 'BUY', price: '0.55', original_size: '20', size_matched: '0', type: 'GTC', created_at: 1700000000000, outcome: 'Yes' },
      { id: 'order-2', token_id: NO_TOKEN_ID, condition_id: CONDITION_ID, side: 'SELL', price: '0.45', original_size: '10', size_matched: '0', type: 'GTC', created_at: 1700000001000, outcome: 'No' },
    ]
    const fetchMock = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(ordersResponse)) // getOpenOrders
      .mockResolvedValueOnce(fakeResponse({ success: true })) // cancel 1
      .mockResolvedValueOnce(fakeResponse({ success: true })) as FetchMock // cancel 2

    const adapter = makeAdapter(fetchMock)
    await expect(adapter.cancelAllOrders()).resolves.toBeUndefined()
  })
})

// ── Error handling ──────────────────────────────────────────────────────────

describe('PolymarketAdapter error handling', () => {
  it('should throw on HTTP error status', async () => {
    const fetchMock = makeFetch({ error: 'Internal server error' }, 500)
    const adapter = makeAdapter(fetchMock)
    await expect(adapter.getBalances()).rejects.toThrow()
  })
})
