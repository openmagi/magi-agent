import type { TickContext, StrategyDecision } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'
import { getParam } from '../math-utils.js'

export class GridMM extends BaseStrategy {
  readonly name = 'grid-mm'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, config } = ctx
    const mid = ticker.mid
    const symbol = ticker.symbol

    const gridLevels = getParam(config.params, 'grid_levels', 5)
    const gridSpacingBps = getParam(config.params, 'grid_spacing_bps', 20)
    const sizePerLevel = getParam(config.params, 'size_per_level', 0.05)
    const maxPosition = getParam(config.params, 'max_position', 1.0)

    // Compute net position for the symbol
    const netPosition = positions
      .filter((p): p is typeof p => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    // Available capacity on each side
    const buyCapacity = maxPosition - netPosition
    const sellCapacity = maxPosition + netPosition

    const decisions: StrategyDecision[] = []

    // Place grid levels
    for (let level = 1; level <= gridLevels; level++) {
      const cumulativeSize = level * sizePerLevel
      const confidence = Math.max(30, 65 - (level - 1) * 5)

      // Bid level
      const bidPrice = mid * (1 - level * gridSpacingBps / 10_000)
      if (cumulativeSize <= buyCapacity + 1e-10) {
        decisions.push({
          action: 'BUY',
          symbol,
          size: sizePerLevel,
          orderType: 'ALO',
          confidence,
          reason: `GridMM bid L${level} at ${bidPrice.toFixed(2)} (spacing ${gridSpacingBps}bps)`,
          stopLoss: bidPrice,
        })
      }

      // Ask level
      const askPrice = mid * (1 + level * gridSpacingBps / 10_000)
      if (cumulativeSize <= sellCapacity + 1e-10) {
        decisions.push({
          action: 'SELL',
          symbol,
          size: sizePerLevel,
          orderType: 'ALO',
          confidence,
          reason: `GridMM ask L${level} at ${askPrice.toFixed(2)} (spacing ${gridSpacingBps}bps)`,
          stopLoss: askPrice,
        })
      }
    }

    // If no decisions possible, emit HOLD
    if (decisions.length === 0) {
      decisions.push({
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'GridMM: max position reached on both sides',
      })
    }

    return decisions
  }
}
