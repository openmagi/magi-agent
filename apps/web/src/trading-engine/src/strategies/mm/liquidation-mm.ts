import type { TickContext, StrategyDecision } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'
import { computeSigma, getParam } from '../math-utils.js'

export class LiquidationMM extends BaseStrategy {
  readonly name = 'liquidation-mm'
  private prevOI: number | null = null

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, candles, config } = ctx
    const mid = ticker.mid
    const symbol = ticker.symbol

    const liqDistancePct = getParam(config.params, 'liq_distance_pct', 5)
    const orderSize = getParam(config.params, 'order_size', 0.1)
    const maxPosition = getParam(config.params, 'max_position', 1.0)
    const fundingThreshold = getParam(config.params, 'funding_threshold', 0.0001)
    const oiSurgeThreshold = getParam(config.params, 'oi_surge_threshold', 5)
    const spreadBps = getParam(config.params, 'spread_bps', 10)

    const fundingRate = ticker.fundingRate
    const currentOI = ticker.openInterest

    // Net position
    const netPosition = positions
      .filter((p): p is typeof p => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    // Compute OI change percentage for size scaling
    let oiChangePct = 0
    if (this.prevOI !== null && this.prevOI > 0) {
      oiChangePct = ((currentOI - this.prevOI) / this.prevOI) * 100
    }
    this.prevOI = currentOI

    // OI drop scaling: if OI drops more than threshold, increase size
    const oiDropPct = Math.max(0, -oiChangePct) // positive when OI drops
    const sizeMultiplier = oiDropPct > oiSurgeThreshold
      ? 1 + (oiDropPct - oiSurgeThreshold) / 10
      : 1.0
    const adjustedSize = orderSize * sizeMultiplier

    // Compute volatility for buffer
    const sigma = computeSigma(candles)
    // Buffer: in high vol, place orders further from liq zone (safer)
    const volBuffer = sigma * mid * 0.5 // volatility-proportional buffer

    const decisions: StrategyDecision[] = []

    const absFunding = Math.abs(fundingRate)

    if (absFunding < fundingThreshold) {
      // No clear directional bias: place symmetric orders around mid with base spread
      const halfSpread = mid * spreadBps / 10_000 / 2
      const bidPrice = mid - halfSpread
      const askPrice = mid + halfSpread

      if (netPosition < maxPosition) {
        decisions.push({
          action: 'BUY',
          symbol,
          size: adjustedSize,
          orderType: 'GTC',
          confidence: 40,
          reason: `LiquidationMM neutral bid, funding=${fundingRate.toFixed(6)} (below threshold)`,
          stopLoss: bidPrice,
        })
      }

      if (netPosition > -maxPosition) {
        decisions.push({
          action: 'SELL',
          symbol,
          size: adjustedSize,
          orderType: 'GTC',
          confidence: 40,
          reason: `LiquidationMM neutral ask, funding=${fundingRate.toFixed(6)} (below threshold)`,
          stopLoss: askPrice,
        })
      }
    } else if (fundingRate > 0) {
      // Positive funding: longs pay shorts -> long squeeze risk below
      // Place BUY orders below mid near liquidation zone to catch forced selling
      const liqZone = mid * (1 - liqDistancePct / 100)
      const bidPrice = liqZone + volBuffer

      if (netPosition < maxPosition) {
        decisions.push({
          action: 'BUY',
          symbol,
          size: adjustedSize,
          orderType: 'GTC',
          confidence: 60,
          reason: `LiquidationMM long-squeeze bid near liqZone=${liqZone.toFixed(2)} funding=${fundingRate.toFixed(6)} oiChg=${oiChangePct.toFixed(1)}%`,
          stopLoss: bidPrice,
        })
      }

      // Also place a sell at a spread above mid for inventory management
      if (netPosition > -maxPosition) {
        const askPrice = mid + mid * spreadBps / 10_000
        decisions.push({
          action: 'SELL',
          symbol,
          size: adjustedSize,
          orderType: 'GTC',
          confidence: 45,
          reason: `LiquidationMM hedge ask, funding=${fundingRate.toFixed(6)}`,
          stopLoss: askPrice,
        })
      }
    } else {
      // Negative funding: shorts pay longs -> short squeeze risk above
      // Place SELL orders above mid near liquidation zone to catch forced buying
      const liqZone = mid * (1 + liqDistancePct / 100)
      const askPrice = liqZone - volBuffer

      if (netPosition > -maxPosition) {
        decisions.push({
          action: 'SELL',
          symbol,
          size: adjustedSize,
          orderType: 'GTC',
          confidence: 60,
          reason: `LiquidationMM short-squeeze ask near liqZone=${liqZone.toFixed(2)} funding=${fundingRate.toFixed(6)} oiChg=${oiChangePct.toFixed(1)}%`,
          stopLoss: askPrice,
        })
      }

      // Also place a bid at a spread below mid for inventory management
      if (netPosition < maxPosition) {
        const bidPrice = mid - mid * spreadBps / 10_000
        decisions.push({
          action: 'BUY',
          symbol,
          size: adjustedSize,
          orderType: 'GTC',
          confidence: 45,
          reason: `LiquidationMM hedge bid, funding=${fundingRate.toFixed(6)}`,
          stopLoss: bidPrice,
        })
      }
    }

    if (decisions.length === 0) {
      decisions.push({
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'LiquidationMM: max position reached on both sides',
      })
    }

    return decisions
  }
}
