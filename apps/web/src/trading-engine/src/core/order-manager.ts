import type {
  OrderRequest,
  OrderResult,
  OrderSide,
  StrategyDecision,
  Balance,
  Ticker,
  ExchangeInfo,
  ExchangeAdapterInterface,
} from '../types.js'

/**
 * OrderManager converts strategy decisions into exchange orders,
 * handles ALO→GTC fallback, stop-loss management, and size calculations.
 */
export class OrderManager {
  /**
   * Convert a StrategyDecision into an OrderRequest and execute it.
   * Returns null for HOLD decisions.
   * Uses bid price for BUY ALO (post on bid side) and ask price for SELL ALO.
   * Uses mid price for non-ALO order types.
   */
  async executeDecision(
    decision: StrategyDecision,
    adapter: ExchangeAdapterInterface,
    ticker: Ticker
  ): Promise<OrderResult | null> {
    if (decision.action === 'HOLD') {
      return null
    }

    const side: OrderSide = decision.action === 'BUY' ? 'BUY' : 'SELL'
    const price = this.resolvePrice(side, decision.orderType, ticker)

    const order: OrderRequest = {
      symbol: decision.symbol,
      side,
      size: decision.size,
      price,
      orderType: decision.orderType,
    }

    return this.placeWithFallback(order, adapter)
  }

  /**
   * Resolve the order price based on side and order type.
   * ALO BUY → bid (post on bid side to be a maker)
   * ALO SELL → ask (post on ask side to be a maker)
   * GTC / IOC → mid price
   */
  private resolvePrice(side: OrderSide, orderType: string, ticker: Ticker): number {
    if (orderType === 'ALO') {
      return side === 'BUY' ? ticker.bid : ticker.ask
    }
    return ticker.mid
  }

  /**
   * Try to place an ALO order first. If rejected, retry with GTC.
   * Preserves all other order fields; only the orderType changes.
   */
  async placeWithFallback(
    order: OrderRequest,
    adapter: ExchangeAdapterInterface
  ): Promise<OrderResult> {
    const result = await adapter.placeOrder(order)

    if (result.status === 'REJECTED' && order.orderType === 'ALO') {
      const gtcOrder: OrderRequest = { ...order, orderType: 'GTC' }
      return adapter.placeOrder(gtcOrder)
    }

    return result
  }

  /**
   * Place / update an exchange-native stop-loss trigger for a position.
   */
  async syncStopLoss(
    symbol: string,
    side: OrderSide,
    triggerPrice: number,
    size: number,
    adapter: ExchangeAdapterInterface
  ): Promise<OrderResult> {
    return adapter.setStopLoss(symbol, side, triggerPrice, size)
  }

  /**
   * Cancel all outstanding stop-loss orders for a given symbol.
   */
  async cancelStopLoss(
    symbol: string,
    adapter: ExchangeAdapterInterface
  ): Promise<void> {
    await adapter.cancelAllOrders(symbol)
  }

  /**
   * Calculate position size (in asset units) from a percentage of equity.
   *
   * equity   = sum of all balances' total values
   * notional = equity * (pct / 100)
   * size     = (notional * leverage) / price
   */
  calcSize(
    balances: Balance[],
    pct: number,
    price: number,
    leverage: number
  ): number {
    const equity = balances.reduce((acc, b) => acc + b.total, 0)
    const notional = equity * (pct / 100)
    return (notional * leverage) / price
  }

  /**
   * Enforce minimum order size from exchange info.
   * Returns the size if it meets the minimum, or null if it is below the minimum.
   * If no minimum is defined for the symbol, the size is returned unchanged.
   */
  enforceMinSize(
    size: number,
    symbol: string,
    exchangeInfo: ExchangeInfo
  ): number | null {
    const minSize = exchangeInfo.minOrderSizes[symbol]
    if (minSize !== undefined && size < minSize) {
      return null
    }
    return size
  }
}
