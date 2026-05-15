import type { TradeRecord, ReflectMetrics, EngineConfig } from '../types.js'

export interface ReflectAdjustment {
  field: string
  currentValue: number
  newValue: number
  reason: string
}

const GUARDRAILS = {
  radarThreshold: { min: 130, max: 250 },
  dailyLossLimit: { min: 100, max: 2000 },
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

export class ReflectAnalyzer {
  analyze(trades: TradeRecord[]): ReflectMetrics {
    if (trades.length === 0) {
      return {
        totalTrades: 0,
        winRate: 0,
        netPnl: 0,
        grossWins: 0,
        grossLosses: 0,
        fdr: 0,
        avgHoldingPeriodMs: 0,
        longestWinStreak: 0,
        longestLoseStreak: 0,
        monsterDependency: 0,
        directionSplit: {
          longWinRate: 0,
          shortWinRate: 0,
          longPnl: 0,
          shortPnl: 0,
        },
        periodStart: 0,
        periodEnd: 0,
      }
    }

    const totalTrades = trades.length
    const wins = trades.filter(t => t.netPnl > 0)
    const losses = trades.filter(t => t.netPnl <= 0)

    const winRate = wins.length / totalTrades

    // Gross wins/losses using pnl (before fees) for winning/losing trades
    const grossWins = wins.reduce((sum, t) => sum + t.pnl, 0)
    const grossLosses = losses.reduce((sum, t) => sum + t.pnl, 0)

    const totalFees = trades.reduce((sum, t) => sum + t.fees, 0)
    const fdr = grossWins > 0 ? totalFees / grossWins : 0

    const netPnl = trades.reduce((sum, t) => sum + t.netPnl, 0)

    const avgHoldingPeriodMs =
      trades.reduce((sum, t) => sum + (t.exitTime - t.entryTime), 0) / totalTrades

    // Streaks
    let longestWinStreak = 0
    let longestLoseStreak = 0
    let currentWinStreak = 0
    let currentLoseStreak = 0

    for (const trade of trades) {
      if (trade.netPnl > 0) {
        currentWinStreak++
        currentLoseStreak = 0
        if (currentWinStreak > longestWinStreak) {
          longestWinStreak = currentWinStreak
        }
      } else {
        currentLoseStreak++
        currentWinStreak = 0
        if (currentLoseStreak > longestLoseStreak) {
          longestLoseStreak = currentLoseStreak
        }
      }
    }

    // Monster dependency
    const bestTrade = trades.reduce((best, t) => (t.netPnl > best.netPnl ? t : best), trades[0]!)
    const monsterDependency = netPnl > 0 ? (bestTrade.netPnl / netPnl) * 100 : 0

    // Direction split
    const longTrades = trades.filter(t => t.side === 'LONG')
    const shortTrades = trades.filter(t => t.side === 'SHORT')

    const longWins = longTrades.filter(t => t.netPnl > 0)
    const shortWins = shortTrades.filter(t => t.netPnl > 0)

    const longWinRate = longTrades.length > 0 ? longWins.length / longTrades.length : 0
    const shortWinRate = shortTrades.length > 0 ? shortWins.length / shortTrades.length : 0

    const longPnl = longTrades.reduce((sum, t) => sum + t.netPnl, 0)
    const shortPnl = shortTrades.reduce((sum, t) => sum + t.netPnl, 0)

    // Period
    const periodStart = Math.min(...trades.map(t => t.entryTime))
    const periodEnd = Math.max(...trades.map(t => t.exitTime))

    return {
      totalTrades,
      winRate,
      netPnl,
      grossWins,
      grossLosses,
      fdr,
      avgHoldingPeriodMs,
      longestWinStreak,
      longestLoseStreak,
      monsterDependency,
      directionSplit: {
        longWinRate,
        shortWinRate,
        longPnl,
        shortPnl,
      },
      periodStart,
      periodEnd,
    }
  }

  suggest(metrics: ReflectMetrics): ReflectAdjustment[] {
    // suggest() uses preset defaults as currentValue since it has no config reference.
    // applyAdjustments resolves actual values against the real config via delta arithmetic.
    const DEFAULT_RADAR = 170
    const DEFAULT_DAILY_LOSS = 500

    const adjustments: ReflectAdjustment[] = []

    // Helper to upsert a radarThreshold adjustment, keeping the larger delta
    const upsertRadar = (delta: number, reason: string): void => {
      const existing = adjustments.find(a => a.field === 'apex.radarThreshold')
      if (existing) {
        const existingDelta = existing.newValue - existing.currentValue
        if (Math.abs(delta) > Math.abs(existingDelta)) {
          existing.newValue = existing.currentValue + delta
          existing.reason = reason
        }
      } else {
        adjustments.push({
          field: 'apex.radarThreshold',
          currentValue: DEFAULT_RADAR,
          newValue: DEFAULT_RADAR + delta,
          reason,
        })
      }
    }

    // FDR > 30% → raise radarThreshold by 10
    if (metrics.fdr > 0.30) {
      upsertRadar(10, 'Too many low-quality entries dragging fees')
    }

    // Win rate < 40% → raise radarThreshold by 15
    if (metrics.winRate < 0.40 && metrics.totalTrades > 0) {
      upsertRadar(15, 'Entry quality too low')
    }

    // Win rate > 70% → lower radarThreshold by 10
    if (metrics.winRate > 0.70 && metrics.totalTrades > 0) {
      upsertRadar(-10, 'Can afford more entries')
    }

    // longestLoseStreak >= 5 → reduce dailyLossLimit by 20%
    if (metrics.longestLoseStreak >= 5) {
      adjustments.push({
        field: 'apex.dailyLossLimit',
        currentValue: DEFAULT_DAILY_LOSS,
        newValue: DEFAULT_DAILY_LOSS * 0.8,
        reason: 'Extended losing streak protection',
      })
    }

    // monsterDependency > 50% → raise radarThreshold by 5
    if (metrics.monsterDependency > 50) {
      upsertRadar(5, 'Over-reliant on single trade')
    }

    // Direction split warnings (text-only, no numeric adjustment)
    if (metrics.directionSplit.longWinRate < 0.30 && metrics.directionSplit.longWinRate > 0) {
      adjustments.push({
        field: 'strategy.longExposure',
        currentValue: 1,
        newValue: 1,
        reason: 'Consider reducing long exposure',
      })
    }

    if (metrics.directionSplit.shortWinRate < 0.30 && metrics.directionSplit.shortWinRate > 0) {
      adjustments.push({
        field: 'strategy.shortExposure',
        currentValue: 1,
        newValue: 1,
        reason: 'Consider reducing short exposure',
      })
    }

    return adjustments
  }

  applyAdjustments(config: EngineConfig, adjustments: ReflectAdjustment[]): EngineConfig {
    // Deep clone to avoid mutating input
    const newConfig: EngineConfig = {
      ...config,
      apex: { ...config.apex },
    }

    for (const adj of adjustments) {
      if (adj.field === 'apex.radarThreshold') {
        // Use config's actual current value, not adj.currentValue (which is preset default)
        const delta = adj.newValue - adj.currentValue
        const applied = config.apex.radarThreshold + delta
        newConfig.apex.radarThreshold = clamp(
          applied,
          GUARDRAILS.radarThreshold.min,
          GUARDRAILS.radarThreshold.max,
        )
      } else if (adj.field === 'apex.dailyLossLimit') {
        const delta = adj.newValue - adj.currentValue
        const applied = config.apex.dailyLossLimit + delta
        newConfig.apex.dailyLossLimit = clamp(
          applied,
          GUARDRAILS.dailyLossLimit.min,
          GUARDRAILS.dailyLossLimit.max,
        )
      }
    }

    return newConfig
  }

  generateReport(metrics: ReflectMetrics, adjustments: ReflectAdjustment[]): string {
    const date = new Date().toISOString().slice(0, 10)

    const winRatePct = (metrics.winRate * 100).toFixed(1)
    const netPnlStr = metrics.netPnl >= 0 ? `$${metrics.netPnl.toFixed(2)}` : `-$${Math.abs(metrics.netPnl).toFixed(2)}`
    const grossWinsStr = `$${metrics.grossWins.toFixed(2)}`
    const grossLossesStr = metrics.grossLosses <= 0
      ? `-$${Math.abs(metrics.grossLosses).toFixed(2)}`
      : `$${metrics.grossLosses.toFixed(2)}`
    const fdrPct = (metrics.fdr * 100).toFixed(1)
    const avgHoldH = (metrics.avgHoldingPeriodMs / 3_600_000).toFixed(1)
    const monsterPct = metrics.monsterDependency.toFixed(1)

    const longWinRatePct = (metrics.directionSplit.longWinRate * 100).toFixed(1)
    const shortWinRatePct = (metrics.directionSplit.shortWinRate * 100).toFixed(1)
    const longPnlStr = metrics.directionSplit.longPnl >= 0
      ? `+$${metrics.directionSplit.longPnl.toFixed(2)}`
      : `-$${Math.abs(metrics.directionSplit.longPnl).toFixed(2)}`
    const shortPnlStr = metrics.directionSplit.shortPnl >= 0
      ? `+$${metrics.directionSplit.shortPnl.toFixed(2)}`
      : `-$${Math.abs(metrics.directionSplit.shortPnl).toFixed(2)}`

    const adjustmentsSection =
      adjustments.length === 0
        ? '- No adjustments needed'
        : adjustments
            .map(a => {
              if (a.newValue === a.currentValue) {
                return `- ${a.reason}`
              }
              return `- \`${a.field}\`: ${a.currentValue} → ${a.newValue} (${a.reason})`
            })
            .join('\n')

    return `# REFLECT Report — ${date}

## Summary
- Trades: ${metrics.totalTrades} | Win Rate: ${winRatePct}%
- Net PnL: ${netPnlStr} | Gross Wins: ${grossWinsStr} | Gross Losses: ${grossLossesStr}
- FDR: ${fdrPct}% | Avg Hold: ${avgHoldH}h

## Streaks
- Longest Win: ${metrics.longestWinStreak} | Longest Loss: ${metrics.longestLoseStreak}
- Monster Dependency: ${monsterPct}%

## Direction Split
- Long: ${longWinRatePct}% win rate, ${longPnlStr}
- Short: ${shortWinRatePct}% win rate, ${shortPnlStr}

## Adjustments
${adjustmentsSection}
`
  }
}
