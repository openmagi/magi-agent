/**
 * Polymarket CLOB exchange adapter.
 * Implements ExchangeAdapterInterface using the Polymarket CLOB REST API.
 *
 * Symbol convention: YES-<conditionId> or NO-<conditionId>
 * Prices represent probabilities (0.00 - 1.00).
 */

import { createHmac } from 'node:crypto'
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

// ── Config ────────────────────────────────────────────────────────────────────

export interface PolymarketConfig {
  apiUrl: string              // 'https://clob.polymarket.com'
  privateKey: string          // for EIP-712 signing
  walletAddress: string       // Ethereum wallet address (required)
  chainId: number             // 137 (Polygon) or 80002 (Amoy testnet)
  funderAddress?: string      // CLOB API key funder
  apiKey?: string             // CLOB API key
  apiSecret?: string          // CLOB API secret (base64)
  apiPassphrase?: string      // CLOB API passphrase
}

// ── Internal types ────────────────────────────────────────────────────────────

interface PolymarketToken {
  token_id: string
  outcome: string
}

interface PolymarketMarket {
  condition_id: string
  question: string
  tokens: PolymarketToken[]
  active: boolean
  minimum_order_size: string
  minimum_tick_size: string
  volume_num_24hr?: number
}

interface PolymarketOrderResponse {
  id: string
  status: string   // 'MATCHED' | 'LIVE' | 'CANCELED' | etc
  size: string
  price: string
}

interface PolymarketOpenOrder {
  id: string
  token_id: string
  condition_id: string
  side: string
  price: string
  original_size: string
  size_matched: string
  type: string
  created_at: number
  outcome: string
}

interface PolymarketPosition {
  asset: string
  condition_id: string
  size: string
  avg_price: string
  cur_price: string
  outcome: string
}

interface PolymarketBalanceEntry {
  asset_type: string
  balance: string
}

interface PolymarketPricePoint {
  t: number
  p: number
}

interface PolymarketPriceHistory {
  history: PolymarketPricePoint[]
}

interface ParsedSymbol {
  conditionId: string
  tokenId: string
  side: 'YES' | 'NO'
}

// ── Main adapter ──────────────────────────────────────────────────────────────

export class PolymarketAdapter implements ExchangeAdapterInterface {
  readonly name = 'Polymarket'

  private readonly apiUrl: string
  private readonly chainId: number
  private readonly privateKey: string
  private readonly walletAddress: string
  private readonly apiKey: string
  private readonly apiSecret: string
  private readonly apiPassphrase: string
  private readonly marketCache: Map<string, PolymarketMarket> = new Map()

  // Injected in tests via _fetch; production uses global fetch
  _fetch: typeof fetch = (...args) => fetch(...args)

  constructor(config: PolymarketConfig) {
    this.apiUrl = config.apiUrl
    this.chainId = config.chainId
    this.privateKey = config.privateKey
    this.walletAddress = requireWalletAddress(config.walletAddress, 'PolymarketAdapter')
    this.apiKey = config.apiKey ?? ''
    this.apiSecret = config.apiSecret ?? ''
    this.apiPassphrase = config.apiPassphrase ?? ''
  }

  // ── Public interface ───────────────────────────────────────────────────────

  async getTicker(symbol: string): Promise<Ticker> {
    const parsed = await this.resolveSymbol(symbol)
    const priceResp = await this.publicGet<{ price: string }>(`/price?token_id=${parsed.tokenId}`)
    const price = parseFloat(priceResp.price)

    // Fetch market info for volume
    const market = await this.fetchMarket(parsed.conditionId)
    const volume24h = market.volume_num_24hr ?? 0

    return {
      symbol,
      mid: price,
      bid: price - 0.01,
      ask: price + 0.01,
      lastPrice: price,
      volume24h,
      openInterest: 0,
      fundingRate: 0,
      timestamp: Date.now(),
    }
  }

  async getOrderBook(symbol: string, depth = 20): Promise<OrderBook> {
    const parsed = await this.resolveSymbol(symbol)
    const raw = await this.publicGet<{ bids: Array<{ price: string; size: string }>; asks: Array<{ price: string; size: string }> }>(
      `/book?token_id=${parsed.tokenId}`
    )

    const bids: OrderBookLevel[] = (raw.bids ?? []).slice(0, depth).map((l) => ({
      price: parseFloat(l.price),
      size: parseFloat(l.size),
    }))
    const asks: OrderBookLevel[] = (raw.asks ?? []).slice(0, depth).map((l) => ({
      price: parseFloat(l.price),
      size: parseFloat(l.size),
    }))

    return { symbol, bids, asks, timestamp: Date.now() }
  }

  async getCandles(symbol: string, interval: string, limit: number): Promise<Candle[]> {
    const parsed = await this.resolveSymbol(symbol)
    const raw = await this.publicGet<PolymarketPriceHistory>(
      `/prices-history?token_id=${parsed.tokenId}&interval=${interval}&fidelity=${limit * 2}`
    )

    const points = raw.history ?? []
    // Group price points into candle-like buckets
    return this.pointsToCandles(points, limit)
  }

  async getBalances(): Promise<Balance[]> {
    const raw = await this.authGet<PolymarketBalanceEntry[]>('/balances')

    const usdcEntry = raw.find((b) => b.asset_type === 'USDC')
    const balance = usdcEntry ? parseFloat(usdcEntry.balance) : 0

    return [
      {
        currency: 'USDC',
        available: balance,
        total: balance,
        unrealizedPnl: 0,
      },
    ]
  }

  async getPositions(): Promise<Position[]> {
    const raw = await this.authGet<PolymarketPosition[]>('/positions')

    return raw
      .filter((p) => parseFloat(p.size) > 0)
      .map((p) => {
        const size = parseFloat(p.size)
        const entryPrice = parseFloat(p.avg_price)
        const markPrice = parseFloat(p.cur_price)
        const unrealizedPnl = (markPrice - entryPrice) * size
        const side = p.outcome.toLowerCase() === 'yes' ? 'YES' : 'NO'
        const conditionSymbol = `${side}-${p.condition_id}`

        return {
          symbol: conditionSymbol,
          side: 'LONG' as const,
          size,
          entryPrice,
          markPrice,
          unrealizedPnl,
          leverage: 1,
          liquidationPrice: null,
        }
      })
  }

  async placeOrder(order: OrderRequest): Promise<OrderResult> {
    const parsed = await this.resolveSymbol(order.symbol)

    const body = {
      token_id: parsed.tokenId,
      price: order.price.toString(),
      size: order.size.toString(),
      side: order.side,
      type: this.toPolyOrderType(order.orderType),
    }

    const resp = await this.authPost<PolymarketOrderResponse>('/order', body)

    return this.mapOrderResponse(resp)
  }

  async cancelOrder(orderId: string): Promise<void> {
    await this.authDelete(`/order/${orderId}`)
  }

  async cancelAllOrders(symbol?: string): Promise<void> {
    const orders = await this.getOpenOrders(symbol)
    await Promise.all(orders.map((o) => this.cancelOrder(o.orderId)))
  }

  async setStopLoss(
    _symbol: string,
    _side: OrderSide,
    _triggerPrice: number,
    _size: number,
  ): Promise<OrderResult> {
    throw new Error('Stop loss is not supported for prediction markets')
  }

  async getOpenOrders(symbol?: string): Promise<OpenOrder[]> {
    const raw = await this.authGet<PolymarketOpenOrder[]>('/orders')

    let filtered = raw
    if (symbol) {
      const parsed = this.parseSymbol(symbol)
      filtered = raw.filter((o) => o.condition_id === parsed.conditionId)
    }

    return filtered.map((o) => ({
      orderId: o.id,
      symbol: `${o.outcome.toUpperCase() === 'YES' ? 'YES' : 'NO'}-${o.condition_id}`,
      side: o.side as OrderSide,
      price: parseFloat(o.price),
      size: parseFloat(o.original_size),
      filledSize: parseFloat(o.size_matched),
      orderType: this.fromPolyOrderType(o.type),
      timestamp: o.created_at,
    }))
  }

  async getExchangeInfo(): Promise<ExchangeInfo> {
    const markets = await this.publicGet<PolymarketMarket[]>('/markets')

    const supportedSymbols: string[] = []
    const minOrderSizes: Record<string, number> = {}
    const tickSizes: Record<string, number> = {}

    for (const market of markets) {
      if (!market.active) continue
      const yesSymbol = `YES-${market.condition_id}`
      const noSymbol = `NO-${market.condition_id}`
      supportedSymbols.push(yesSymbol, noSymbol)

      const minSize = parseFloat(market.minimum_order_size)
      const tickSize = parseFloat(market.minimum_tick_size)
      minOrderSizes[yesSymbol] = minSize
      minOrderSizes[noSymbol] = minSize
      tickSizes[yesSymbol] = tickSize
      tickSizes[noSymbol] = tickSize

      // Cache market for later use
      this.marketCache.set(market.condition_id, market)
    }

    return {
      name: 'Polymarket',
      testnet: this.apiUrl.includes('testnet'),
      supportedSymbols,
      minOrderSizes,
      tickSizes,
    }
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  private parseSymbol(symbol: string): { conditionId: string; side: 'YES' | 'NO' } {
    const match = /^(YES|NO)-(.+)$/.exec(symbol)
    if (!match || !match[1] || !match[2]) {
      throw new Error(`Invalid Polymarket symbol: ${symbol}`)
    }
    return { conditionId: match[2], side: match[1] as 'YES' | 'NO' }
  }

  private async resolveSymbol(symbol: string): Promise<ParsedSymbol> {
    const { conditionId, side } = this.parseSymbol(symbol)

    // Check cache first
    let market = this.marketCache.get(conditionId)
    if (!market) {
      market = await this.fetchMarket(conditionId)
      this.marketCache.set(conditionId, market)
    }

    const targetOutcome = side === 'YES' ? 'Yes' : 'No'
    const token = market.tokens.find((t) => t.outcome === targetOutcome)
    if (!token) {
      throw new Error(`Token not found for ${side} outcome in market ${conditionId}`)
    }

    return { conditionId, tokenId: token.token_id, side }
  }

  private async fetchMarket(conditionId: string): Promise<PolymarketMarket> {
    return this.publicGet<PolymarketMarket>(`/markets/${conditionId}`)
  }

  private pointsToCandles(points: PolymarketPricePoint[], limit: number): Candle[] {
    if (points.length === 0) return []

    // Take only the last `limit * 2` points to form `limit` candles
    const relevantPoints = points.slice(-limit * 2)

    // Group into candles of ~2 points each
    const candles: Candle[] = []
    const chunkSize = Math.max(1, Math.floor(relevantPoints.length / limit))

    for (let i = 0; i < relevantPoints.length; i += chunkSize) {
      const chunk = relevantPoints.slice(i, i + chunkSize)
      if (chunk.length === 0) continue

      const prices = chunk.map((p) => p.p)
      const firstPoint = chunk[0]!
      const lastPoint = chunk[chunk.length - 1]!

      candles.push({
        timestamp: firstPoint.t * 1000,
        open: firstPoint.p,
        high: Math.max(...prices),
        low: Math.min(...prices),
        close: lastPoint.p,
        volume: 0, // prediction markets don't have per-candle volume
      })

      if (candles.length >= limit) break
    }

    return candles.slice(-limit)
  }

  private mapOrderResponse(resp: PolymarketOrderResponse): OrderResult {
    const status = resp.status.toUpperCase()
    const filledSize = parseFloat(resp.size)
    const filledPrice = parseFloat(resp.price)

    if (status === 'MATCHED') {
      return {
        orderId: resp.id,
        status: 'FILLED',
        filledSize,
        filledPrice,
        timestamp: Date.now(),
      }
    }

    // LIVE = resting/open order
    return {
      orderId: resp.id,
      status: 'OPEN',
      filledSize: 0,
      filledPrice: 0,
      timestamp: Date.now(),
    }
  }

  private toPolyOrderType(orderType: OrderType): string {
    switch (orderType) {
      case 'ALO': return 'FOK'  // Polymarket doesn't have ALO; use FOK closest analog
      case 'GTC': return 'GTC'
      case 'IOC': return 'FOK'
    }
  }

  private fromPolyOrderType(rawType: string): OrderType {
    if (rawType.toUpperCase() === 'FOK') return 'IOC'
    if (rawType.toUpperCase() === 'GTC') return 'GTC'
    return 'GTC'
  }

  private generateApiHeaders(method: string, path: string, body?: string): Record<string, string> {
    const timestamp = Math.floor(Date.now() / 1000).toString()
    const nonce = '0'
    const message = timestamp + method.toUpperCase() + path + (body ?? '')

    let signature = ''
    if (this.apiSecret) {
      const secretBytes = Buffer.from(this.apiSecret, 'base64')
      signature = createHmac('sha256', secretBytes)
        .update(message)
        .digest('base64')
    }

    return {
      'POLY-ADDRESS': this.walletAddress,
      'POLY-SIGNATURE': signature,
      'POLY-TIMESTAMP': timestamp,
      'POLY-NONCE': nonce,
      'POLY-API-KEY': this.apiKey,
      'POLY-PASSPHRASE': this.apiPassphrase,
      'Content-Type': 'application/json',
    }
  }

  private async publicGet<T>(path: string): Promise<T> {
    const resp = await this._fetch(`${this.apiUrl}${path}`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    })

    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Polymarket API error ${resp.status}: ${text}`)
    }

    return resp.json() as Promise<T>
  }

  private async authGet<T>(path: string): Promise<T> {
    const headers = this.generateApiHeaders('GET', path)
    const resp = await this._fetch(`${this.apiUrl}${path}`, {
      method: 'GET',
      headers,
    })

    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Polymarket API error ${resp.status}: ${text}`)
    }

    return resp.json() as Promise<T>
  }

  private async authPost<T>(path: string, body: Record<string, unknown>): Promise<T> {
    const bodyStr = JSON.stringify(body)
    const headers = this.generateApiHeaders('POST', path, bodyStr)
    const resp = await this._fetch(`${this.apiUrl}${path}`, {
      method: 'POST',
      headers,
      body: bodyStr,
    })

    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Polymarket API error ${resp.status}: ${text}`)
    }

    return resp.json() as Promise<T>
  }

  private async authDelete(path: string): Promise<void> {
    const headers = this.generateApiHeaders('DELETE', path)
    const resp = await this._fetch(`${this.apiUrl}${path}`, {
      method: 'DELETE',
      headers,
    })

    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Polymarket API error ${resp.status}: ${text}`)
    }
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function requireWalletAddress(address: string | undefined, context: string): string {
  if (!address) {
    throw new Error(`${context}: walletAddress is required. Ethereum addresses cannot be derived from private keys without elliptic curve point multiplication. Provide the address explicitly in config.`)
  }
  return address
}
