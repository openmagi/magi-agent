import { describe, it, expect, jest } from '@jest/globals'
import { KiumAdapter, parseKoreanNumber } from './kium.js'
import type { KiumComBridge, KiumConfig } from './kium.js'
import type { OrderRequest } from '../types.js'

function makeMockBridge(overrides: Partial<KiumComBridge> = {}): KiumComBridge {
  return {
    requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([]),
    sendOrder: jest.fn<KiumComBridge['sendOrder']>().mockResolvedValue({ orderId: 'ORD001', status: 'FILLED' }),
    cancelOrder: jest.fn<KiumComBridge['cancelOrder']>().mockResolvedValue(undefined),
    getAccountInfo: jest.fn<KiumComBridge['getAccountInfo']>().mockResolvedValue({ accountNo: '1234567890', userId: 'test' }),
    subscribe: jest.fn<KiumComBridge['subscribe']>(),
    unsubscribe: jest.fn<KiumComBridge['unsubscribe']>(),
    ...overrides,
  }
}

function makeConfig(bridge: KiumComBridge, overrides: Partial<KiumConfig> = {}): KiumConfig {
  return {
    accountNo: '1234567890',
    accountPassword: '0000',
    bridge,
    ...overrides,
  }
}

describe('KiumAdapter', () => {
  describe('parseKoreanNumber', () => {
    it('should parse comma-separated Korean numbers', () => {
      expect(parseKoreanNumber('1,234,567')).toBe(1234567)
    })

    it('should handle positive sign prefix', () => {
      expect(parseKoreanNumber('+1,234')).toBe(1234)
    })

    it('should handle negative sign prefix', () => {
      expect(parseKoreanNumber('-500')).toBe(500)
    })

    it('should handle zero', () => {
      expect(parseKoreanNumber('0')).toBe(0)
    })

    it('should handle empty string', () => {
      expect(parseKoreanNumber('')).toBe(0)
    })

    it('should handle spaces', () => {
      expect(parseKoreanNumber(' 1,000 ')).toBe(1000)
    })
  })

  describe('constructor', () => {
    it('should store account number and bridge', () => {
      const bridge = makeMockBridge()
      const adapter = new KiumAdapter(makeConfig(bridge))
      expect(adapter.name).toBe('키움증권')
    })

    it('should set name to "키움증권"', () => {
      const bridge = makeMockBridge()
      const adapter = new KiumAdapter(makeConfig(bridge))
      expect(adapter.name).toBe('키움증권')
    })
  })

  describe('getTicker', () => {
    it('should call requestTR with opt10001 and symbol', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([{
          '현재가': '+72,500',
          '매수최우선호가': '72,400',
          '매도최우선호가': '72,600',
          '거래량': '15,234,567',
        }]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      await adapter.getTicker('005930')

      expect(bridge.requestTR).toHaveBeenCalledWith('opt10001', { '종목코드': '005930' }, '0101')
    })

    it('should transform TR response to Ticker type', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([{
          '현재가': '+72,500',
          '매수최우선호가': '72,400',
          '매도최우선호가': '72,600',
          '거래량': '15,234,567',
        }]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      const ticker = await adapter.getTicker('005930')

      expect(ticker.symbol).toBe('005930')
      expect(ticker.lastPrice).toBe(72500)
      expect(ticker.mid).toBe(72500)
      expect(ticker.bid).toBe(72400)
      expect(ticker.ask).toBe(72600)
      expect(ticker.volume24h).toBe(15234567)
    })

    it('should set fundingRate and openInterest to 0', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([{
          '현재가': '72,500',
          '매수최우선호가': '72,400',
          '매도최우선호가': '72,600',
          '거래량': '100',
        }]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      const ticker = await adapter.getTicker('005930')

      expect(ticker.fundingRate).toBe(0)
      expect(ticker.openInterest).toBe(0)
    })

    it('should throw when no data returned', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      await expect(adapter.getTicker('999999')).rejects.toThrow('No data for symbol: 999999')
    })
  })

  describe('getOrderBook', () => {
    it('should call requestTR with opt10004', async () => {
      const rows = Array.from({ length: 10 }, (_, i) => ({
        [`매도호가${i + 1}`]: String((72600 + i * 100)),
        [`매도호가수량${i + 1}`]: String((100 + i * 10)),
        [`매수호가${i + 1}`]: String((72500 - i * 100)),
        [`매수호가수량${i + 1}`]: String((200 + i * 10)),
      })).reduce((acc, row) => ({ ...acc, ...row }), {})

      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([rows]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      await adapter.getOrderBook('005930')

      expect(bridge.requestTR).toHaveBeenCalledWith('opt10004', { '종목코드': '005930' }, '0102')
    })

    it('should map 매도호가/매수호가 to asks/bids with 10 levels', async () => {
      const rows: Record<string, string> = {}
      for (let i = 1; i <= 10; i++) {
        rows[`매도호가${i}`] = String(72600 + (i - 1) * 100)
        rows[`매도호가수량${i}`] = String(100 + (i - 1) * 10)
        rows[`매수호가${i}`] = String(72500 - (i - 1) * 100)
        rows[`매수호가수량${i}`] = String(200 + (i - 1) * 10)
      }

      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([rows]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      const ob = await adapter.getOrderBook('005930')

      expect(ob.asks).toHaveLength(10)
      expect(ob.bids).toHaveLength(10)
      expect(ob.asks[0]!.price).toBe(72600)
      expect(ob.asks[0]!.size).toBe(100)
      expect(ob.bids[0]!.price).toBe(72500)
      expect(ob.bids[0]!.size).toBe(200)
      expect(ob.symbol).toBe('005930')
    })
  })

  describe('getCandles', () => {
    it('should call requestTR with opt10081', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([
          { '일자': '20260316', '시가': '72,000', '고가': '73,000', '저가': '71,500', '현재가': '72,800', '거래량': '10,000' },
        ]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      await adapter.getCandles('005930', '1d', 1)

      expect(bridge.requestTR).toHaveBeenCalledWith('opt10081', expect.objectContaining({ '종목코드': '005930' }), '0103')
    })

    it('should map 시가/고가/저가/종가/거래량 to OHLCV', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([
          { '일자': '20260316', '시가': '72,000', '고가': '73,000', '저가': '71,500', '현재가': '72,800', '거래량': '10,000' },
        ]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      const candles = await adapter.getCandles('005930', '1d', 1)

      expect(candles).toHaveLength(1)
      expect(candles[0]!.open).toBe(72000)
      expect(candles[0]!.high).toBe(73000)
      expect(candles[0]!.low).toBe(71500)
      expect(candles[0]!.close).toBe(72800)
      expect(candles[0]!.volume).toBe(10000)
    })
  })

  describe('getBalances', () => {
    it('should call requestTR with opw00018 and set currency to KRW', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([{
          '총평가금액': '50,000,000',
          '추정예탁자산': '55,000,000',
          '총평가손익금액': '+3,000,000',
        }]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      const balances = await adapter.getBalances()

      expect(bridge.requestTR).toHaveBeenCalledWith('opw00018', expect.objectContaining({ '계좌번호': '1234567890' }), '0104')
      expect(balances).toHaveLength(1)
      expect(balances[0]!.currency).toBe('KRW')
      expect(balances[0]!.total).toBe(55000000)
      expect(balances[0]!.available).toBe(50000000)
      expect(balances[0]!.unrealizedPnl).toBe(3000000)
    })
  })

  describe('getPositions', () => {
    it('should map 보유종목 to Position[] with all LONG', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([
          { '종목번호': '005930', '보유수량': '100', '매입가': '70,000', '현재가': '72,500', '평가손익': '+250,000' },
          { '종목번호': '000660', '보유수량': '50', '매입가': '130,000', '현재가': '135,000', '평가손익': '+250,000' },
        ]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      const positions = await adapter.getPositions()

      expect(positions).toHaveLength(2)
      expect(positions[0]!.symbol).toBe('005930')
      expect(positions[0]!.side).toBe('LONG')
      expect(positions[0]!.size).toBe(100)
      expect(positions[0]!.entryPrice).toBe(70000)
      expect(positions[0]!.markPrice).toBe(72500)
      expect(positions[0]!.unrealizedPnl).toBe(250000)
      expect(positions[0]!.leverage).toBe(1)
      expect(positions[0]!.liquidationPrice).toBeNull()
      expect(positions[1]!.side).toBe('LONG')
    })
  })

  describe('placeOrder', () => {
    it('should call sendOrder with correct params for BUY', async () => {
      const bridge = makeMockBridge()
      const adapter = new KiumAdapter(makeConfig(bridge))

      const order: OrderRequest = {
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 72500,
        orderType: 'ALO',
      }
      await adapter.placeOrder(order)

      expect(bridge.sendOrder).toHaveBeenCalledWith({
        accountNo: '1234567890',
        orderType: 1,
        symbol: '005930',
        quantity: 10,
        price: 72500,
        priceType: '00',
        originalOrderNo: undefined,
      })
    })

    it('should call sendOrder with correct params for SELL', async () => {
      const bridge = makeMockBridge()
      const adapter = new KiumAdapter(makeConfig(bridge))

      const order: OrderRequest = {
        symbol: '005930',
        side: 'SELL',
        size: 5,
        price: 73000,
        orderType: 'GTC',
      }
      await adapter.placeOrder(order)

      expect(bridge.sendOrder).toHaveBeenCalledWith({
        accountNo: '1234567890',
        orderType: 2,
        symbol: '005930',
        quantity: 5,
        price: 73000,
        priceType: '00',
        originalOrderNo: undefined,
      })
    })

    it('should map IOC to 시장가 (03)', async () => {
      const bridge = makeMockBridge()
      const adapter = new KiumAdapter(makeConfig(bridge))

      const order: OrderRequest = {
        symbol: '005930',
        side: 'BUY',
        size: 10,
        price: 0,
        orderType: 'IOC',
      }
      await adapter.placeOrder(order)

      expect(bridge.sendOrder).toHaveBeenCalledWith(
        expect.objectContaining({ priceType: '03' }),
      )
    })
  })

  describe('cancelOrder', () => {
    it('should call bridge cancelOrder with orderId', async () => {
      const bridge = makeMockBridge()
      const adapter = new KiumAdapter(makeConfig(bridge))

      await adapter.cancelOrder('ORD-123')

      expect(bridge.cancelOrder).toHaveBeenCalledWith('ORD-123', 3)
    })
  })

  describe('getExchangeInfo', () => {
    it('should return exchange name "키움증권"', async () => {
      const bridge = makeMockBridge()
      const adapter = new KiumAdapter(makeConfig(bridge))

      const info = await adapter.getExchangeInfo()

      expect(info.name).toBe('키움증권')
    })

    it('should report testnet=false (키움 has no testnet)', async () => {
      const bridge = makeMockBridge()
      const adapter = new KiumAdapter(makeConfig(bridge))

      const info = await adapter.getExchangeInfo()

      expect(info.testnet).toBe(false)
    })
  })

  describe('getOpenOrders', () => {
    it('should call requestTR with opt10075', async () => {
      const bridge = makeMockBridge({
        requestTR: jest.fn<KiumComBridge['requestTR']>().mockResolvedValue([
          { '주문번호': 'ORD001', '종목코드': '005930', '주문구분': '+매수', '주문가격': '72,500', '주문수량': '10', '미체결수량': '5', '주문시간': '093000' },
        ]),
      })
      const adapter = new KiumAdapter(makeConfig(bridge))

      const orders = await adapter.getOpenOrders('005930')

      expect(bridge.requestTR).toHaveBeenCalledWith('opt10075', expect.objectContaining({ '계좌번호': '1234567890' }), '0105')
      expect(orders).toHaveLength(1)
      expect(orders[0]!.orderId).toBe('ORD001')
      expect(orders[0]!.symbol).toBe('005930')
      expect(orders[0]!.side).toBe('BUY')
    })
  })
})
