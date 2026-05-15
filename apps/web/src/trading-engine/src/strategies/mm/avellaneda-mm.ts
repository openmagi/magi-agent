import type { TickContext, StrategyDecision } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'
import { computeSigma } from '../math-utils.js'

type VolRegime = 'quiet' | 'normal' | 'volatile' | 'extreme'

function classifyVol(sigma: number): VolRegime {
  if (sigma < 0.001) return 'quiet'
  if (sigma < 0.005) return 'normal'
  if (sigma < 0.01) return 'volatile'
  return 'extreme'
}

function volMultiplier(regime: VolRegime): number {
  switch (regime) {
    case 'quiet': return 0.8
    case 'normal': return 1.0
    case 'volatile': return 1.5
    case 'extreme': return 2.0
  }
}

export class AvellanedaMM extends BaseStrategy {
  readonly name = 'avellaneda-mm'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, candles, config } = ctx
    const mid = ticker.mid
    const symbol = ticker.symbol

    const gamma = typeof config.params['gamma'] === 'number' ? config.params['gamma'] : 0.1
    const orderSize = typeof config.params['order_size'] === 'number' ? config.params['order_size'] : 0.1
    const maxPosition = typeof config.params['max_position'] === 'number' ? config.params['max_position'] : 1.0
    const timeHorizon = typeof config.params['time_horizon'] === 'number' ? config.params['time_horizon'] : 1

    const T = timeHorizon

    // Realized volatility
    const sigma = computeSigma(candles)
    const sigma2 = sigma ** 2

    // Net inventory (positive = long, negative = short)
    const q = positions
      .filter(p => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    // Avellaneda-Stoikov reservation price
    const reservationPrice = mid - q * gamma * sigma2 * T

    // Optimal spread: gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/k)
    const k = 1.5
    const optimalSpread = gamma * sigma2 * T + (2 / gamma) * Math.log(1 + gamma / k)

    // Vol-bin spread multiplier
    const regime = classifyVol(sigma)
    let spreadMultiplier = volMultiplier(regime)

    // Drawdown amplifier: widen spread when losing
    const hasDrawdown = positions
      .filter(p => p.symbol === symbol)
      .some(p => p.unrealizedPnl < 0)
    if (hasDrawdown) {
      spreadMultiplier *= 1.5
    }

    const adjustedSpread = optimalSpread * spreadMultiplier
    const bidPrice = reservationPrice - adjustedSpread / 2
    const askPrice = reservationPrice + adjustedSpread / 2

    const decisions: StrategyDecision[] = []

    if (q < maxPosition) {
      decisions.push({
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'ALO',
        confidence: 60,
        reason: `AvellanedaMM bid=${bidPrice.toFixed(4)} rp=${reservationPrice.toFixed(4)} sigma=${sigma.toFixed(6)} regime=${regime}`,
        stopLoss: bidPrice,
      })
    }

    if (q > -maxPosition) {
      decisions.push({
        action: 'SELL',
        symbol,
        size: orderSize,
        orderType: 'ALO',
        confidence: 60,
        reason: `AvellanedaMM ask=${askPrice.toFixed(4)} rp=${reservationPrice.toFixed(4)} sigma=${sigma.toFixed(6)} regime=${regime}`,
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
        reason: 'AvellanedaMM: max position reached on both sides',
      })
    }

    return decisions
  }
}
