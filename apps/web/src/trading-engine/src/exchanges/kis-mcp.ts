// === 한국투자증권 (KIS) MCP Bridge Adapter ===
// Wraps McpBridgeAdapter with KIS-specific tool mappings and KRX hours guard

import type {
  ExchangeAdapterInterface,
  ExchangeInfo,
  Ticker,
  OrderBook,
  Candle,
  Balance,
  Position,
  OrderRequest,
  OrderResult,
  OpenOrder,
  OrderSide,
} from '../types.js'
import type { McpToolCaller, McpToolMapping, McpBridgeConfig } from './mcp-bridge.js'
import { McpBridgeAdapter } from './mcp-bridge.js'
import { getCurrentKrxSession, canTradeKrx, getNextKrxMarketOpen } from './krx-calendar.js'

export interface KisMcpConfig {
  mcpCaller: McpToolCaller
  accountNo: string
  afterHoursTrading?: boolean
}

/** KIS-specific MCP tool names */
const KIS_TOOL_MAPPING: McpToolMapping = {
  getTicker: 'kis_get_stock_price',
  getOrderBook: 'kis_get_orderbook',
  getBalances: 'kis_get_account_balance',
  getPositions: 'kis_get_positions',
  placeOrder: 'kis_place_order',
  cancelOrder: 'kis_cancel_order',
  getOpenOrders: 'kis_get_open_orders',
}

/**
 * 한국투자증권 MCP Adapter.
 *
 * Wraps the generic McpBridgeAdapter with:
 * - KIS-specific MCP tool name mappings
 * - KRX market hours guard on order mutations
 * - Optional after-hours trading support
 *
 * An optional `nowFn` can be injected for testing time-dependent behavior.
 */
export class KisMcpAdapter implements ExchangeAdapterInterface {
  readonly name = '한국투자증권'

  private readonly bridge: McpBridgeAdapter
  private readonly accountNo: string
  private readonly afterHours: boolean
  private readonly nowFn: () => Date

  constructor(config: KisMcpConfig, nowFn?: () => Date) {
    this.accountNo = config.accountNo
    this.afterHours = config.afterHoursTrading ?? false
    this.nowFn = nowFn ?? (() => new Date())

    const bridgeConfig: McpBridgeConfig = {
      exchangeName: '한국투자증권',
      toolMapping: KIS_TOOL_MAPPING,
      supportedSymbols: [],
    }
    this.bridge = new McpBridgeAdapter(config.mcpCaller, bridgeConfig)
  }

  // --- Read-only methods: delegate directly to bridge ---

  async getTicker(symbol: string): Promise<Ticker> {
    return this.bridge.getTicker(symbol)
  }

  async getOrderBook(symbol: string, depth?: number): Promise<OrderBook> {
    return this.bridge.getOrderBook(symbol, depth)
  }

  async getCandles(symbol: string, interval: string, limit: number): Promise<Candle[]> {
    return this.bridge.getCandles(symbol, interval, limit)
  }

  async getBalances(): Promise<Balance[]> {
    return this.bridge.getBalances()
  }

  async getPositions(): Promise<Position[]> {
    return this.bridge.getPositions()
  }

  async getOpenOrders(symbol?: string): Promise<OpenOrder[]> {
    return this.bridge.getOpenOrders(symbol)
  }

  async getExchangeInfo(): Promise<ExchangeInfo> {
    return this.bridge.getExchangeInfo()
  }

  // --- Mutation methods: KRX hours guard ---

  async placeOrder(order: OrderRequest): Promise<OrderResult> {
    this.assertMarketOpen()
    return this.bridge.placeOrder(order)
  }

  async cancelOrder(orderId: string): Promise<void> {
    this.assertMarketOpen()
    return this.bridge.cancelOrder(orderId)
  }

  async cancelAllOrders(symbol?: string): Promise<void> {
    this.assertMarketOpen()
    return this.bridge.cancelAllOrders(symbol)
  }

  async setStopLoss(
    symbol: string,
    side: OrderSide,
    triggerPrice: number,
    size: number,
  ): Promise<OrderResult> {
    this.assertMarketOpen()
    return this.bridge.setStopLoss(symbol, side, triggerPrice, size)
  }

  // --- Private helpers ---

  /** Throw if KRX market is not open for trading */
  private assertMarketOpen(): void {
    const now = this.nowFn()
    const session = getCurrentKrxSession(now)
    if (!canTradeKrx(session, this.afterHours)) {
      const nextOpen = getNextKrxMarketOpen(now)
      throw new Error(
        `KRX market is ${session}. Next open: ${nextOpen.toISOString()}`,
      )
    }
  }
}
