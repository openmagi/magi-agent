import type { TickContext, StrategyDecision, Candle, OrderBookLevel } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'
import { computeSigma, getParam } from '../math-utils.js'

/** Vol-based spread multiplier */
function volMultiplier(sigma: number): number {
  if (sigma < 0.001) return 0.8
  if (sigma < 0.005) return 1.0
  if (sigma < 0.01) return 1.5
  return 2.0
}

/** Micro-price: size-weighted mid from top-of-book */
function computeMicroPrice(topBid: OrderBookLevel, topAsk: OrderBookLevel): number {
  const totalSize = topBid.size + topAsk.size
  if (totalSize === 0) return (topBid.price + topAsk.price) / 2
  return (topBid.price * topAsk.size + topAsk.price * topBid.size) / totalSize
}

/** VWAP from candle data: sum(close * volume) / sum(volume) */
function computeVwap(candles: Candle[]): number {
  let sumPV = 0
  let sumV = 0
  for (const c of candles) {
    sumPV += c.close * c.volume
    sumV += c.volume
  }
  return sumV > 0 ? sumPV / sumV : 0
}

/** Order Flow Imbalance: bid_volume / (bid_volume + ask_volume) - 0.5, scaled to [-1, 1] */
function computeOfi(topBid: OrderBookLevel, topAsk: OrderBookLevel): number {
  const total = topBid.size + topAsk.size
  if (total === 0) return 0
  // OFI: 0.5 = balanced, > 0.5 = buy pressure, < 0.5 = sell pressure
  // Normalize to [-1, 1] range
  return (topBid.size / total - 0.5) * 2
}

/** Simple Moving Average of close prices */
function computeSma(candles: Candle[], period: number): number {
  const slice = candles.slice(-period)
  if (slice.length === 0) return 0
  return slice.reduce((s, c) => s + c.close, 0) / slice.length
}

export class EngineMM extends BaseStrategy {
  readonly name = 'engine-mm'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, candles, config, orderBook } = ctx
    const mid = ticker.mid
    const symbol = ticker.symbol

    // Read params
    const baseSpreadBps = getParam(config.params, 'base_spread_bps', 10)
    const orderSize = getParam(config.params, 'order_size', 0.1)
    const maxPosition = getParam(config.params, 'max_position', 1.0)
    const wMicro = getParam(config.params, 'w_micro', 0.4)
    const wVwap = getParam(config.params, 'w_vwap', 0.2)
    const wOfi = getParam(config.params, 'w_ofi', 0.2)
    const wMeanRev = getParam(config.params, 'w_mean_rev', 0.2)
    const ofiSensitivity = getParam(config.params, 'ofi_sensitivity', 0.5)
    const meanRevPeriod = getParam(config.params, 'mean_rev_period', 20)

    // Get top-of-book
    const topBid = orderBook.bids[0]
    const topAsk = orderBook.asks[0]
    if (!topBid || !topAsk) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'EngineMM: no order book data',
      }]
    }

    // 1. Compute 4 signals
    const microPrice = computeMicroPrice(topBid, topAsk)
    const vwap = computeVwap(candles)
    const ofi = computeOfi(topBid, topAsk) // [-1, 1]
    const sma = computeSma(candles, meanRevPeriod)

    // Convert signals to price-level adjustments relative to mid
    const microAdj = microPrice
    const vwapAdj = vwap > 0 ? vwap : mid
    const ofiAdj = mid + ofi * mid * 0.001 // OFI shifts mid by up to 0.1%
    const meanRevAdj = sma > 0 ? sma : mid

    // 2. Blend into composite fair value
    const totalWeight = wMicro + wVwap + wOfi + wMeanRev
    const fairValue = totalWeight > 0
      ? (wMicro * microAdj + wVwap * vwapAdj + wOfi * ofiAdj + wMeanRev * meanRevAdj) / totalWeight
      : mid

    // 3. Compute dynamic spread
    const sigma = computeSigma(candles)
    const volMult = volMultiplier(sigma)
    const absOfi = Math.abs(ofi)
    const dynamicSpread = mid * baseSpreadBps / 10_000 * (1 + absOfi * ofiSensitivity) * volMult

    const bidPrice = fairValue - dynamicSpread / 2
    const askPrice = fairValue + dynamicSpread / 2

    // Net position
    const netPosition = positions
      .filter((p): p is typeof p => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    const decisions: StrategyDecision[] = []

    if (netPosition < maxPosition) {
      decisions.push({
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'ALO',
        confidence: 55,
        reason: `EngineMM bid fv=${fairValue.toFixed(2)} micro=${microPrice.toFixed(2)} ofi=${ofi.toFixed(3)} sigma=${sigma.toFixed(6)}`,
        stopLoss: bidPrice,
      })
    }

    if (netPosition > -maxPosition) {
      decisions.push({
        action: 'SELL',
        symbol,
        size: orderSize,
        orderType: 'ALO',
        confidence: 55,
        reason: `EngineMM ask fv=${fairValue.toFixed(2)} micro=${microPrice.toFixed(2)} ofi=${ofi.toFixed(3)} sigma=${sigma.toFixed(6)}`,
        stopLoss: askPrice,
      })
    }

    if (decisions.length === 0) {
      decisions.push({
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'EngineMM: max position reached on both sides',
      })
    }

    return decisions
  }
}
