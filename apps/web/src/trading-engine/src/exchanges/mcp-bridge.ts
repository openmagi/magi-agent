import type {
  ExchangeAdapterInterface,
  ExchangeInfo,
  Ticker,
  OrderBook,
  OrderBookLevel,
  Candle,
  Balance,
  Position,
  OrderRequest,
  OrderResult,
  OpenOrder,
  OrderSide,
} from '../types.js'

/** Generic MCP tool caller -- injected by the bot runtime */
export interface McpToolCaller {
  callTool(name: string, args: Record<string, unknown>): Promise<unknown>
}

/** Maps exchange method names to MCP tool names */
export interface McpToolMapping {
  getTicker: string
  getOrderBook: string
  getBalances: string
  getPositions: string
  placeOrder: string
  cancelOrder: string
  getOpenOrders: string
}

export interface McpBridgeConfig {
  exchangeName: string
  toolMapping: McpToolMapping
  supportedSymbols: string[]
}

/**
 * MCP Bridge Adapter: wraps MCP tool calls into ExchangeAdapterInterface.
 * Enables any MCP-compatible DEX (Clober, Fly.trade) to be used with the trading engine.
 */
export class McpBridgeAdapter implements ExchangeAdapterInterface {
  readonly name: string
  private readonly caller: McpToolCaller
  private readonly mapping: McpToolMapping
  private readonly config: McpBridgeConfig

  constructor(caller: McpToolCaller, config: McpBridgeConfig) {
    this.name = config.exchangeName
    this.caller = caller
    this.mapping = config.toolMapping
    this.config = config
  }

  async getTicker(symbol: string): Promise<Ticker> {
    const raw = await this.caller.callTool(this.mapping.getTicker, { symbol })
    return this.parseTicker(raw)
  }

  async getOrderBook(symbol: string, depth?: number): Promise<OrderBook> {
    const raw = await this.caller.callTool(this.mapping.getOrderBook, { symbol, depth })
    return this.parseOrderBook(raw)
  }

  async getCandles(_symbol: string, _interval: string, _limit: number): Promise<Candle[]> {
    throw new Error('getCandles is not supported via MCP bridge')
  }

  async getBalances(): Promise<Balance[]> {
    const raw = await this.caller.callTool(this.mapping.getBalances, {})
    return this.parseBalances(raw)
  }

  async getPositions(): Promise<Position[]> {
    const raw = await this.caller.callTool(this.mapping.getPositions, {})
    return this.parsePositions(raw)
  }

  async placeOrder(order: OrderRequest): Promise<OrderResult> {
    const raw = await this.caller.callTool(this.mapping.placeOrder, {
      symbol: order.symbol,
      side: order.side,
      size: order.size,
      price: order.price,
      orderType: order.orderType,
    })
    return this.parseOrderResult(raw)
  }

  async cancelOrder(orderId: string): Promise<void> {
    await this.caller.callTool(this.mapping.cancelOrder, { orderId })
  }

  async cancelAllOrders(_symbol?: string): Promise<void> {
    // Cancel all by fetching open orders and cancelling each
    const openOrders = await this.getOpenOrders(_symbol)
    for (const order of openOrders) {
      await this.cancelOrder(order.orderId)
    }
  }

  async setStopLoss(
    _symbol: string,
    _side: OrderSide,
    _triggerPrice: number,
    _size: number,
  ): Promise<OrderResult> {
    throw new Error('setStopLoss is not supported via MCP bridge')
  }

  async getOpenOrders(symbol?: string): Promise<OpenOrder[]> {
    const raw = await this.caller.callTool(this.mapping.getOpenOrders, { symbol })
    return this.parseOpenOrders(raw)
  }

  async getExchangeInfo(): Promise<ExchangeInfo> {
    return {
      name: this.config.exchangeName,
      testnet: false,
      supportedSymbols: this.config.supportedSymbols,
      minOrderSizes: {},
      tickSizes: {},
    }
  }

  // --- Parsers ---

  private parseTicker(raw: unknown): Ticker {
    const data = raw as Record<string, unknown>
    return {
      symbol: String(data['symbol'] ?? ''),
      mid: Number(data['mid'] ?? 0),
      bid: Number(data['bid'] ?? 0),
      ask: Number(data['ask'] ?? 0),
      lastPrice: Number(data['lastPrice'] ?? 0),
      volume24h: Number(data['volume24h'] ?? 0),
      openInterest: Number(data['openInterest'] ?? 0),
      fundingRate: Number(data['fundingRate'] ?? 0),
      timestamp: Number(data['timestamp'] ?? Date.now()),
    }
  }

  private parseOrderBook(raw: unknown): OrderBook {
    const data = raw as Record<string, unknown>
    const parseLevels = (levels: unknown): OrderBookLevel[] => {
      if (!Array.isArray(levels)) return []
      return levels.map((l: Record<string, unknown>) => ({
        price: Number(l['price'] ?? 0),
        size: Number(l['size'] ?? 0),
      }))
    }

    return {
      symbol: String(data['symbol'] ?? ''),
      bids: parseLevels(data['bids']),
      asks: parseLevels(data['asks']),
      timestamp: Number(data['timestamp'] ?? Date.now()),
    }
  }

  private parseOrderResult(raw: unknown): OrderResult {
    const data = raw as Record<string, unknown>
    return {
      orderId: String(data['orderId'] ?? ''),
      status: String(data['status'] ?? 'REJECTED') as OrderResult['status'],
      filledSize: Number(data['filledSize'] ?? 0),
      filledPrice: Number(data['filledPrice'] ?? 0),
      timestamp: Number(data['timestamp'] ?? Date.now()),
    }
  }

  private parseBalances(raw: unknown): Balance[] {
    if (!Array.isArray(raw)) return []
    return raw.map((b: Record<string, unknown>) => ({
      currency: String(b['currency'] ?? ''),
      available: Number(b['available'] ?? 0),
      total: Number(b['total'] ?? 0),
      unrealizedPnl: Number(b['unrealizedPnl'] ?? 0),
    }))
  }

  private parsePositions(raw: unknown): Position[] {
    if (!Array.isArray(raw)) return []
    return raw.map((p: Record<string, unknown>) => ({
      symbol: String(p['symbol'] ?? ''),
      side: String(p['side'] ?? 'LONG') as Position['side'],
      size: Number(p['size'] ?? 0),
      entryPrice: Number(p['entryPrice'] ?? 0),
      markPrice: Number(p['markPrice'] ?? 0),
      unrealizedPnl: Number(p['unrealizedPnl'] ?? 0),
      leverage: Number(p['leverage'] ?? 1),
      liquidationPrice: p['liquidationPrice'] != null ? Number(p['liquidationPrice']) : null,
    }))
  }

  private parseOpenOrders(raw: unknown): OpenOrder[] {
    if (!Array.isArray(raw)) return []
    return raw.map((o: Record<string, unknown>) => ({
      orderId: String(o['orderId'] ?? ''),
      symbol: String(o['symbol'] ?? ''),
      side: String(o['side'] ?? 'BUY') as OpenOrder['side'],
      price: Number(o['price'] ?? 0),
      size: Number(o['size'] ?? 0),
      filledSize: Number(o['filledSize'] ?? 0),
      orderType: String(o['orderType'] ?? 'GTC') as OpenOrder['orderType'],
      timestamp: Number(o['timestamp'] ?? Date.now()),
    }))
  }
}
