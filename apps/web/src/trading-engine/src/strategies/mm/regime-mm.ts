import type { TickContext, StrategyDecision } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'
import { computeSigma, getParam } from '../math-utils.js'

type VolRegime = 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME'

interface RegimeParams {
  spreadMultiplier: number
  sizeMultiplier: number
  inventorySkewFactor: number // 0 = no skew, 1 = max skew
}

const REGIME_PARAMS: Record<VolRegime, RegimeParams> = {
  LOW:     { spreadMultiplier: 0.5, sizeMultiplier: 2.0, inventorySkewFactor: 0 },
  NORMAL:  { spreadMultiplier: 1.0, sizeMultiplier: 1.0, inventorySkewFactor: 0.3 },
  HIGH:    { spreadMultiplier: 2.0, sizeMultiplier: 0.5, inventorySkewFactor: 0.7 },
  EXTREME: { spreadMultiplier: 3.0, sizeMultiplier: 0.25, inventorySkewFactor: 1.0 },
}

const HYSTERESIS_TICKS = 3

/** Classify volatility into 4 regimes */
function classifyRegime(sigma: number): VolRegime {
  if (sigma < 0.001) return 'LOW'
  if (sigma < 0.005) return 'NORMAL'
  if (sigma < 0.015) return 'HIGH'
  return 'EXTREME'
}

export class RegimeMM extends BaseStrategy {
  readonly name = 'regime-mm'
  private currentRegime: VolRegime = 'NORMAL'
  private pendingRegime: VolRegime | null = null
  private pendingCount = 0

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, candles, config } = ctx
    const mid = ticker.mid
    const symbol = ticker.symbol

    const baseSpreadBps = getParam(config.params, 'base_spread_bps', 10)
    const orderSize = getParam(config.params, 'order_size', 0.1)
    const maxPosition = getParam(config.params, 'max_position', 1.0)
    const volWindow = getParam(config.params, 'vol_window', 20)

    // 1. Compute sigma
    const sigma = computeSigma(candles, volWindow)

    // 2. Classify raw regime
    const rawRegime = classifyRegime(sigma)

    // 3. Apply hysteresis
    if (rawRegime !== this.currentRegime) {
      if (rawRegime === this.pendingRegime) {
        this.pendingCount++
        if (this.pendingCount >= HYSTERESIS_TICKS) {
          this.currentRegime = rawRegime
          this.pendingRegime = null
          this.pendingCount = 0
        }
      } else {
        this.pendingRegime = rawRegime
        this.pendingCount = 1
      }
    } else {
      // Back to current regime, reset pending
      this.pendingRegime = null
      this.pendingCount = 0
    }

    // 4. Look up regime params
    const regime = REGIME_PARAMS[this.currentRegime]

    // 5. Compute spread
    const halfSpread = mid * baseSpreadBps / 10_000 / 2 * regime.spreadMultiplier

    // 6. Compute size
    const adjustedSize = orderSize * regime.sizeMultiplier

    // 7. Apply inventory skew
    const netPosition = positions
      .filter((p): p is typeof p => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    // Skew shifts fair value against inventory direction
    // Positive netPosition -> shift fair value down (discourage more longs)
    const skewShift = netPosition * regime.inventorySkewFactor * mid * 0.001

    const fairValue = mid - skewShift
    const bidPrice = fairValue - halfSpread
    const askPrice = fairValue + halfSpread

    const decisions: StrategyDecision[] = []

    if (netPosition < maxPosition) {
      decisions.push({
        action: 'BUY',
        symbol,
        size: adjustedSize,
        orderType: 'ALO',
        confidence: 55,
        reason: `RegimeMM bid regime=${this.currentRegime} sigma=${sigma.toFixed(6)} spread_mult=${regime.spreadMultiplier}`,
        stopLoss: bidPrice,
      })
    }

    if (netPosition > -maxPosition) {
      decisions.push({
        action: 'SELL',
        symbol,
        size: adjustedSize,
        orderType: 'ALO',
        confidence: 55,
        reason: `RegimeMM ask regime=${this.currentRegime} sigma=${sigma.toFixed(6)} spread_mult=${regime.spreadMultiplier}`,
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
        reason: `RegimeMM: max position reached on both sides (regime=${this.currentRegime})`,
      })
    }

    return decisions
  }
}
