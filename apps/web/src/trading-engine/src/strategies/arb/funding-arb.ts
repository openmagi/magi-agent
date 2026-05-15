import type { TickContext, StrategyDecision } from '../../types.js'
import { BaseStrategy } from '../base-strategy.js'

/**
 * FundingArb — Cross-exchange funding rate arbitrage strategy.
 *
 * Delta-neutral: long on low-funding exchange, short on high-funding exchange.
 * The peer exchange funding rate is injected via `peer_funding_rate` in config params.
 *
 * Params:
 *   - min_spread: minimum funding rate differential to enter (default 0.0001 = 1bps)
 *   - order_size: size per leg (default 0.5)
 *   - max_position: maximum position size per side (default 2.0)
 *   - peer_funding_rate: funding rate on the peer exchange (required, injected per tick)
 */
export class FundingArb extends BaseStrategy {
  readonly name = 'funding-arb'

  onTick(ctx: TickContext): StrategyDecision[] {
    const { ticker, positions, config } = ctx
    const symbol = ticker.symbol

    // Extract params with defaults
    const minSpread = typeof config.params['min_spread'] === 'number'
      ? config.params['min_spread'] : 0.0001
    const orderSize = typeof config.params['order_size'] === 'number'
      ? config.params['order_size'] : 0.5
    const maxPosition = typeof config.params['max_position'] === 'number'
      ? config.params['max_position'] : 2.0

    // Peer funding rate is required
    const peerFundingRate = config.params['peer_funding_rate']
    if (typeof peerFundingRate !== 'number') {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: 'FundingArb: peer_funding_rate not provided',
      }]
    }

    // Compute funding spread: primary - peer
    const primaryFunding = ticker.fundingRate
    const spread = primaryFunding - peerFundingRate

    // Check if spread exceeds threshold
    if (Math.abs(spread) < minSpread) {
      return [{
        action: 'HOLD',
        symbol,
        size: 0,
        orderType: 'GTC',
        confidence: 0,
        reason: `FundingArb: funding spread ${(spread * 10000).toFixed(2)}bps below threshold ${(minSpread * 10000).toFixed(2)}bps`,
      }]
    }

    // Calculate net position for the symbol
    const netPosition = positions
      .filter(p => p.symbol === symbol)
      .reduce((sum, p) => sum + (p.side === 'LONG' ? p.size : -p.size), 0)

    // Determine direction based on spread sign
    // spread > 0: primary funding higher => short primary (pay less funding)
    // spread < 0: primary funding lower => long primary (get paid funding)
    if (spread > 0) {
      // Short primary — check position limit
      if (netPosition <= -maxPosition) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: `FundingArb: max short position reached (${netPosition.toFixed(4)})`,
        }]
      }

      const confidence = Math.min(90, Math.round(Math.abs(spread) / minSpread * 30))

      return [{
        action: 'SELL',
        symbol,
        size: orderSize,
        orderType: 'GTC',
        confidence,
        reason: `FundingArb: short primary, funding spread +${(spread * 10000).toFixed(2)}bps (primary ${(primaryFunding * 10000).toFixed(2)}bps > peer ${(peerFundingRate * 10000).toFixed(2)}bps)`,
      }]
    } else {
      // Long primary — check position limit
      if (netPosition >= maxPosition) {
        return [{
          action: 'HOLD',
          symbol,
          size: 0,
          orderType: 'GTC',
          confidence: 0,
          reason: `FundingArb: max long position reached (${netPosition.toFixed(4)})`,
        }]
      }

      const confidence = Math.min(90, Math.round(Math.abs(spread) / minSpread * 30))

      return [{
        action: 'BUY',
        symbol,
        size: orderSize,
        orderType: 'GTC',
        confidence,
        reason: `FundingArb: long primary, funding spread ${(spread * 10000).toFixed(2)}bps (primary ${(primaryFunding * 10000).toFixed(2)}bps < peer ${(peerFundingRate * 10000).toFixed(2)}bps)`,
      }]
    }
  }
}
