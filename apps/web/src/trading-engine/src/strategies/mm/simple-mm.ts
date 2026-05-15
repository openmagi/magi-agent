import type { TickContext, StrategyDecision } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'

export class SimpleMM extends BaseStrategy {
  readonly name = 'simple-mm'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, config } = ctx
    const mid = ticker.mid
    const symbol = ticker.symbol

    const spreadBps = typeof config.params['spread_bps'] === 'number' ? config.params['spread_bps'] : 10
    const orderSize = typeof config.params['order_size'] === 'number' ? config.params['order_size'] : 0.1
    const maxPosition = typeof config.params['max_position'] === 'number' ? config.params['max_position'] : 1.0

    const halfSpread = mid * spreadBps / 10_000 / 2
    const bidPrice = mid - halfSpread
    const askPrice = mid + halfSpread

    // Calculate net position for the symbol
    const netPosition = positions
      .filter(p => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    const decisions: StrategyDecision[] = []

    // Emit BUY if not at max long
    if (netPosition < maxPosition) {
      decisions.push({
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'ALO',
        confidence: 50,
        reason: `SimpleMM bid at ${bidPrice.toFixed(4)} (spread ${spreadBps}bps)`,
        stopLoss: bidPrice,
      })
    }

    // Emit SELL if not at max short
    if (netPosition > -maxPosition) {
      decisions.push({
        action: 'SELL',
        symbol,
        size: orderSize,
        orderType: 'ALO',
        confidence: 50,
        reason: `SimpleMM ask at ${askPrice.toFixed(4)} (spread ${spreadBps}bps)`,
        stopLoss: askPrice,
      })
    }

    // If both sides are blocked, emit a single HOLD
    if (decisions.length === 0) {
      decisions.push({
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'SimpleMM: max position reached on both sides',
      })
    }

    return decisions
  }
}
