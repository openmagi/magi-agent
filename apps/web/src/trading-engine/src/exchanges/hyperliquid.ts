/**
 * Hyperliquid exchange adapter.
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

import { HlSigner } from './hl-signer.js'
import type { HlOrderWire, HlOrderType, HlAction } from './hl-signer.js'

// ── Config ────────────────────────────────────────────────────────────────────

export interface HyperliquidConfig {
  testnet: boolean
  privateKey: string
  walletAddress: string
}

// ── Hyperliquid raw API types ──────────────────────────────────────────────────

interface HlL2Level {
  px: string
  sz: string
  n: number
}

interface HlCandle {
  t: number
  o: string
  h: string
  l: string
  c: string
  v: string
}

interface HlAssetPosition {
  position: {
    coin: string
    szi: string
    entryPx: string
    positionValue: string
    unrealizedPnl: string
    leverage: { value: string }
    liquidationPx: string | null
  }
}

interface HlClearinghouseState {
  marginSummary: {
    accountValue: string
    totalMarginUsed: string
  }
  withdrawable: string
  assetPositions: HlAssetPosition[]
}

interface HlOpenOrder {
  coin: string
  oid: number
  side: string  // 'B' | 'A'
  limitPx: string
  sz: string
  timestamp: number
  orderType: string
}

interface HlMeta {
  universe: Array<{
    name: string
    szDecimals: number
    maxLeverage?: number
  }>
}

interface HlExchangeResponse {
  status: string
  response: {
    type: string
    data?: {
      statuses: Array<
        | { resting: { oid: number } }
        | { filled: { oid: number; totalSz: string; avgPx: string } }
        | { error: string }
        | 'success'
      >
    }
  } | string
}

interface HlMidsResponse {
  [coin: string]: string
}

interface HlContextResponse {
  mids?: Record<string, string>
  funding?: Record<string, { fundingRate: string }>
  assetCtxs?: Array<{
    funding: string
    openInterest: string
    prevDayPx: string
    dayNtlVlm: string
    premium: string
    oraclePx: string
    markPx: string
    midPx: string | null
    impactPxs: [string, string] | null
  }>
}

// ── Symbol utilities ──────────────────────────────────────────────────────────

function toHlCoin(symbol: string): string {
  // "ETH-PERP" → "ETH"
  return symbol.replace(/-PERP$/, '')
}

function fromHlCoin(coin: string): string {
  // "ETH" → "ETH-PERP"
  return `${coin}-PERP`
}

// ── Order type mapping ────────────────────────────────────────────────────────

function toHlOrderType(orderType: OrderType): HlOrderType {
  switch (orderType) {
    case 'ALO': return { limit: { tif: 'Alo' } }
    case 'GTC': return { limit: { tif: 'Gtc' } }
    case 'IOC': return { limit: { tif: 'Ioc' } }
  }
}

function fromHlOrderType(rawType: string): OrderType {
  if (rawType.toLowerCase().includes('alo')) return 'ALO'
  if (rawType.toLowerCase().includes('ioc')) return 'IOC'
  return 'GTC'
}

// ── Asset index lookup ────────────────────────────────────────────────────────

// Hyperliquid uses a numeric asset index in order actions.
// We build a map from coin → index using the /info meta endpoint.
// For now we use a static well-known map for common assets and fall back to a live fetch.
const STATIC_ASSET_INDEX: Record<string, number> = {
  BTC: 0,
  ETH: 1,
  ATOM: 2,
  MATIC: 3,
  DYDX: 4,
  SOL: 5,
  AVAX: 6,
  BNB: 7,
  APT: 8,
  ARB: 9,
  OP: 10,
  LTC: 11,
  DOGE: 12,
  CFX: 13,
  SUI: 14,
  kPEPE: 15,
  SHIB: 16,
  TRX: 17,
  ADA: 18,
  TON: 19,
  LINK: 20,
}

// ── Main adapter ──────────────────────────────────────────────────────────────

export class HyperliquidAdapter implements ExchangeAdapterInterface {
  readonly name = 'Hyperliquid'

  private readonly baseUrl: string
  private readonly walletAddress: string
  private readonly signer: HlSigner
  private assetIndex: Map<string, number> = new Map(Object.entries(STATIC_ASSET_INDEX))
  private assetIndexLoaded = false

  // Injected in tests via _fetch; production uses global fetch
  _fetch: typeof fetch = (...args) => fetch(...args)

  constructor(config: HyperliquidConfig) {
    this.baseUrl = config.testnet
      ? 'https://api.hyperliquid-testnet.xyz'
      : 'https://api.hyperliquid.xyz'

    this.walletAddress = requireWalletAddress(config.walletAddress, 'HyperliquidAdapter')
    this.signer = new HlSigner(config.privateKey, config.testnet)
  }

  // ── Public interface ───────────────────────────────────────────────────────

  async getTicker(symbol: string): Promise<Ticker> {
    const coin = toHlCoin(symbol)

    const [midsRaw, ctxRaw] = await Promise.all([
      this.infoPost<HlMidsResponse>({ type: 'allMids' }),
      this.infoPost<HlContextResponse>({ type: 'metaAndAssetCtxs' }),
    ])

    if (!(coin in midsRaw)) {
      throw new Error(`Symbol not found: ${symbol}`)
    }

    const mid = parseFloat(midsRaw[coin]!)
    // Spread is not directly available — estimate 0.01% from mid
    const spread = mid * 0.0001
    const bid = mid - spread
    const ask = mid + spread

    // Funding rate from context
    let fundingRate = 0
    let volume24h = 0
    let openInterest = 0

    if (Array.isArray((ctxRaw as { universe?: unknown; assetCtxs?: unknown }).assetCtxs)) {
      const typedCtx = ctxRaw as { universe: Array<{ name: string }>; assetCtxs: HlContextResponse['assetCtxs'] }
      const idx = typedCtx.universe?.findIndex((u) => u.name === coin) ?? -1
      if (idx >= 0 && typedCtx.assetCtxs) {
        const ctx = typedCtx.assetCtxs[idx]
        if (ctx) {
          fundingRate = parseFloat(ctx.funding)
          openInterest = parseFloat(ctx.openInterest)
          volume24h = parseFloat(ctx.dayNtlVlm)
        }
      }
    }

    return {
      symbol,
      mid,
      bid,
      ask,
      lastPrice: mid,
      volume24h,
      openInterest,
      fundingRate,
      timestamp: Date.now(),
    }
  }

  async getOrderBook(symbol: string, depth = 20): Promise<OrderBook> {
    const coin = toHlCoin(symbol)
    const raw = await this.infoPost<{ levels: [HlL2Level[], HlL2Level[]] }>({
      type: 'l2Book',
      coin,
    })

    const [rawBids, rawAsks] = raw.levels
    const bids: OrderBookLevel[] = (rawBids ?? []).slice(0, depth).map((l) => ({
      price: parseFloat(l.px),
      size: parseFloat(l.sz),
    }))
    const asks: OrderBookLevel[] = (rawAsks ?? []).slice(0, depth).map((l) => ({
      price: parseFloat(l.px),
      size: parseFloat(l.sz),
    }))

    return { symbol, bids, asks, timestamp: Date.now() }
  }

  async getCandles(symbol: string, interval: string, limit: number): Promise<Candle[]> {
    const coin = toHlCoin(symbol)
    const startTime = Date.now() - intervalToMs(interval) * limit

    const raw = await this.infoPost<HlCandle[]>({
      type: 'candleSnapshot',
      coin,
      interval,
      startTime,
    })

    return raw.slice(-limit).map((c) => ({
      timestamp: c.t,
      open: parseFloat(c.o),
      high: parseFloat(c.h),
      low: parseFloat(c.l),
      close: parseFloat(c.c),
      volume: parseFloat(c.v),
    }))
  }

  async getBalances(): Promise<Balance[]> {
    const state = await this.getAccountState()
    const available = parseFloat(state.withdrawable)
    const total = parseFloat(state.marginSummary.accountValue)
    const unrealizedPnl = state.assetPositions.reduce(
      (acc, ap) => acc + parseFloat(ap.position.unrealizedPnl),
      0,
    )

    return [
      {
        currency: 'USDC',
        available,
        total,
        unrealizedPnl,
      },
    ]
  }

  async getPositions(): Promise<Position[]> {
    const state = await this.getAccountState()

    return state.assetPositions
      .filter((ap) => parseFloat(ap.position.szi) !== 0)
      .map((ap) => {
        const p = ap.position
        const szi = parseFloat(p.szi)
        return {
          symbol: fromHlCoin(p.coin),
          side: szi >= 0 ? ('LONG' as const) : ('SHORT' as const),
          size: Math.abs(szi),
          entryPrice: parseFloat(p.entryPx),
          markPrice: parseFloat(p.positionValue) / Math.abs(szi),
          unrealizedPnl: parseFloat(p.unrealizedPnl),
          leverage: parseFloat(p.leverage.value),
          liquidationPrice: p.liquidationPx !== null ? parseFloat(p.liquidationPx) : null,
        }
      })
  }

  async placeOrder(order: OrderRequest): Promise<OrderResult> {
    const coin = toHlCoin(order.symbol)
    const assetIdx = await this.resolveAssetIndex(coin)

    const wire: HlOrderWire = {
      a: assetIdx,
      b: order.side === 'BUY',
      p: order.price.toString(),
      s: order.size.toString(),
      r: order.reduceOnly ?? false,
      t: toHlOrderType(order.orderType),
      ...(order.clientOrderId ? { c: order.clientOrderId } : {}),
    }

    const action: HlAction = {
      type: 'order',
      orders: [wire],
      grouping: 'na',
    }

    const nonce = Date.now()
    const { signature } = this.signer.signAction(action, nonce)

    const resp = await this.exchangePost<HlExchangeResponse>({
      action,
      nonce,
      signature,
    })

    if (resp.status !== 'ok') {
      const errMsg = typeof resp.response === 'string'
        ? resp.response
        : 'Order rejected by exchange'
      throw new Error(errMsg)
    }

    const responseData = resp.response as Exclude<HlExchangeResponse['response'], string>
    const statuses = responseData.data?.statuses ?? []
    const first = statuses[0]

    if (!first) {
      throw new Error('No order status returned from exchange')
    }

    return parseOrderStatus(first)
  }

  async cancelOrder(orderId: string): Promise<void> {
    // orderId format: "oid:coin" or just "oid"
    const [oidStr, coin] = orderId.split(':')
    const oid = parseInt(oidStr!, 10)

    const assetIdx = coin ? await this.resolveAssetIndex(coin) : 0

    const action: HlAction = {
      type: 'cancel',
      cancels: [{ a: assetIdx, o: oid }],
    }

    const nonce = Date.now()
    const { signature } = this.signer.signAction(action, nonce)

    const resp = await this.exchangePost<HlExchangeResponse>({
      action,
      nonce,
      signature,
    })

    if (resp.status !== 'ok') {
      const errMsg = typeof resp.response === 'string'
        ? resp.response
        : 'Cancel rejected by exchange'
      throw new Error(errMsg)
    }
  }

  async cancelAllOrders(symbol?: string): Promise<void> {
    const openOrders = await this.getRawOpenOrders()

    const filtered = symbol
      ? openOrders.filter((o) => o.coin === toHlCoin(symbol))
      : openOrders

    if (filtered.length === 0) return

    // Build cancels with asset indices
    const cancels = await Promise.all(
      filtered.map(async (o) => ({
        a: await this.resolveAssetIndex(o.coin),
        o: o.oid,
      })),
    )

    const action: HlAction = {
      type: 'cancel',
      cancels,
    }

    const nonce = Date.now()
    const { signature } = this.signer.signAction(action, nonce)

    const resp = await this.exchangePost<HlExchangeResponse>({
      action,
      nonce,
      signature,
    })

    if (resp.status !== 'ok') {
      const errMsg = typeof resp.response === 'string'
        ? resp.response
        : 'Cancel all rejected by exchange'
      throw new Error(errMsg)
    }
  }

  async setStopLoss(
    symbol: string,
    side: OrderSide,
    triggerPrice: number,
    size: number,
  ): Promise<OrderResult> {
    const coin = toHlCoin(symbol)
    const assetIdx = await this.resolveAssetIndex(coin)

    // Stop loss for a LONG (BUY) position → we sell on trigger
    // Stop loss for a SHORT (SELL) position → we buy on trigger
    const isBuy = side !== 'BUY'

    const wire: HlOrderWire = {
      a: assetIdx,
      b: isBuy,
      p: triggerPrice.toString(),
      s: size.toString(),
      r: true, // reduce_only
      t: {
        trigger: {
          isMarket: true,
          tpsl: 'sl',
          triggerPx: triggerPrice.toString(),
        },
      },
    }

    const action: HlAction = {
      type: 'order',
      orders: [wire],
      grouping: 'na',
    }

    const nonce = Date.now()
    const { signature } = this.signer.signAction(action, nonce)

    const resp = await this.exchangePost<HlExchangeResponse>({
      action,
      nonce,
      signature,
    })

    if (resp.status !== 'ok') {
      const errMsg = typeof resp.response === 'string'
        ? resp.response
        : 'Stop loss order rejected'
      throw new Error(errMsg)
    }

    const responseData = resp.response as Exclude<HlExchangeResponse['response'], string>
    const statuses = responseData.data?.statuses ?? []
    const first = statuses[0]

    if (!first) {
      throw new Error('No order status returned from exchange')
    }

    return parseOrderStatus(first)
  }

  async getOpenOrders(symbol?: string): Promise<OpenOrder[]> {
    const raw = await this.getRawOpenOrders()

    const filtered = symbol
      ? raw.filter((o) => o.coin === toHlCoin(symbol))
      : raw

    return filtered.map((o) => ({
      orderId: String(o.oid),
      symbol: fromHlCoin(o.coin),
      side: o.side === 'B' ? ('BUY' as OrderSide) : ('SELL' as OrderSide),
      price: parseFloat(o.limitPx),
      size: parseFloat(o.sz),
      filledSize: 0, // HL open orders don't carry partial fill info
      orderType: fromHlOrderType(o.orderType),
      timestamp: o.timestamp,
    }))
  }

  async getExchangeInfo(): Promise<ExchangeInfo> {
    const meta = await this.infoPost<HlMeta>({ type: 'meta' })

    const supportedSymbols = meta.universe.map((u) => fromHlCoin(u.name))
    const minOrderSizes: Record<string, number> = {}
    const tickSizes: Record<string, number> = {}

    for (const asset of meta.universe) {
      const sym = fromHlCoin(asset.name)
      // szDecimals e.g. 3 means min order = 0.001
      minOrderSizes[sym] = Math.pow(10, -asset.szDecimals)
      tickSizes[sym] = 0.01 // HL default price tick
    }

    return {
      name: 'Hyperliquid',
      testnet: this.baseUrl.includes('testnet'),
      supportedSymbols,
      minOrderSizes,
      tickSizes,
    }
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  private async getAccountState(): Promise<HlClearinghouseState> {
    return this.infoPost<HlClearinghouseState>({
      type: 'clearinghouseState',
      user: this.walletAddress,
    })
  }

  private async getRawOpenOrders(): Promise<HlOpenOrder[]> {
    return this.infoPost<HlOpenOrder[]>({
      type: 'openOrders',
      user: this.walletAddress,
    })
  }

  private async resolveAssetIndex(coin: string): Promise<number> {
    // Use static map first
    if (this.assetIndex.has(coin)) {
      return this.assetIndex.get(coin)!
    }

    // Load meta if not already done
    if (!this.assetIndexLoaded) {
      await this.loadAssetIndex()
      if (this.assetIndex.has(coin)) {
        return this.assetIndex.get(coin)!
      }
    }

    // Unknown coin — return 0 as fallback (will likely cause an API error)
    return 0
  }

  private async loadAssetIndex(): Promise<void> {
    const meta = await this.infoPost<HlMeta>({ type: 'meta' })
    for (let i = 0; i < meta.universe.length; i++) {
      const asset = meta.universe[i]
      if (asset) this.assetIndex.set(asset.name, i)
    }
    this.assetIndexLoaded = true
  }

  private async infoPost<T>(body: Record<string, unknown>): Promise<T> {
    const resp = await this._fetch(`${this.baseUrl}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Hyperliquid info API error ${resp.status}: ${text}`)
    }

    return resp.json() as Promise<T>
  }

  private async exchangePost<T>(body: Record<string, unknown>): Promise<T> {
    const resp = await this._fetch(`${this.baseUrl}/exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Hyperliquid exchange API error ${resp.status}: ${text}`)
    }

    return resp.json() as Promise<T>
  }
}

// ── Parse order status ─────────────────────────────────────────────────────────

type HlOrderStatus =
  | { resting: { oid: number } }
  | { filled: { oid: number; totalSz: string; avgPx: string } }
  | { error: string }
  | 'success'

function parseOrderStatus(status: HlOrderStatus): OrderResult {
  if (typeof status === 'string') {
    return { orderId: '0', status: 'OPEN', filledSize: 0, filledPrice: 0, timestamp: Date.now() }
  }

  if ('error' in status) {
    throw new Error(`Order rejected: ${status.error}`)
  }

  if ('resting' in status) {
    return {
      orderId: String(status.resting.oid),
      status: 'OPEN',
      filledSize: 0,
      filledPrice: 0,
      timestamp: Date.now(),
    }
  }

  if ('filled' in status) {
    return {
      orderId: String(status.filled.oid),
      status: 'FILLED',
      filledSize: parseFloat(status.filled.totalSz),
      filledPrice: parseFloat(status.filled.avgPx),
      timestamp: Date.now(),
    }
  }

  return { orderId: '0', status: 'OPEN', filledSize: 0, filledPrice: 0, timestamp: Date.now() }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function intervalToMs(interval: string): number {
  const n = parseInt(interval)
  if (interval.endsWith('m')) return n * 60_000
  if (interval.endsWith('h')) return n * 3_600_000
  if (interval.endsWith('d')) return n * 86_400_000
  return 60_000
}

function requireWalletAddress(address: string | undefined, context: string): string {
  if (!address) {
    throw new Error(`${context}: walletAddress is required. Ethereum addresses cannot be derived from private keys without elliptic curve point multiplication. Provide the address explicitly in config.`)
  }
  return address
}
