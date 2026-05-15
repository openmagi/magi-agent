// === 키움증권 (Kium Securities) Adapter Skeleton ===
// COM-based Open API bridge adapter for Korean stock trading

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

/** Bridge interface for 키움증권 COM API calls */
export interface KiumComBridge {
  /** CommRqData wrapper: request TR data */
  requestTR(trCode: string, inputs: Record<string, string>, screenNo: string): Promise<Record<string, string>[]>
  /** SendOrder wrapper */
  sendOrder(params: KiumOrderParams): Promise<{ orderId: string; status: string }>
  /** CancelOrder wrapper */
  cancelOrder(orderId: string, orderType: number): Promise<void>
  /** GetLoginInfo */
  getAccountInfo(): Promise<{ accountNo: string; userId: string }>
  /** Real-time data subscription */
  subscribe(symbol: string, callback: (data: Record<string, string>) => void): void
  unsubscribe(symbol: string): void
}

export interface KiumConfig {
  accountNo: string
  accountPassword: string
  bridge: KiumComBridge
  afterHoursTrading?: boolean
}

export interface KiumOrderParams {
  accountNo: string
  orderType: number    // 1: 매수, 2: 매도, 3: 매수취소, 4: 매도취소
  symbol: string       // 종목코드 (e.g., '005930' for Samsung)
  quantity: number
  price: number
  priceType: string    // '00': 지정가, '03': 시장가
  originalOrderNo?: string
}

/** Parse Korean number format: remove commas/signs, handle +/- prefix, return absolute value */
export function parseKoreanNumber(s: string): number {
  const cleaned = s.replace(/[,+\s]/g, '')
  return Math.abs(parseFloat(cleaned) || 0)
}

/** Map OrderType to 키움 price type code */
function mapPriceType(orderType: string): string {
  if (orderType === 'IOC') return '03' // 시장가
  return '00' // 지정가 (ALO, GTC)
}

/** Map OrderSide to 키움 order type number */
function mapOrderSide(side: OrderSide): number {
  return side === 'BUY' ? 1 : 2
}

/**
 * 키움증권 Adapter (skeleton).
 *
 * This adapter defines the method signatures and data transformations
 * for the 키움증권 Open API. Actual COM calls are delegated to the
 * injectable KiumComBridge, which will be implemented with a native
 * Windows bridge in the future.
 */
export class KiumAdapter implements ExchangeAdapterInterface {
  readonly name = '키움증권'

  private readonly accountNo: string
  private readonly bridge: KiumComBridge
  private readonly afterHours: boolean

  constructor(config: KiumConfig) {
    this.accountNo = config.accountNo
    this.bridge = config.bridge
    this.afterHours = config.afterHoursTrading ?? false
  }

  async getTicker(symbol: string): Promise<Ticker> {
    const rows = await this.bridge.requestTR('opt10001', { '종목코드': symbol }, '0101')
    const row = rows[0]
    if (!row) throw new Error(`No data for symbol: ${symbol}`)

    const currentPrice = parseKoreanNumber(row['현재가'] ?? '0')
    return {
      symbol,
      mid: currentPrice,
      bid: parseKoreanNumber(row['매수최우선호가'] ?? '0'),
      ask: parseKoreanNumber(row['매도최우선호가'] ?? '0'),
      lastPrice: currentPrice,
      volume24h: parseKoreanNumber(row['거래량'] ?? '0'),
      openInterest: 0,
      fundingRate: 0,
      timestamp: Date.now(),
    }
  }

  async getOrderBook(symbol: string, _depth?: number): Promise<OrderBook> {
    const rows = await this.bridge.requestTR('opt10004', { '종목코드': symbol }, '0102')
    const row = rows[0] ?? {}

    const asks: OrderBookLevel[] = []
    const bids: OrderBookLevel[] = []

    for (let i = 1; i <= 10; i++) {
      const askPrice = parseKoreanNumber(row[`매도호가${i}`] ?? '0')
      const askSize = parseKoreanNumber(row[`매도호가수량${i}`] ?? '0')
      const bidPrice = parseKoreanNumber(row[`매수호가${i}`] ?? '0')
      const bidSize = parseKoreanNumber(row[`매수호가수량${i}`] ?? '0')

      asks.push({ price: askPrice, size: askSize })
      bids.push({ price: bidPrice, size: bidSize })
    }

    return {
      symbol,
      asks,
      bids,
      timestamp: Date.now(),
    }
  }

  async getCandles(symbol: string, _interval: string, _limit: number): Promise<Candle[]> {
    const rows = await this.bridge.requestTR('opt10081', { '종목코드': symbol, '기준일자': '', '수정주가구분': '1' }, '0103')

    return rows.map((row) => {
      const dateStr = row['일자'] ?? ''
      const year = parseInt(dateStr.slice(0, 4), 10) || 0
      const month = parseInt(dateStr.slice(4, 6), 10) || 1
      const day = parseInt(dateStr.slice(6, 8), 10) || 1
      const timestamp = new Date(year, month - 1, day).getTime()

      return {
        timestamp,
        open: parseKoreanNumber(row['시가'] ?? '0'),
        high: parseKoreanNumber(row['고가'] ?? '0'),
        low: parseKoreanNumber(row['저가'] ?? '0'),
        close: parseKoreanNumber(row['현재가'] ?? '0'),
        volume: parseKoreanNumber(row['거래량'] ?? '0'),
      }
    })
  }

  async getBalances(): Promise<Balance[]> {
    const rows = await this.bridge.requestTR('opw00018', {
      '계좌번호': this.accountNo,
      '비밀번호': '',
      '비밀번호입력매체구분': '00',
      '조회구분': '1',
    }, '0104')

    const row = rows[0]
    if (!row) return []

    return [{
      currency: 'KRW',
      available: parseKoreanNumber(row['총평가금액'] ?? '0'),
      total: parseKoreanNumber(row['추정예탁자산'] ?? '0'),
      unrealizedPnl: parseKoreanNumber(row['총평가손익금액'] ?? '0'),
    }]
  }

  async getPositions(): Promise<Position[]> {
    const rows = await this.bridge.requestTR('opw00018', {
      '계좌번호': this.accountNo,
      '비밀번호': '',
      '비밀번호입력매체구분': '00',
      '조회구분': '2',
    }, '0104')

    return rows.map((row) => ({
      symbol: (row['종목번호'] ?? '').replace(/\s/g, ''),
      side: 'LONG' as const,   // Korean stocks: no short selling for retail
      size: parseKoreanNumber(row['보유수량'] ?? '0'),
      entryPrice: parseKoreanNumber(row['매입가'] ?? '0'),
      markPrice: parseKoreanNumber(row['현재가'] ?? '0'),
      unrealizedPnl: parseKoreanNumber(row['평가손익'] ?? '0'),
      leverage: 1,              // Korean stocks: no leverage
      liquidationPrice: null,   // N/A for spot stocks
    }))
  }

  async placeOrder(order: OrderRequest): Promise<OrderResult> {
    const result = await this.bridge.sendOrder({
      accountNo: this.accountNo,
      orderType: mapOrderSide(order.side),
      symbol: order.symbol,
      quantity: order.size,
      price: order.price,
      priceType: mapPriceType(order.orderType),
      originalOrderNo: undefined,
    })

    return {
      orderId: result.orderId,
      status: result.status === 'FILLED' ? 'FILLED' : 'OPEN',
      filledSize: result.status === 'FILLED' ? order.size : 0,
      filledPrice: result.status === 'FILLED' ? order.price : 0,
      timestamp: Date.now(),
    }
  }

  async cancelOrder(orderId: string): Promise<void> {
    await this.bridge.cancelOrder(orderId, 3) // 3: 매수취소
  }

  async cancelAllOrders(symbol?: string): Promise<void> {
    const openOrders = await this.getOpenOrders(symbol)
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
    throw new Error('setStopLoss is not natively supported by 키움증권 Open API')
  }

  async getOpenOrders(_symbol?: string): Promise<OpenOrder[]> {
    const rows = await this.bridge.requestTR('opt10075', {
      '계좌번호': this.accountNo,
      '전체종목구분': '0',
      '매매구분': '0',
      '종목코드': '',
      '체결구분': '1',
    }, '0105')

    return rows.map((row) => {
      const orderTypeStr = row['주문구분'] ?? ''
      const side: OrderSide = orderTypeStr.includes('매수') ? 'BUY' : 'SELL'
      const totalSize = parseKoreanNumber(row['주문수량'] ?? '0')
      const unfilledSize = parseKoreanNumber(row['미체결수량'] ?? '0')

      return {
        orderId: row['주문번호'] ?? '',
        symbol: row['종목코드'] ?? '',
        side,
        price: parseKoreanNumber(row['주문가격'] ?? '0'),
        size: totalSize,
        filledSize: totalSize - unfilledSize,
        orderType: 'GTC' as const,
        timestamp: Date.now(),
      }
    })
  }

  async getExchangeInfo(): Promise<ExchangeInfo> {
    return {
      name: '키움증권',
      testnet: false,
      supportedSymbols: [],
      minOrderSizes: {},
      tickSizes: {},
    }
  }
}
