import type { TickContext, StrategyDecision } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'

/**
 * BasisArb — Spot vs Perpetual basis trade strategy.
 *
 * Trades the price difference between spot and perpetual markets.
 * - Contango (perp > spot): short perp (overvalued)
 * - Backwardation (perp < spot): long perp (undervalued)
 *
 * Params:
 *   - min_basis_bps: minimum basis in bps to enter (default 20)
 *   - order_size: size per leg (default 0.5)
 *   - max_position: maximum position size per side (default 2.0)
 *   - spot_price: current spot price (required, injected per tick)
 */
export class BasisArb extends BaseStrategy {
  readonly name = 'basis-arb'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, config } = ctx
    const symbol = ticker.symbol
    const perpPrice = ticker.mid

    // Extract params with defaults
    const minBasisBps = typeof config.params['min_basis_bps'] === 'number'
      ? config.params['min_basis_bps'] : 20
    const orderSize = typeof config.params['order_size'] === 'number'
      ? config.params['order_size'] : 0.5
    const maxPosition = typeof config.params['max_position'] === 'number'
      ? config.params['max_position'] : 2.0

    // Spot price is required
    const spotPrice = config.params['spot_price']
    if (typeof spotPrice !== 'number' || spotPrice <= 0) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'BasisArb: spot_price not provided',
      }]
    }

    // Compute basis in bps: (perp - spot) / spot * 10000
    const basisBps = (perpPrice - spotPrice) / spotPrice * 10_000

    // Check if basis exceeds threshold
    if (Math.abs(basisBps) < minBasisBps) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: `BasisArb: basis ${basisBps.toFixed(2)}bps below threshold ${minBasisBps}bps`,
      }]
    }

    // Calculate net position for the symbol
    const netPosition = positions
      .filter(p => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    if (basisBps > 0) {
      // Contango: perp > spot => short perp (sell overvalued)
      if (netPosition <= -maxPosition) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: `BasisArb: max short position reached (${netPosition.toFixed(4)})`,
        }]
      }

      const confidence = Math.min(90, Math.round(Math.abs(basisBps) / minBasisBps * 30))

      return [{
        action: 'SELL',
        symbol,
        size: orderSize,
        orderType: 'GTC',
        confidence,
        reason: `BasisArb: contango, short perp, basis +${basisBps.toFixed(2)}bps (perp=${perpPrice.toFixed(2)} > spot=${spotPrice.toFixed(2)})`,
      }]
    } else {
      // Backwardation: perp < spot => long perp (buy undervalued)
      if (netPosition >= maxPosition) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: `BasisArb: max long position reached (${netPosition.toFixed(4)})`,
        }]
      }

      const confidence = Math.min(90, Math.round(Math.abs(basisBps) / minBasisBps * 30))

      return [{
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'GTC',
        confidence,
        reason: `BasisArb: backwardation, long perp, basis ${basisBps.toFixed(2)}bps (perp=${perpPrice.toFixed(2)} < spot=${spotPrice.toFixed(2)})`,
      }]
    }
  }
}
