import { describe, it, expect, jest } from '@jest/globals'
import { BinanceAdapter } from './binance.js'
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

function makeAdapter(fetchMock: FetchMock): BinanceAdapter {
  const adapter = new BinanceAdapter({
    apiKey: 'testApiKey123',
    secretKey: 'testSecretKey456',
    testnet: true,
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

// ── getTicker ────────────────────────────────────────────────────────────────

describe('BinanceAdapter.getTicker', () => {
  it('returns a Ticker for ETH-PERP from 24hr ticker + premiumIndex', async () => {
    const tickerResp = {
      symbol: 'ETHUSDT',
      lastPrice: '3450.50',
      volume: '125000.5',
      openInterest: '50000.0',
      priceChangePercent: '2.5',
    }
    const premiumResp = {
      symbol: 'ETHUSDT',
      markPrice: '3450.00',
      lastFundingRate: '0.0001',
    }
    const bookTickerResp = {
      symbol: 'ETHUSDT',
      bidPrice: '3449.50',
      askPrice: '3451.00',
    }
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(tickerResp))
      .mockResolvedValueOnce(fakeResponse(premiumResp))
      .mockResolvedValueOnce(fakeResponse(bookTickerResp)) as FetchMock
    const adapter = makeAdapter(fetch)
    const ticker = await adapter.getTicker('ETH-PERP')

    expect(ticker.symbol).toBe('ETH-PERP')
    expect(ticker.lastPrice).toBeCloseTo(3450.5)
    expect(ticker.bid).toBeCloseTo(3449.5)
    expect(ticker.ask).toBeCloseTo(3451.0)
    expect(ticker.mid).toBeCloseTo((3449.5 + 3451.0) / 2)
    expect(ticker.fundingRate).toBeCloseTo(0.0001)
    expect(ticker.volume24h).toBeCloseTo(125000.5)
    expect(typeof ticker.timestamp).toBe('number')
  })

  it('throws for unknown symbol on HTTP error', async () => {
    const errorResp = { code: -1121, msg: 'Invalid symbol.' }
    const fetch = makeFetch(errorResp, 400)
    const adapter = makeAdapter(fetch)
    await expect(adapter.getTicker('AAPL-PERP')).rejects.toThrow()
  })
})

// ── getOrderBook ─────────────────────────────────────────────────────────────

describe('BinanceAdapter.getOrderBook', () => {
  it('parses bids and asks from /fapi/v1/depth response', async () => {
    const depthResp = {
      bids: [
        ['3449.00', '1.500'],
        ['3448.00', '2.000'],
      ],
      asks: [
        ['3451.00', '1.000'],
        ['3452.00', '3.500'],
      ],
    }
    const fetch = makeFetch(depthResp)
    const adapter = makeAdapter(fetch)
    const book = await adapter.getOrderBook('ETH-PERP', 10)

    expect(book.symbol).toBe('ETH-PERP')
    expect(book.bids).toHaveLength(2)
    expect(book.asks).toHaveLength(2)
    expect(book.bids[0]).toEqual({ price: 3449.0, size: 1.5 })
    expect(book.asks[0]).toEqual({ price: 3451.0, size: 1.0 })
    expect(typeof book.timestamp).toBe('number')
  })

  it('sends the correct symbol in the request URL', async () => {
    const depthResp = { bids: [], asks: [] }
    const fetch = makeFetch(depthResp)
    const adapter = makeAdapter(fetch)
    await adapter.getOrderBook('BTC-PERP')

    const url = getCallUrl(fetch)
    expect(url).toContain('symbol=BTCUSDT')
  })

  it('respects depth limit parameter', async () => {
    const depthResp = { bids: [], asks: [] }
    const fetch = makeFetch(depthResp)
    const adapter = makeAdapter(fetch)
    await adapter.getOrderBook('ETH-PERP', 5)

    const url = getCallUrl(fetch)
    expect(url).toContain('limit=5')
  })
})

// ── getCandles ───────────────────────────────────────────────────────────────

describe('BinanceAdapter.getCandles', () => {
  it('parses kline data from /fapi/v1/klines', async () => {
    // Binance kline format: [openTime, o, h, l, c, v, closeTime, ...]
    const rawKlines = [
      [1700000000000, '3400.0', '3500.0', '3350.0', '3450.0', '1000.0', 1700003599999, '0', 0, '0', '0', '0'],
      [1700003600000, '3450.0', '3480.0', '3420.0', '3460.0', '800.0', 1700007199999, '0', 0, '0', '0', '0'],
    ]
    const fetch = makeFetch(rawKlines)
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

  it('requests the correct interval and symbol', async () => {
    const fetch = makeFetch([])
    const adapter = makeAdapter(fetch)
    await adapter.getCandles('SOL-PERP', '15m', 50)

    const url = getCallUrl(fetch)
    expect(url).toContain('symbol=SOLUSDT')
    expect(url).toContain('interval=15m')
    expect(url).toContain('limit=50')
  })
})

// ── getBalances ──────────────────────────────────────────────────────────────

describe('BinanceAdapter.getBalances', () => {
  it('parses account balance from /fapi/v2/balance', async () => {
    const balanceResp = [
      {
        asset: 'USDT',
        balance: '10500.00000000',
        crossWalletBalance: '10000.00000000',
        availableBalance: '9500.00000000',
        crossUnPnl: '75.00000000',
      },
      {
        asset: 'BNB',
        balance: '0.50000000',
        crossWalletBalance: '0.50000000',
        availableBalance: '0.50000000',
        crossUnPnl: '0.00000000',
      },
    ]
    const fetch = makeFetch(balanceResp)
    const adapter = makeAdapter(fetch)
    const balances = await adapter.getBalances()

    // Should return USDT balance
    const usdt = balances.find((b) => b.currency === 'USDT')
    expect(usdt).toBeDefined()
    expect(usdt!.total).toBeCloseTo(10500.0)
    expect(usdt!.available).toBeCloseTo(9500.0)
    expect(usdt!.unrealizedPnl).toBeCloseTo(75.0)
  })

  it('includes signed parameters in request', async () => {
    const fetch = makeFetch([])
    const adapter = makeAdapter(fetch)
    await adapter.getBalances()

    const url = getCallUrl(fetch)
    expect(url).toContain('timestamp=')
    expect(url).toContain('signature=')
    expect(url).toContain('recvWindow=')
  })
})

// ── getPositions ─────────────────────────────────────────────────────────────

describe('BinanceAdapter.getPositions', () => {
  it('parses positions from /fapi/v2/positionRisk', async () => {
    const positionResp = [
      {
        symbol: 'ETHUSDT',
        positionAmt: '1.500',
        entryPrice: '3400.00000',
        markPrice: '3450.00000',
        unRealizedProfit: '75.00000000',
        leverage: '10',
        liquidationPrice: '3200.00000',
        positionSide: 'BOTH',
      },
    ]
    const fetch = makeFetch(positionResp)
    const adapter = makeAdapter(fetch)
    const positions = await adapter.getPositions()

    expect(positions).toHaveLength(1)
    const pos = positions[0]!
    expect(pos.symbol).toBe('ETH-PERP')
    expect(pos.side).toBe('LONG')
    expect(pos.size).toBeCloseTo(1.5)
    expect(pos.entryPrice).toBeCloseTo(3400.0)
    expect(pos.markPrice).toBeCloseTo(3450.0)
    expect(pos.unrealizedPnl).toBeCloseTo(75.0)
    expect(pos.leverage).toBeCloseTo(10)
    expect(pos.liquidationPrice).toBeCloseTo(3200.0)
  })

  it('sets side SHORT when positionAmt is negative', async () => {
    const positionResp = [
      {
        symbol: 'BTCUSDT',
        positionAmt: '-0.500',
        entryPrice: '67000.00000',
        markPrice: '67200.00000',
        unRealizedProfit: '-100.00000000',
        leverage: '5',
        liquidationPrice: '70000.00000',
        positionSide: 'BOTH',
      },
    ]
    const fetch = makeFetch(positionResp)
    const adapter = makeAdapter(fetch)
    const positions = await adapter.getPositions()

    expect(positions[0]!.side).toBe('SHORT')
    expect(positions[0]!.size).toBeCloseTo(0.5)
  })

  it('filters out zero-size positions', async () => {
    const positionResp = [
      {
        symbol: 'ETHUSDT',
        positionAmt: '0.000',
        entryPrice: '0.00000',
        markPrice: '3450.00000',
        unRealizedProfit: '0.00000000',
        leverage: '10',
        liquidationPrice: '0.00000',
        positionSide: 'BOTH',
      },
    ]
    const fetch = makeFetch(positionResp)
    const adapter = makeAdapter(fetch)
    const positions = await adapter.getPositions()

    expect(positions).toHaveLength(0)
  })

  it('handles liquidationPrice of 0 as null', async () => {
    const positionResp = [
      {
        symbol: 'ETHUSDT',
        positionAmt: '1.0',
        entryPrice: '3400.00000',
        markPrice: '3450.00000',
        unRealizedProfit: '50.00000000',
        leverage: '1',
        liquidationPrice: '0',
        positionSide: 'BOTH',
      },
    ]
    const fetch = makeFetch(positionResp)
    const adapter = makeAdapter(fetch)
    const positions = await adapter.getPositions()

    expect(positions[0]!.liquidationPrice).toBeNull()
  })
})

// ── placeOrder ───────────────────────────────────────────────────────────────

describe('BinanceAdapter.placeOrder', () => {
  it('places a LIMIT GTC order and returns OrderResult', async () => {
    const orderResp = {
      orderId: 12345,
      symbol: 'ETHUSDT',
      status: 'NEW',
      executedQty: '0.00000',
      avgPrice: '0.00000',
      updateTime: 1700000000000,
    }
    const fetch = makeFetch(orderResp)
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
    const orderResp = {
      orderId: 99,
      symbol: 'ETHUSDT',
      status: 'FILLED',
      executedQty: '0.10000',
      avgPrice: '3401.00000',
      updateTime: 1700000000000,
    }
    const fetch = makeFetch(orderResp)
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

  it('returns PARTIAL status when order is partially filled', async () => {
    const orderResp = {
      orderId: 100,
      symbol: 'ETHUSDT',
      status: 'PARTIALLY_FILLED',
      executedQty: '0.05000',
      avgPrice: '3400.50000',
      updateTime: 1700000000000,
    }
    const fetch = makeFetch(orderResp)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'ETH-PERP',
      side: 'SELL',
      size: 0.1,
      price: 3400.0,
      orderType: 'GTC',
    }
    const result = await adapter.placeOrder(order)

    expect(result.status).toBe('PARTIAL')
    expect(result.filledSize).toBeCloseTo(0.05)
  })

  it('handles LIMIT ALO by setting timeInForce=GTX', async () => {
    const orderResp = {
      orderId: 200,
      symbol: 'ETHUSDT',
      status: 'NEW',
      executedQty: '0.00000',
      avgPrice: '0.00000',
      updateTime: 1700000000000,
    }
    const fetch = makeFetch(orderResp)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'ETH-PERP',
      side: 'BUY',
      size: 0.1,
      price: 3400.0,
      orderType: 'ALO',
    }
    await adapter.placeOrder(order)

    const url = getCallUrl(fetch)
    expect(url).toContain('timeInForce=GTX')
  })

  it('sends correct request parameters', async () => {
    const orderResp = {
      orderId: 300,
      symbol: 'ETHUSDT',
      status: 'NEW',
      executedQty: '0.00000',
      avgPrice: '0.00000',
      updateTime: 1700000000000,
    }
    const fetch = makeFetch(orderResp)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'ETH-PERP',
      side: 'BUY',
      size: 0.1,
      price: 3400.0,
      orderType: 'GTC',
      reduceOnly: true,
    }
    await adapter.placeOrder(order)

    const url = getCallUrl(fetch)
    expect(url).toContain('symbol=ETHUSDT')
    expect(url).toContain('side=BUY')
    expect(url).toContain('quantity=0.1')
    expect(url).toContain('price=3400')
    expect(url).toContain('type=LIMIT')
    expect(url).toContain('timeInForce=GTC')
    expect(url).toContain('reduceOnly=true')
    expect(url).toContain('timestamp=')
    expect(url).toContain('signature=')

    const init = getCallInit(fetch)
    expect(init.method).toBe('POST')
  })

  it('throws on order rejection', async () => {
    const errorResp = { code: -2010, msg: 'Account has insufficient balance for requested action.' }
    const fetch = makeFetch(errorResp, 400)
    const adapter = makeAdapter(fetch)

    const order: OrderRequest = {
      symbol: 'ETH-PERP',
      side: 'BUY',
      size: 100000,
      price: 3400.0,
      orderType: 'GTC',
    }
    await expect(adapter.placeOrder(order)).rejects.toThrow()
  })
})

// ── cancelOrder ──────────────────────────────────────────────────────────────

describe('BinanceAdapter.cancelOrder', () => {
  it('sends DELETE /fapi/v1/order with orderId', async () => {
    const cancelResp = {
      orderId: 99999,
      symbol: 'ETHUSDT',
      status: 'CANCELED',
    }
    const fetch = makeFetch(cancelResp)
    const adapter = makeAdapter(fetch)
    await adapter.cancelOrder('99999:ETHUSDT')

    const url = getCallUrl(fetch)
    expect(url).toContain('/fapi/v1/order')
    expect(url).toContain('orderId=99999')
    expect(url).toContain('symbol=ETHUSDT')

    const init = getCallInit(fetch)
    expect(init.method).toBe('DELETE')
  })

  it('resolves without error on success', async () => {
    const cancelResp = { orderId: 123, status: 'CANCELED' }
    const fetch = makeFetch(cancelResp)
    const adapter = makeAdapter(fetch)
    await expect(adapter.cancelOrder('123:ETHUSDT')).resolves.toBeUndefined()
  })

  it('throws on cancel failure', async () => {
    const errorResp = { code: -2011, msg: 'Unknown order sent.' }
    const fetch = makeFetch(errorResp, 400)
    const adapter = makeAdapter(fetch)
    await expect(adapter.cancelOrder('999:ETHUSDT')).rejects.toThrow()
  })
})

// ── cancelAllOrders ──────────────────────────────────────────────────────────

describe('BinanceAdapter.cancelAllOrders', () => {
  it('sends DELETE /fapi/v1/allOpenOrders with symbol', async () => {
    const cancelResp = { code: 200, msg: 'The operation of cancel all open orders is done.' }
    const fetch = makeFetch(cancelResp)
    const adapter = makeAdapter(fetch)
    await adapter.cancelAllOrders('ETH-PERP')

    const url = getCallUrl(fetch)
    expect(url).toContain('/fapi/v1/allOpenOrders')
    expect(url).toContain('symbol=ETHUSDT')

    const init = getCallInit(fetch)
    expect(init.method).toBe('DELETE')
  })

  it('cancels all orders when no symbol given by fetching open orders first', async () => {
    const openOrdersResp = [
      { symbol: 'ETHUSDT', orderId: 1 },
      { symbol: 'BTCUSDT', orderId: 2 },
    ]
    // First call: fetch open orders, then two cancel-all-per-symbol calls
    const cancelResp = { code: 200, msg: 'Done.' }
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockResolvedValueOnce(fakeResponse(openOrdersResp))
      .mockResolvedValueOnce(fakeResponse(cancelResp))
      .mockResolvedValueOnce(fakeResponse(cancelResp)) as FetchMock
    const adapter = makeAdapter(fetch)
    await adapter.cancelAllOrders()

    // Should have called open orders GET, then DELETE for each unique symbol
    expect(fetch).toHaveBeenCalledTimes(3)
  })
})

// ── setStopLoss ───────────────────────────────────────────────────────────────

describe('BinanceAdapter.setStopLoss', () => {
  it('places a STOP_MARKET order with stopPrice', async () => {
    const orderResp = {
      orderId: 55555,
      symbol: 'ETHUSDT',
      status: 'NEW',
      executedQty: '0.00000',
      avgPrice: '0.00000',
      updateTime: 1700000000000,
    }
    const fetch = makeFetch(orderResp)
    const adapter = makeAdapter(fetch)
    const result = await adapter.setStopLoss('ETH-PERP', 'BUY', 3300.0, 0.5)

    expect(result.orderId).toBe('55555')

    const url = getCallUrl(fetch)
    expect(url).toContain('type=STOP_MARKET')
    expect(url).toContain('stopPrice=3300')
    expect(url).toContain('side=SELL') // SL for BUY position -> SELL
    expect(url).toContain('quantity=0.5')
    expect(url).toContain('reduceOnly=true')
  })

  it('sets side BUY for stop loss on SHORT position', async () => {
    const orderResp = {
      orderId: 55556,
      symbol: 'ETHUSDT',
      status: 'NEW',
      executedQty: '0.00000',
      avgPrice: '0.00000',
      updateTime: 1700000000000,
    }
    const fetch = makeFetch(orderResp)
    const adapter = makeAdapter(fetch)
    await adapter.setStopLoss('ETH-PERP', 'SELL', 3600.0, 0.5)

    const url = getCallUrl(fetch)
    expect(url).toContain('side=BUY')
  })
})

// ── getOpenOrders ────────────────────────────────────────────────────────────

describe('BinanceAdapter.getOpenOrders', () => {
  it('returns open orders from /fapi/v1/openOrders', async () => {
    const rawOrders = [
      {
        orderId: 1,
        symbol: 'ETHUSDT',
        side: 'BUY',
        price: '3400.00000',
        origQty: '0.10000',
        executedQty: '0.00000',
        type: 'LIMIT',
        timeInForce: 'GTC',
        time: 1700000000000,
        status: 'NEW',
      },
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
    expect(o.filledSize).toBeCloseTo(0.0)
    expect(o.orderType).toBe('GTC')
  })

  it('filters by symbol when provided', async () => {
    const rawOrders = [
      {
        orderId: 1,
        symbol: 'ETHUSDT',
        side: 'BUY',
        price: '3400.00000',
        origQty: '0.10000',
        executedQty: '0.00000',
        type: 'LIMIT',
        timeInForce: 'GTC',
        time: 1700000000000,
        status: 'NEW',
      },
    ]
    const fetch = makeFetch(rawOrders)
    const adapter = makeAdapter(fetch)
    await adapter.getOpenOrders('ETH-PERP')

    const url = getCallUrl(fetch)
    expect(url).toContain('symbol=ETHUSDT')
  })

  it('maps GTX timeInForce to ALO order type', async () => {
    const rawOrders = [
      {
        orderId: 2,
        symbol: 'ETHUSDT',
        side: 'SELL',
        price: '3500.00000',
        origQty: '0.20000',
        executedQty: '0.05000',
        type: 'LIMIT',
        timeInForce: 'GTX',
        time: 1700000000000,
        status: 'NEW',
      },
    ]
    const fetch = makeFetch(rawOrders)
    const adapter = makeAdapter(fetch)
    const orders = await adapter.getOpenOrders()

    expect(orders[0]!.orderType).toBe('ALO')
  })
})

// ── getExchangeInfo ──────────────────────────────────────────────────────────

describe('BinanceAdapter.getExchangeInfo', () => {
  it('returns symbols, minQty, tickSize from /fapi/v1/exchangeInfo', async () => {
    const exchangeInfoResp = {
      symbols: [
        {
          symbol: 'ETHUSDT',
          status: 'TRADING',
          filters: [
            { filterType: 'LOT_SIZE', minQty: '0.001', stepSize: '0.001' },
            { filterType: 'PRICE_FILTER', tickSize: '0.01' },
          ],
        },
        {
          symbol: 'BTCUSDT',
          status: 'TRADING',
          filters: [
            { filterType: 'LOT_SIZE', minQty: '0.00001', stepSize: '0.00001' },
            { filterType: 'PRICE_FILTER', tickSize: '0.10' },
          ],
        },
        {
          symbol: 'SOLUSDT',
          status: 'BREAK', // non-TRADING status should be excluded
          filters: [],
        },
      ],
    }
    const fetch = makeFetch(exchangeInfoResp)
    const adapter = makeAdapter(fetch)
    const info = await adapter.getExchangeInfo()

    expect(info.name).toBe('Binance')
    expect(info.testnet).toBe(true)
    expect(info.supportedSymbols).toContain('ETH-PERP')
    expect(info.supportedSymbols).toContain('BTC-PERP')
    expect(info.supportedSymbols).not.toContain('SOL-PERP')
    expect(info.minOrderSizes['ETH-PERP']).toBeCloseTo(0.001)
    expect(info.minOrderSizes['BTC-PERP']).toBeCloseTo(0.00001)
    expect(info.tickSizes['ETH-PERP']).toBeCloseTo(0.01)
    expect(info.tickSizes['BTC-PERP']).toBeCloseTo(0.1)
  })
})

// ── Symbol conversion ─────────────────────────────────────────────────────────

describe('Symbol conversion', () => {
  it('converts ETH-PERP to ETHUSDT for API calls', async () => {
    const depthResp = { bids: [], asks: [] }
    const fetch = makeFetch(depthResp)
    const adapter = makeAdapter(fetch)
    await adapter.getOrderBook('ETH-PERP')

    const url = getCallUrl(fetch)
    expect(url).toContain('symbol=ETHUSDT')
  })

  it('converts ETHUSDT back to ETH-PERP in responses', async () => {
    const rawOrders = [
      {
        orderId: 1,
        symbol: 'ETHUSDT',
        side: 'BUY',
        price: '3400.00000',
        origQty: '0.10000',
        executedQty: '0.00000',
        type: 'LIMIT',
        timeInForce: 'GTC',
        time: 1700000000000,
        status: 'NEW',
      },
    ]
    const fetch = makeFetch(rawOrders)
    const adapter = makeAdapter(fetch)
    const orders = await adapter.getOpenOrders()
    expect(orders[0]!.symbol).toBe('ETH-PERP')
  })
})

// ── Error handling ────────────────────────────────────────────────────────────

describe('Error handling', () => {
  it('throws on HTTP error with Binance error code', async () => {
    const errorResp = { code: -1100, msg: 'Illegal characters found in a parameter.' }
    const fetch = makeFetch(errorResp, 400)
    const adapter = makeAdapter(fetch)
    await expect(adapter.getBalances()).rejects.toThrow('Binance')
  })

  it('throws on network failure', async () => {
    const fetch = jest.fn<(...args: unknown[]) => Promise<FakeResponse>>()
      .mockRejectedValue(new Error('ECONNREFUSED')) as FetchMock
    const adapter = makeAdapter(fetch)
    await expect(adapter.getBalances()).rejects.toThrow('ECONNREFUSED')
  })

  it('includes recvWindow in signed requests', async () => {
    const fetch = makeFetch([])
    const adapter = makeAdapter(fetch)
    await adapter.getBalances()

    const url = getCallUrl(fetch)
    expect(url).toContain('recvWindow=')
  })

  it('includes X-MBX-APIKEY header in all requests', async () => {
    const depthResp = { bids: [], asks: [] }
    const fetch = makeFetch(depthResp)
    const adapter = makeAdapter(fetch)
    await adapter.getOrderBook('ETH-PERP')

    const init = getCallInit(fetch)
    const headers = init.headers as Record<string, string>
    expect(headers['X-MBX-APIKEY']).toBe('testApiKey123')
  })

  it('uses testnet URL when testnet is true', () => {
    const adapter = new BinanceAdapter({
      apiKey: 'key',
      secretKey: 'secret',
      testnet: true,
    })
    // Verify via a property or by making a request
    expect((adapter as unknown as { baseUrl: string }).baseUrl).toBe('https://testnet.binancefuture.com')
  })

  it('uses mainnet URL when testnet is false', () => {
    const adapter = new BinanceAdapter({
      apiKey: 'key',
      secretKey: 'secret',
      testnet: false,
    })
    expect((adapter as unknown as { baseUrl: string }).baseUrl).toBe('https://fapi.binance.com')
  })
})
