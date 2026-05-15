/**
 * Alpaca exchange adapter for US stock trading.
 * Implements ExchangeAdapterInterface using Alpaca Trading API v2.
 */

import type {
  ExchangeAdapterInterface,
  Ticker,
  OrderBook,
  Candle,
  Balance,
  Position,
  OrderRequest,
  OrderResult,
  OpenOrder,
  ExchangeInfo,
  OrderSide,
  OrderType,
} from '../types.js'

// ── Config ────────────────────────────────────────────────────────────────────

export interface AlpacaConfig {
  apiKey: string
  apiSecret: string
  paper: boolean             // true = paper trading, false = live
  dataFeed?: 'iex' | 'sip'  // IEX (free) or SIP (paid, NBBO)
}

// ── Alpaca raw API response types ─────────────────────────────────────────────

interface AlpacaSnapshot {
  latestTrade: { p: number; s: number; t: string }
  latestQuote: { bp: number; bs: number; ap: number; as: number; t: string }
  minuteBar: { o: number; h: number; l: number; c: number; v: number; t: string }
  dailyBar: { o: number; h: number; l: number; c: number; v: number; t: string }
  prevDailyBar: { o: number; h: number; l: number; c: number; v: number; t: string }
}

interface AlpacaQuoteResponse {
  quote: {
    bp: number; bs: number; ap: number; as: number; t: string
  }
}

interface AlpacaBar {
  t: string
  o: number
  h: number
  l: number
  c: number
  v: number
}

interface AlpacaBarsResponse {
  bars: AlpacaBar[]
}

interface AlpacaAccount {
  equity: string
  cash: string
  buying_power: string
  unrealized_pl: string
}

interface AlpacaPosition {
  symbol: string
  qty: string
  avg_entry_price: string
  current_price: string
  unrealized_pl: string
  market_value: string
}

interface AlpacaOrderResponse {
  id: string
  status: string
  filled_qty: string
  filled_avg_price: string | null
  created_at: string
}

interface AlpacaRawOrder {
  id: string
  symbol: string
  side: string
  qty: string
  filled_qty: string
  type: string
  limit_price: string
  time_in_force: string
  created_at: string
  status: string
}

interface AlpacaAsset {
  symbol: string
  status: string
  tradable: boolean
  min_order_size: string
  min_trade_increment: string
  price_increment: string
}

// ── Interval mapping ──────────────────────────────────────────────────────────

function toAlpacaTimeframe(interval: string): string {
  switch (interval) {
    case '1m': return '1Min'
    case '5m': return '5Min'
    case '15m': return '15Min'
    case '1h': return '1Hour'
    case '1d': return '1Day'
    default: return '1Min'
  }
}

// ── Order type mapping ────────────────────────────────────────────────────────

function toAlpacaTif(orderType: OrderType): string {
  switch (orderType) {
    case 'ALO': return 'day'   // ALO not available — map to day limit
    case 'GTC': return 'gtc'
    case 'IOC': return 'ioc'
  }
}

function fromAlpacaTif(tif: string): OrderType {
  switch (tif) {
    case 'gtc': return 'GTC'
    case 'ioc': return 'IOC'
    default: return 'GTC'
  }
}

// ── Order status mapping ──────────────────────────────────────────────────────

function toOrderStatus(alpacaStatus: string): 'FILLED' | 'PARTIAL' | 'OPEN' | 'REJECTED' {
  switch (alpacaStatus) {
    case 'filled': return 'FILLED'
    case 'partially_filled': return 'PARTIAL'
    case 'rejected':
    case 'canceled':
    case 'expired':
    case 'suspended': return 'REJECTED'
    default: return 'OPEN'
  }
}

// ── Main adapter ──────────────────────────────────────────────────────────────

export class AlpacaAdapter implements ExchangeAdapterInterface {
  readonly name = 'Alpaca'

  private readonly tradingUrl: string
  private readonly dataUrl: string
  private readonly apiKey: string
  private readonly apiSecret: string
  private readonly paper: boolean
  private readonly dataFeed: 'iex' | 'sip'

  // Injected in tests via _fetch; production uses global fetch
  _fetch: typeof fetch = (...args) => fetch(...args)

  constructor(config: AlpacaConfig) {
    this.paper = config.paper
    this.tradingUrl = config.paper
      ? 'https://paper-api.alpaca.markets'
      : 'https://api.alpaca.markets'
    this.dataUrl = 'https://data.alpaca.markets'
    this.apiKey = config.apiKey
    this.apiSecret = config.apiSecret
    this.dataFeed = config.dataFeed ?? 'iex'
  }

  // ── Public interface ───────────────────────────────────────────────────────

  async getTicker(symbol: string): Promise<Ticker> {
    const snapshot = await this.dataGet<AlpacaSnapshot>(
      `/v2/stocks/${symbol}/snapshot?feed=${this.dataFeed}`,
    )

    const bid = snapshot.latestQuote.bp
    const ask = snapshot.latestQuote.ap
    const mid = (bid + ask) / 2
    const lastPrice = snapshot.latestTrade.p
    const volume24h = snapshot.dailyBar.v

    return {
      symbol,
      mid,
      bid,
      ask,
      lastPrice,
      volume24h,
      openInterest: 0,
      fundingRate: 0,
      timestamp: Date.now(),
    }
  }

  async getOrderBook(symbol: string, _depth?: number): Promise<OrderBook> {
    const resp = await this.dataGet<AlpacaQuoteResponse>(
      `/v2/stocks/${symbol}/quotes/latest?feed=${this.dataFeed}`,
    )

    const q = resp.quote
    return {
      symbol,
      bids: [{ price: q.bp, size: q.bs }],
      asks: [{ price: q.ap, size: q.as }],
      timestamp: Date.now(),
    }
  }

  async getCandles(symbol: string, interval: string, limit: number): Promise<Candle[]> {
    const timeframe = toAlpacaTimeframe(interval)
    const resp = await this.dataGet<AlpacaBarsResponse>(
      `/v2/stocks/${symbol}/bars?timeframe=${timeframe}&limit=${limit}&feed=${this.dataFeed}`,
    )

    const bars = resp.bars ?? []
    return bars.slice(-limit).map((bar) => ({
      timestamp: new Date(bar.t).getTime(),
      open: bar.o,
      high: bar.h,
      low: bar.l,
      close: bar.c,
      volume: bar.v,
    }))
  }

  async getBalances(): Promise<Balance[]> {
    const account = await this.tradingGet<AlpacaAccount>('/v2/account')

    return [
      {
        currency: 'USD',
        available: parseFloat(account.cash),
        total: parseFloat(account.equity),
        unrealizedPnl: parseFloat(account.unrealized_pl),
      },
    ]
  }

  async getPositions(): Promise<Position[]> {
    const positions = await this.tradingGet<AlpacaPosition[]>('/v2/positions')

    return positions.map((p) => {
      const qty = parseFloat(p.qty)
      return {
        symbol: p.symbol,
        side: qty >= 0 ? ('LONG' as const) : ('SHORT' as const),
        size: Math.abs(qty),
        entryPrice: parseFloat(p.avg_entry_price),
        markPrice: parseFloat(p.current_price),
        unrealizedPnl: parseFloat(p.unrealized_pl),
        leverage: 1,
        liquidationPrice: null,
      }
    })
  }

  async placeOrder(order: OrderRequest): Promise<OrderResult> {
    const body = {
      symbol: order.symbol,
      qty: String(order.size),
      side: order.side.toLowerCase(),
      type: 'limit',
      time_in_force: toAlpacaTif(order.orderType),
      limit_price: String(order.price),
      ...(order.clientOrderId ? { client_order_id: order.clientOrderId } : {}),
    }

    const resp = await this.tradingPost<AlpacaOrderResponse>('/v2/orders', body)

    return {
      orderId: resp.id,
      status: toOrderStatus(resp.status),
      filledSize: parseFloat(resp.filled_qty),
      filledPrice: resp.filled_avg_price ? parseFloat(resp.filled_avg_price) : 0,
      timestamp: new Date(resp.created_at).getTime(),
    }
  }

  async cancelOrder(orderId: string): Promise<void> {
    await this.tradingDelete(`/v2/orders/${orderId}`)
  }

  async cancelAllOrders(symbol?: string): Promise<void> {
    if (!symbol) {
      await this.tradingDelete('/v2/orders')
      return
    }

    // Symbol-filtered cancel: fetch open orders, then cancel matching ones
    const openOrders = await this.tradingGet<AlpacaRawOrder[]>('/v2/orders?status=open')
    const matching = openOrders.filter((o) => o.symbol === symbol)
    for (const order of matching) {
      await this.tradingDelete(`/v2/orders/${order.id}`)
    }
  }

  async setStopLoss(
    symbol: string,
    side: OrderSide,
    triggerPrice: number,
    size: number,
  ): Promise<OrderResult> {
    // SL for a LONG (BUY) position -> sell on trigger
    // SL for a SHORT (SELL) position -> buy on trigger
    const closeSide = side === 'BUY' ? 'sell' : 'buy'

    const body = {
      symbol,
      qty: String(size),
      side: closeSide,
      type: 'stop',
      time_in_force: 'gtc',
      stop_price: String(triggerPrice),
    }

    const resp = await this.tradingPost<AlpacaOrderResponse>('/v2/orders', body)

    return {
      orderId: resp.id,
      status: toOrderStatus(resp.status),
      filledSize: parseFloat(resp.filled_qty),
      filledPrice: resp.filled_avg_price ? parseFloat(resp.filled_avg_price) : 0,
      timestamp: new Date(resp.created_at).getTime(),
    }
  }

  async getOpenOrders(symbol?: string): Promise<OpenOrder[]> {
    const rawOrders = await this.tradingGet<AlpacaRawOrder[]>('/v2/orders?status=open')

    const filtered = symbol
      ? rawOrders.filter((o) => o.symbol === symbol)
      : rawOrders

    return filtered.map((o) => ({
      orderId: o.id,
      symbol: o.symbol,
      side: o.side === 'buy' ? ('BUY' as OrderSide) : ('SELL' as OrderSide),
      price: parseFloat(o.limit_price),
      size: parseFloat(o.qty),
      filledSize: parseFloat(o.filled_qty),
      orderType: fromAlpacaTif(o.time_in_force),
      timestamp: new Date(o.created_at).getTime(),
    }))
  }

  async getExchangeInfo(): Promise<ExchangeInfo> {
    const assets = await this.tradingGet<AlpacaAsset[]>('/v2/assets?status=active')

    const supportedSymbols: string[] = []
    const minOrderSizes: Record<string, number> = {}
    const tickSizes: Record<string, number> = {}

    for (const asset of assets) {
      if (!asset.tradable) continue
      supportedSymbols.push(asset.symbol)
      minOrderSizes[asset.symbol] = parseFloat(asset.min_order_size || '1')
      tickSizes[asset.symbol] = parseFloat(asset.price_increment || '0.01')
    }

    return {
      name: 'Alpaca',
      testnet: this.paper,
      supportedSymbols,
      minOrderSizes,
      tickSizes,
    }
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  private authHeaders(): Record<string, string> {
    return {
      'APCA-API-KEY-ID': this.apiKey,
      'APCA-API-SECRET-KEY': this.apiSecret,
      'Content-Type': 'application/json',
    }
  }

  private async tradingGet<T>(path: string): Promise<T> {
    const resp = await this._fetch(`${this.tradingUrl}${path}`, {
      headers: this.authHeaders(),
    })
    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Alpaca trading API error ${resp.status}: ${text}`)
    }
    return resp.json() as Promise<T>
  }

  private async tradingPost<T>(path: string, body: Record<string, unknown>): Promise<T> {
    const resp = await this._fetch(`${this.tradingUrl}${path}`, {
      method: 'POST',
      headers: this.authHeaders(),
      body: JSON.stringify(body),
    })
    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Alpaca trading API error ${resp.status}: ${text}`)
    }
    return resp.json() as Promise<T>
  }

  private async tradingDelete(path: string): Promise<void> {
    const resp = await this._fetch(`${this.tradingUrl}${path}`, {
      method: 'DELETE',
      headers: this.authHeaders(),
    })
    // 204 No Content and 207 Multi-Status are both success for DELETEs
    if (!resp.ok && resp.status !== 204 && resp.status !== 207) {
      const text = await resp.text()
      throw new Error(`Alpaca trading API error ${resp.status}: ${text}`)
    }
  }

  private async dataGet<T>(path: string): Promise<T> {
    const resp = await this._fetch(`${this.dataUrl}${path}`, {
      headers: this.authHeaders(),
    })
    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Alpaca data API error ${resp.status}: ${text}`)
    }
    return resp.json() as Promise<T>
  }
}
