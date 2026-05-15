import { ReflectAnalyzer } from './analyzer.js'
import type { ReflectAdjustment } from './analyzer.js'
import type { TradeRecord, EngineConfig } from '../types.js'
import { GUARD_PRESETS } from '../types.js'

function makeTrade(overrides: Partial<TradeRecord>): TradeRecord {
  return {
    id: 'test-' + Math.random().toString(36).slice(2),
    symbol: 'ETH-PERP',
    side: 'LONG',
    entryPrice: 3450,
    exitPrice: 3500,
    size: 0.1,
    entryTime: Date.now() - 3600000,
    exitTime: Date.now(),
    pnl: 5,
    fees: 0.5,
    netPnl: 4.5,
    exitReason: 'guard_tier',
    slotId: 0,
    ...overrides,
  }
}

function makeConfig(overrides: Partial<EngineConfig['apex']> = {}): EngineConfig {
  return {
    exchange: { name: 'hyperliquid', testnet: true },
    apex: {
      preset: 'default',
      maxSlots: 3,
      leverage: 10,
      radarThreshold: 170,
      dailyLossLimit: 500,
      tickIntervalMs: 5000,
      ...overrides,
    },
    guard: GUARD_PRESETS.moderate,
    strategy: {
      name: 'momentum',
      symbols: ['ETH-PERP'],
      params: {},
    },
    reflect: { autoAdjust: true, intervalTicks: 100 },
  }
}

describe('ReflectAnalyzer', () => {
  const analyzer = new ReflectAnalyzer()

  it('should handle empty trade history', () => {
    const metrics = analyzer.analyze([])
    expect(metrics.totalTrades).toBe(0)
    expect(metrics.winRate).toBe(0)
    expect(metrics.netPnl).toBe(0)
    expect(metrics.grossWins).toBe(0)
    expect(metrics.grossLosses).toBe(0)
    expect(metrics.fdr).toBe(0)
    expect(metrics.avgHoldingPeriodMs).toBe(0)
    expect(metrics.longestWinStreak).toBe(0)
    expect(metrics.longestLoseStreak).toBe(0)
    expect(metrics.monsterDependency).toBe(0)
    expect(metrics.directionSplit.longWinRate).toBe(0)
    expect(metrics.directionSplit.shortWinRate).toBe(0)
    expect(metrics.directionSplit.longPnl).toBe(0)
    expect(metrics.directionSplit.shortPnl).toBe(0)
  })

  it('should compute win rate correctly', () => {
    const trades = [
      makeTrade({ netPnl: 10 }),  // win
      makeTrade({ netPnl: 5 }),   // win
      makeTrade({ netPnl: -3 }),  // loss
      makeTrade({ netPnl: -2 }),  // loss
      makeTrade({ netPnl: 8 }),   // win
    ]
    const metrics = analyzer.analyze(trades)
    expect(metrics.totalTrades).toBe(5)
    expect(metrics.winRate).toBeCloseTo(0.6, 5)
  })

  it('should compute FDR (Fee Drag Ratio)', () => {
    // FDR = totalFees / grossWins
    const trades = [
      makeTrade({ netPnl: 10, fees: 2, pnl: 12 }),  // win
      makeTrade({ netPnl: 5, fees: 1, pnl: 6 }),    // win
      makeTrade({ netPnl: -3, fees: 0.5, pnl: -2.5 }),  // loss
    ]
    // grossWins = 12 + 6 = 18 (using pnl before fees for winning trades)
    // totalFees = 2 + 1 + 0.5 = 3.5
    // FDR = 3.5 / 18 ≈ 0.194...
    const metrics = analyzer.analyze(trades)
    expect(metrics.fdr).toBeCloseTo(3.5 / 18, 5)
  })

  it('should compute direction split', () => {
    const trades = [
      makeTrade({ side: 'LONG', netPnl: 10 }),
      makeTrade({ side: 'LONG', netPnl: -5 }),
      makeTrade({ side: 'LONG', netPnl: 8 }),
      makeTrade({ side: 'SHORT', netPnl: 3 }),
      makeTrade({ side: 'SHORT', netPnl: -2 }),
    ]
    const metrics = analyzer.analyze(trades)
    // Long: 2 wins out of 3 → 66.67% win rate, PnL = 10 - 5 + 8 = 13
    expect(metrics.directionSplit.longWinRate).toBeCloseTo(2 / 3, 5)
    expect(metrics.directionSplit.longPnl).toBeCloseTo(13, 5)
    // Short: 1 win out of 2 → 50% win rate, PnL = 3 - 2 = 1
    expect(metrics.directionSplit.shortWinRate).toBeCloseTo(0.5, 5)
    expect(metrics.directionSplit.shortPnl).toBeCloseTo(1, 5)
  })

  it('should compute monster dependency', () => {
    // monsterDependency = bestTrade.netPnl / totalNetPnl * 100 (when totalNetPnl > 0)
    const trades = [
      makeTrade({ netPnl: 100 }),  // monster trade
      makeTrade({ netPnl: 20 }),
      makeTrade({ netPnl: 30 }),
      makeTrade({ netPnl: -50 }),
    ]
    // totalNetPnl = 100 + 20 + 30 - 50 = 100
    // monsterDependency = 100 / 100 * 100 = 100%
    const metrics = analyzer.analyze(trades)
    expect(metrics.monsterDependency).toBeCloseTo(100, 5)
  })

  it('should compute monster dependency as 0 when net PnL is not positive', () => {
    const trades = [
      makeTrade({ netPnl: -10 }),
      makeTrade({ netPnl: -5 }),
    ]
    const metrics = analyzer.analyze(trades)
    expect(metrics.monsterDependency).toBe(0)
  })

  it('should compute win and lose streaks', () => {
    const trades = [
      makeTrade({ netPnl: 5 }),   // win (streak 1)
      makeTrade({ netPnl: 3 }),   // win (streak 2)
      makeTrade({ netPnl: 2 }),   // win (streak 3)
      makeTrade({ netPnl: -1 }),  // loss (streak 1)
      makeTrade({ netPnl: -2 }),  // loss (streak 2)
      makeTrade({ netPnl: 4 }),   // win (streak 1)
      makeTrade({ netPnl: -3 }),  // loss (streak 1)
      makeTrade({ netPnl: -1 }),  // loss (streak 2)
      makeTrade({ netPnl: -2 }),  // loss (streak 3)
      makeTrade({ netPnl: -4 }),  // loss (streak 4)
      makeTrade({ netPnl: -5 }),  // loss (streak 5)
    ]
    const metrics = analyzer.analyze(trades)
    expect(metrics.longestWinStreak).toBe(3)
    expect(metrics.longestLoseStreak).toBe(5)
  })

  it('should suggest raising radar threshold when FDR > 30%', () => {
    // Create metrics with FDR > 30% and neutral other stats
    const trades = [
      makeTrade({ netPnl: 5, fees: 3, pnl: 8 }),   // win with heavy fees → FDR > 30%
      makeTrade({ netPnl: 5, fees: 3, pnl: 8 }),
      makeTrade({ netPnl: 5, fees: 3, pnl: 8 }),
      makeTrade({ netPnl: 5, fees: 3, pnl: 8 }),
      makeTrade({ netPnl: 5, fees: 3, pnl: 8 }),
      makeTrade({ netPnl: 5, fees: 3, pnl: 8 }),
      makeTrade({ netPnl: 5, fees: 3, pnl: 8 }),
      makeTrade({ netPnl: -1, fees: 0.1, pnl: -0.9 }),
    ]
    const metrics = analyzer.analyze(trades)
    // FDR = totalFees / grossWins = (7*3 + 0.1) / (7*8) = 21.1 / 56 ≈ 37.7%
    expect(metrics.fdr).toBeGreaterThan(0.30)

    const adjustments = analyzer.suggest(metrics)
    const fdrAdjustment = adjustments.find(a => a.reason.includes('fee') || a.reason.toLowerCase().includes('fee'))
    expect(fdrAdjustment).toBeDefined()
    expect(fdrAdjustment?.field).toBe('apex.radarThreshold')
    expect(fdrAdjustment?.newValue).toBe(fdrAdjustment!.currentValue + 10)
  })

  it('should suggest raising radar threshold when win rate < 40%', () => {
    const trades = [
      makeTrade({ netPnl: 5 }),
      makeTrade({ netPnl: -3 }),
      makeTrade({ netPnl: -3 }),
      makeTrade({ netPnl: -3 }),
      makeTrade({ netPnl: -3 }),
    ]
    // win rate = 1/5 = 20%
    const metrics = analyzer.analyze(trades)
    expect(metrics.winRate).toBeLessThan(0.4)

    const adjustments = analyzer.suggest(metrics)
    const winRateAdj = adjustments.find(a => a.field === 'apex.radarThreshold' && a.reason.toLowerCase().includes('entry quality'))
    expect(winRateAdj).toBeDefined()
    expect(winRateAdj?.newValue).toBe(winRateAdj!.currentValue + 15)
  })

  it('should suggest lowering radar threshold when win rate > 70%', () => {
    const trades = [
      makeTrade({ netPnl: 5 }),
      makeTrade({ netPnl: 5 }),
      makeTrade({ netPnl: 5 }),
      makeTrade({ netPnl: 5 }),
      makeTrade({ netPnl: -2 }),
    ]
    // win rate = 4/5 = 80%
    const metrics = analyzer.analyze(trades)
    expect(metrics.winRate).toBeGreaterThan(0.7)

    const adjustments = analyzer.suggest(metrics)
    const adj = adjustments.find(a => a.field === 'apex.radarThreshold' && a.newValue < a.currentValue)
    expect(adj).toBeDefined()
    expect(adj?.newValue).toBe(adj!.currentValue - 10)
  })

  it('should suggest reducing daily loss limit on 5+ consecutive losses', () => {
    const trades = [
      makeTrade({ netPnl: 5 }),
      makeTrade({ netPnl: -1 }),
      makeTrade({ netPnl: -2 }),
      makeTrade({ netPnl: -3 }),
      makeTrade({ netPnl: -1 }),
      makeTrade({ netPnl: -2 }),
    ]
    // longestLoseStreak = 5
    const metrics = analyzer.analyze(trades)
    expect(metrics.longestLoseStreak).toBe(5)

    const adjustments = analyzer.suggest(metrics)
    const adj = adjustments.find(a => a.field === 'apex.dailyLossLimit')
    expect(adj).toBeDefined()
    expect(adj?.newValue).toBe(adj!.currentValue * 0.8)
  })

  it('should apply adjustments within guardrail bounds', () => {
    const config = makeConfig({ radarThreshold: 170, dailyLossLimit: 500 })
    const adjustments: ReflectAdjustment[] = [
      {
        field: 'apex.radarThreshold',
        currentValue: 170,
        newValue: 185,
        reason: 'test',
      },
      {
        field: 'apex.dailyLossLimit',
        currentValue: 500,
        newValue: 400,
        reason: 'test',
      },
    ]
    const newConfig = analyzer.applyAdjustments(config, adjustments)
    expect(newConfig.apex.radarThreshold).toBe(185)
    expect(newConfig.apex.dailyLossLimit).toBe(400)
    // Ensure original is not mutated
    expect(config.apex.radarThreshold).toBe(170)
    expect(config.apex.dailyLossLimit).toBe(500)
  })

  it('should clamp values at guardrail min/max', () => {
    const config = makeConfig({ radarThreshold: 245, dailyLossLimit: 110 })
    const adjustments: ReflectAdjustment[] = [
      {
        field: 'apex.radarThreshold',
        currentValue: 245,
        newValue: 260,  // above max 250
        reason: 'test',
      },
      {
        field: 'apex.dailyLossLimit',
        currentValue: 110,
        newValue: 80,  // below min 100
        reason: 'test',
      },
    ]
    const newConfig = analyzer.applyAdjustments(config, adjustments)
    expect(newConfig.apex.radarThreshold).toBe(250)  // clamped to max
    expect(newConfig.apex.dailyLossLimit).toBe(100)  // clamped to min
  })

  it('should generate markdown report', () => {
    const trades = [
      makeTrade({ netPnl: 100, fees: 5, pnl: 105, side: 'LONG', entryTime: Date.now() - 7200000, exitTime: Date.now() - 3600000 }),
      makeTrade({ netPnl: -30, fees: 2, pnl: -28, side: 'SHORT', entryTime: Date.now() - 3600000, exitTime: Date.now() }),
    ]
    const metrics = analyzer.analyze(trades)
    const adjustments = analyzer.suggest(metrics)
    const report = analyzer.generateReport(metrics, adjustments)

    expect(report).toContain('# REFLECT Report')
    expect(report).toContain('## Summary')
    expect(report).toContain('## Streaks')
    expect(report).toContain('## Direction Split')
    expect(report).toContain('## Adjustments')
    expect(report).toContain('Trades:')
    expect(report).toContain('Win Rate:')
  })

  it('should compute average holding period', () => {
    const now = Date.now()
    const trades = [
      makeTrade({ entryTime: now - 7200000, exitTime: now - 3600000 }),  // 1h hold
      makeTrade({ entryTime: now - 10800000, exitTime: now - 7200000 }), // 1h hold
    ]
    const metrics = analyzer.analyze(trades)
    expect(metrics.avgHoldingPeriodMs).toBeCloseTo(3600000, -3)
  })

  it('should set periodStart and periodEnd from trade times', () => {
    const now = Date.now()
    const trades = [
      makeTrade({ entryTime: now - 10000, exitTime: now - 5000 }),
      makeTrade({ entryTime: now - 20000, exitTime: now - 1000 }),
    ]
    const metrics = analyzer.analyze(trades)
    expect(metrics.periodStart).toBe(now - 20000)
    expect(metrics.periodEnd).toBe(now - 1000)
  })
})
