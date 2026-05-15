/**
 * Binance USDM Futures exchange adapter.
 * Implements ExchangeAdapterInterface using native fetch (Node 22).
 */

import type {
  ExchangeAdapterInterface,
  Ticker,
  OrderBook,
  OrderBookLevel,
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

import { BnSigner } from './bn-signer.js'

// ── Config ────────────────────────────────────────────────────────────────────

export interface BinanceConfig {
  apiKey: string
  secretKey: string
  testnet: boolean
  /** 'futures' (default) or 'spot' */
  market?: 'futures' | 'spot'
}

// ── Binance raw API types ──────────────────────────────────────────────────

interface BnTicker24hr {
  symbol: string
  lastPrice: string
  volume: string
  openInterest?: string
  priceChangePercent: string
}

interface BnPremiumIndex {
  symbol: string
  markPrice: string
  lastFundingRate: string
}

interface BnBookTicker {
  symbol: string
  bidPrice: string
  askPrice: string
}

interface BnDepth {
  bids: [string, string][]
  asks: [string, string][]
}

type BnKline = [
  number,  // openTime
  string,  // open
  string,  // high
  string,  // low
  string,  // close
  string,  // volume
  number,  // closeTime
  ...unknown[],
]

interface BnBalance {
  asset: string
  balance: string
  crossWalletBalance: string
  availableBalance: string
  crossUnPnl: string
}

interface BnPositionRisk {
  symbol: string
  positionAmt: string
  entryPrice: string
  markPrice: string
  unRealizedProfit: string
  leverage: string
  liquidationPrice: string
  positionSide: string
}

interface BnOrderResponse {
  orderId: number
  symbol: string
  status: string
  executedQty: string
  avgPrice: string
  updateTime: number
}

interface BnOpenOrder {
  orderId: number
  symbol: string
  side: string
  price: string
  origQty: string
  executedQty: string
  type: string
  timeInForce: string
  time: number
  status: string
}

interface BnExchangeInfoSymbol {
  symbol: string
  status: string
  filters: Array<{
    filterType: string
    minQty?: string
    tickSize?: string
  }>
}

interface BnExchangeInfoResponse {
  symbols: BnExchangeInfoSymbol[]
}

interface BnErrorResponse {
  code: number
  msg: string
}

// ── Constants ────────────────────────────────────────────────────────────────

const RECV_WINDOW = '5000'

// ── Symbol utilities ──────────────────────────────────────────────────────────

function toBnSymbol(symbol: string): string {
  // "ETH-PERP" → "ETHUSDT"
  return symbol.replace(/-PERP$/, '') + 'USDT'
}

function fromBnSymbol(bnSymbol: string): string {
  // "ETHUSDT" → "ETH-PERP"
  return bnSymbol.replace(/USDT$/, '') + '-PERP'
}

// ── Order type mapping ────────────────────────────────────────────────────────

function toBnTimeInForce(orderType: OrderType): string {
  switch (orderType) {
    case 'ALO': return 'GTX'
    case 'GTC': return 'GTC'
    case 'IOC': return 'IOC'
  }
}

function fromBnTimeInForce(tif: string): OrderType {
  switch (tif) {
    case 'GTX': return 'ALO'
    case 'IOC': return 'IOC'
    default: return 'GTC'
  }
}

// ── Status mapping ────────────────────────────────────────────────────────────

function fromBnStatus(bnStatus: string): 'FILLED' | 'PARTIAL' | 'OPEN' | 'REJECTED' {
  switch (bnStatus) {
    case 'NEW': return 'OPEN'
    case 'FILLED': return 'FILLED'
    case 'PARTIALLY_FILLED': return 'PARTIAL'
    case 'CANCELED':
    case 'REJECTED':
    case 'EXPIRED':
      return 'REJECTED'
    default: return 'OPEN'
  }
}

// ── Main adapter ──────────────────────────────────────────────────────────────

export class BinanceAdapter implements ExchangeAdapterInterface {
  readonly name = 'Binance'

  private readonly baseUrl: string
  private readonly apiKey: string
  private readonly signer: BnSigner
  private readonly market: 'futures' | 'spot'

  // Injected in tests via _fetch; production uses global fetch
  _fetch: typeof fetch = (...args) => fetch(...args)

  constructor(config: BinanceConfig) {
    this.market = config.market ?? 'futures'
    this.baseUrl = config.testnet
      ? (this.market === 'futures'
          ? 'https://testnet.binancefuture.com'
          : 'https://testnet.binance.vision')
      : (this.market === 'futures'
          ? 'https://fapi.binance.com'
          : 'https://api.binance.com')
    this.apiKey = config.apiKey
    this.signer = new BnSigner(config.secretKey)
  }

  // ── Public interface ───────────────────────────────────────────────────────

  async getTicker(symbol: string): Promise<Ticker> {
    const bnSymbol = toBnSymbol(symbol)

    const [ticker24hr, premium, bookTicker] = await Promise.all([
      this.publicGet<BnTicker24hr>('/fapi/v1/ticker/24hr', { symbol: bnSymbol }),
      this.publicGet<BnPremiumIndex>('/fapi/v1/premiumIndex', { symbol: bnSymbol }),
      this.publicGet<BnBookTicker>('/fapi/v1/ticker/bookTicker', { symbol: bnSymbol }),
    ])

    const bid = parseFloat(bookTicker.bidPrice)
    const ask = parseFloat(bookTicker.askPrice)
    const mid = (bid + ask) / 2

    return {
      symbol,
      mid,
      bid,
      ask,
      lastPrice: parseFloat(ticker24hr.lastPrice),
      volume24h: parseFloat(ticker24hr.volume),
      openInterest: ticker24hr.openInterest ? parseFloat(ticker24hr.openInterest) : 0,
      fundingRate: parseFloat(premium.lastFundingRate),
      timestamp: Date.now(),
    }
  }

  async getOrderBook(symbol: string, depth = 20): Promise<OrderBook> {
    const bnSymbol = toBnSymbol(symbol)
    const raw = await this.publicGet<BnDepth>('/fapi/v1/depth', {
      symbol: bnSymbol,
      limit: String(depth),
    })

    const bids: OrderBookLevel[] = (raw.bids ?? []).map((level) => ({
      price: parseFloat(level[0]),
      size: parseFloat(level[1]),
    }))
    const asks: OrderBookLevel[] = (raw.asks ?? []).map((level) => ({
      price: parseFloat(level[0]),
      size: parseFloat(level[1]),
    }))

    return { symbol, bids, asks, timestamp: Date.now() }
  }

  async getCandles(symbol: string, interval: string, limit: number): Promise<Candle[]> {
    const bnSymbol = toBnSymbol(symbol)
    const raw = await this.publicGet<BnKline[]>('/fapi/v1/klines', {
      symbol: bnSymbol,
      interval,
      limit: String(limit),
    })

    return raw.map((k) => ({
      timestamp: k[0],
      open: parseFloat(k[1]),
      high: parseFloat(k[2]),
      low: parseFloat(k[3]),
      close: parseFloat(k[4]),
      volume: parseFloat(k[5]),
    }))
  }

  async getBalances(): Promise<Balance[]> {
    const raw = await this.signedGet<BnBalance[]>('/fapi/v2/balance')

    return raw
      .filter((b) => parseFloat(b.balance) !== 0)
      .map((b) => ({
        currency: b.asset,
        available: parseFloat(b.availableBalance),
        total: parseFloat(b.balance),
        unrealizedPnl: parseFloat(b.crossUnPnl),
      }))
  }

  async getPositions(): Promise<Position[]> {
    const raw = await this.signedGet<BnPositionRisk[]>('/fapi/v2/positionRisk')

    return raw
      .filter((p) => parseFloat(p.positionAmt) !== 0)
      .map((p) => {
        const amt = parseFloat(p.positionAmt)
        const liqPrice = parseFloat(p.liquidationPrice)

        return {
          symbol: fromBnSymbol(p.symbol),
          side: amt >= 0 ? ('LONG' as const) : ('SHORT' as const),
          size: Math.abs(amt),
          entryPrice: parseFloat(p.entryPrice),
          markPrice: parseFloat(p.markPrice),
          unrealizedPnl: parseFloat(p.unRealizedProfit),
          leverage: parseFloat(p.leverage),
          liquidationPrice: liqPrice === 0 ? null : liqPrice,
        }
      })
  }

  async placeOrder(order: OrderRequest): Promise<OrderResult> {
    const bnSymbol = toBnSymbol(order.symbol)

    const params: Record<string, string> = {
      symbol: bnSymbol,
      side: order.side,
      type: 'LIMIT',
      quantity: String(order.size),
      price: String(order.price),
      timeInForce: toBnTimeInForce(order.orderType),
    }

    if (order.reduceOnly) {
      params['reduceOnly'] = 'true'
    }

    if (order.clientOrderId) {
      params['newClientOrderId'] = order.clientOrderId
    }

    const resp = await this.signedPost<BnOrderResponse>('/fapi/v1/order', params)

    return {
      orderId: String(resp.orderId),
      status: fromBnStatus(resp.status),
      filledSize: parseFloat(resp.executedQty),
      filledPrice: parseFloat(resp.avgPrice),
      timestamp: resp.updateTime,
    }
  }

  async cancelOrder(orderId: string): Promise<void> {
    // orderId format: "orderId:symbol" e.g. "99999:ETHUSDT"
    const [oidStr, bnSymbol] = orderId.split(':')

    const params: Record<string, string> = {
      orderId: oidStr!,
    }
    if (bnSymbol) {
      params['symbol'] = bnSymbol
    }

    await this.signedDelete<unknown>('/fapi/v1/order', params)
  }

  async cancelAllOrders(symbol?: string): Promise<void> {
    if (symbol) {
      const bnSymbol = toBnSymbol(symbol)
      await this.signedDelete<unknown>('/fapi/v1/allOpenOrders', { symbol: bnSymbol })
      return
    }

    // No symbol specified: fetch open orders to get unique symbols, then cancel each
    const openOrders = await this.signedGet<BnOpenOrder[]>('/fapi/v1/openOrders')
    const uniqueSymbols = [...new Set(openOrders.map((o) => o.symbol))]

    await Promise.all(
      uniqueSymbols.map((sym) =>
        this.signedDelete<unknown>('/fapi/v1/allOpenOrders', { symbol: sym }),
      ),
    )
  }

  async setStopLoss(
    symbol: string,
    side: OrderSide,
    triggerPrice: number,
    size: number,
  ): Promise<OrderResult> {
    const bnSymbol = toBnSymbol(symbol)

    // Stop loss for a LONG (BUY) position -> sell on trigger
    // Stop loss for a SHORT (SELL) position -> buy on trigger
    const slSide = side === 'BUY' ? 'SELL' : 'BUY'

    const params: Record<string, string> = {
      symbol: bnSymbol,
      side: slSide,
      type: 'STOP_MARKET',
      quantity: String(size),
      stopPrice: String(triggerPrice),
      reduceOnly: 'true',
    }

    const resp = await this.signedPost<BnOrderResponse>('/fapi/v1/order', params)

    return {
      orderId: String(resp.orderId),
      status: fromBnStatus(resp.status),
      filledSize: parseFloat(resp.executedQty),
      filledPrice: parseFloat(resp.avgPrice),
      timestamp: resp.updateTime,
    }
  }

  async getOpenOrders(symbol?: string): Promise<OpenOrder[]> {
    const params: Record<string, string> = {}
    if (symbol) {
      params['symbol'] = toBnSymbol(symbol)
    }

    const raw = await this.signedGet<BnOpenOrder[]>('/fapi/v1/openOrders', params)

    return raw.map((o) => ({
      orderId: String(o.orderId),
      symbol: fromBnSymbol(o.symbol),
      side: o.side as OrderSide,
      price: parseFloat(o.price),
      size: parseFloat(o.origQty),
      filledSize: parseFloat(o.executedQty),
      orderType: fromBnTimeInForce(o.timeInForce),
      timestamp: o.time,
    }))
  }

  async getExchangeInfo(): Promise<ExchangeInfo> {
    const raw = await this.publicGet<BnExchangeInfoResponse>('/fapi/v1/exchangeInfo', {})

    const supportedSymbols: string[] = []
    const minOrderSizes: Record<string, number> = {}
    const tickSizes: Record<string, number> = {}

    for (const sym of raw.symbols) {
      if (sym.status !== 'TRADING') continue

      const engineSymbol = fromBnSymbol(sym.symbol)
      supportedSymbols.push(engineSymbol)

      const lotFilter = sym.filters.find((f) => f.filterType === 'LOT_SIZE')
      if (lotFilter?.minQty) {
        minOrderSizes[engineSymbol] = parseFloat(lotFilter.minQty)
      }

      const priceFilter = sym.filters.find((f) => f.filterType === 'PRICE_FILTER')
      if (priceFilter?.tickSize) {
        tickSizes[engineSymbol] = parseFloat(priceFilter.tickSize)
      }
    }

    return {
      name: 'Binance',
      testnet: this.baseUrl.includes('testnet'),
      supportedSymbols,
      minOrderSizes,
      tickSizes,
    }
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  private async publicGet<T>(path: string, params: Record<string, string>): Promise<T> {
    const qs = Object.entries(params)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&')

    const url = qs ? `${this.baseUrl}${path}?${qs}` : `${this.baseUrl}${path}`
    const resp = await this._fetch(url, {
      method: 'GET',
      headers: { 'X-MBX-APIKEY': this.apiKey },
    })

    if (!resp.ok) {
      const text = await resp.text()
      let errMsg = `Binance API error ${resp.status}: ${text}`
      try {
        const err = JSON.parse(text) as BnErrorResponse
        errMsg = `Binance API error ${resp.status}: [${err.code}] ${err.msg}`
      } catch { /* use raw text */ }
      throw new Error(errMsg)
    }

    return resp.json() as Promise<T>
  }

  private async signedGet<T>(path: string, extraParams?: Record<string, string>): Promise<T> {
    const params: Record<string, string> = {
      ...extraParams,
      timestamp: String(Date.now()),
      recvWindow: RECV_WINDOW,
    }

    const signedQs = this.signer.signQueryString(params)
    const url = `${this.baseUrl}${path}?${signedQs}`

    const resp = await this._fetch(url, {
      method: 'GET',
      headers: { 'X-MBX-APIKEY': this.apiKey },
    })

    if (!resp.ok) {
      const text = await resp.text()
      let errMsg = `Binance API error ${resp.status}: ${text}`
      try {
        const err = JSON.parse(text) as BnErrorResponse
        errMsg = `Binance API error ${resp.status}: [${err.code}] ${err.msg}`
      } catch { /* use raw text */ }
      throw new Error(errMsg)
    }

    return resp.json() as Promise<T>
  }

  private async signedPost<T>(path: string, extraParams: Record<string, string>): Promise<T> {
    const params: Record<string, string> = {
      ...extraParams,
      timestamp: String(Date.now()),
      recvWindow: RECV_WINDOW,
    }

    const signedQs = this.signer.signQueryString(params)
    const url = `${this.baseUrl}${path}?${signedQs}`

    const resp = await this._fetch(url, {
      method: 'POST',
      headers: { 'X-MBX-APIKEY': this.apiKey },
    })

    if (!resp.ok) {
      const text = await resp.text()
      let errMsg = `Binance API error ${resp.status}: ${text}`
      try {
        const err = JSON.parse(text) as BnErrorResponse
        errMsg = `Binance API error ${resp.status}: [${err.code}] ${err.msg}`
      } catch { /* use raw text */ }
      throw new Error(errMsg)
    }

    return resp.json() as Promise<T>
  }

  private async signedDelete<T>(path: string, extraParams: Record<string, string>): Promise<T> {
    const params: Record<string, string> = {
      ...extraParams,
      timestamp: String(Date.now()),
      recvWindow: RECV_WINDOW,
    }

    const signedQs = this.signer.signQueryString(params)
    const url = `${this.baseUrl}${path}?${signedQs}`

    const resp = await this._fetch(url, {
      method: 'DELETE',
      headers: { 'X-MBX-APIKEY': this.apiKey },
    })

    if (!resp.ok) {
      const text = await resp.text()
      let errMsg = `Binance API error ${resp.status}: ${text}`
      try {
        const err = JSON.parse(text) as BnErrorResponse
        errMsg = `Binance API error ${resp.status}: [${err.code}] ${err.msg}`
      } catch { /* use raw text */ }
      throw new Error(errMsg)
    }

    return resp.json() as Promise<T>
  }
}
