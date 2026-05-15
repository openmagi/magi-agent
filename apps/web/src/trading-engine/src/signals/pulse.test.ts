import { Pulse } from './pulse.js'
import type { PulseSignal, Ticker } from '../types.js'

function makeTicker(overrides: Partial<Ticker> = {}): Ticker {
  return {
    symbol: 'XRP',  // Not in any sector to avoid FIRST_JUMP interference
    mid: 50000,
    bid: 49990,
    ask: 50010,
    lastPrice: 50000,
    volume24h: 1_000_000,
    openInterest: 500_000,
    fundingRate: 0.0001,
    timestamp: Date.now(),
    ...overrides,
  }
}

describe('Pulse', () => {
  let pulse: Pulse

  beforeEach(() => {
    pulse = new Pulse()
  })

  it('should return empty signals on first scan (no previous data)', () => {
    const tickers = [makeTicker()]
    const result = pulse.scan(tickers)
    expect(result.signals).toEqual([])
    expect(result.timestamp).toBeGreaterThan(0)
  })

  it('should detect IMMEDIATE_MOVER on OI change >= 15%', () => {
    const t = Date.now()
    // Baseline scan
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    // OI jumps 15%
    const result = pulse.scan([makeTicker({ openInterest: 115_000, volume24h: 1000, lastPrice: 102, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.type).toBe('IMMEDIATE_MOVER')
    expect(result.signals[0]!.symbol).toBe('XRP')
  })

  it('should detect IMMEDIATE_MOVER on volume multiple >= 5x', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    // Volume jumps 5x, OI stays same
    const result = pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 5000, lastPrice: 102, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.type).toBe('IMMEDIATE_MOVER')
  })

  it('should detect CONTRIB_EXPLOSION on OI +15% AND volume 5x', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    // OI +15% AND volume 5x
    const result = pulse.scan([makeTicker({ openInterest: 115_000, volume24h: 5000, lastPrice: 102, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.type).toBe('CONTRIB_EXPLOSION')
    expect(result.signals[0]!.confidence).toBe(95)
  })

  it('should detect NEW_ENTRY_DEEP on OI +8% with low volume (<2x)', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    // OI +8%, volume stays low (<2x)
    const result = pulse.scan([makeTicker({ openInterest: 108_000, volume24h: 1500, lastPrice: 101, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.type).toBe('NEW_ENTRY_DEEP')
    expect(result.signals[0]!.confidence).toBe(65)
  })

  it('should detect DEEP_CLIMBER after 3 consecutive OI climbs of 5%+', () => {
    const t = Date.now()
    // Scan 0: baseline
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    // Scan 1: OI +5% (no DEEP_CLIMBER yet, only 1 consecutive)
    pulse.scan([makeTicker({ openInterest: 105_000, volume24h: 1000, lastPrice: 100.5, timestamp: t + 1000 })])
    // Scan 2: OI +5% again (2 consecutive)
    pulse.scan([makeTicker({ openInterest: 110_250, volume24h: 1000, lastPrice: 101, timestamp: t + 2000 })])
    // Scan 3: OI +5% again (3 consecutive → DEEP_CLIMBER)
    const result = pulse.scan([makeTicker({ openInterest: 115_763, volume24h: 1000, lastPrice: 101.5, timestamp: t + 3000 })])

    const deepClimber = result.signals.find((s: PulseSignal) => s.type === 'DEEP_CLIMBER')
    expect(deepClimber).toBeDefined()
    expect(deepClimber!.confidence).toBe(55)
  })

  it('should detect VOLUME_SURGE on volume >= 3x', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    // Volume 3x, OI low change
    const result = pulse.scan([makeTicker({ openInterest: 101_000, volume24h: 3000, lastPrice: 102, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.type).toBe('VOLUME_SURGE')
    expect(result.signals[0]!.confidence).toBe(70)
  })

  it('should detect OI_BREAKOUT on OI jump >= 8%', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    // OI +8%, volume 2.5x (above 2x so not NEW_ENTRY_DEEP, below 3x so not VOLUME_SURGE)
    const result = pulse.scan([makeTicker({ openInterest: 108_000, volume24h: 2500, lastPrice: 101, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.type).toBe('OI_BREAKOUT')
    expect(result.signals[0]!.confidence).toBe(60)
  })

  it('should detect FUNDING_FLIP on funding rate reversal', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, fundingRate: 0.01, lastPrice: 100, timestamp: t })])
    // Funding flips from 0.01 to -0.01 (change = -0.02, which is >= |0.01| * 0.5)
    const result = pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, fundingRate: -0.01, lastPrice: 99, timestamp: t + 1000 })])

    const fundingFlip = result.signals.find((s: PulseSignal) => s.type === 'FUNDING_FLIP')
    expect(fundingFlip).toBeDefined()
    expect(fundingFlip!.confidence).toBe(50)
  })

  it('should assign correct confidence per signal type', () => {
    const confidenceMap: Record<string, number> = {
      FIRST_JUMP: 100,
      CONTRIB_EXPLOSION: 95,
      IMMEDIATE_MOVER: 80,
      VOLUME_SURGE: 70,
      NEW_ENTRY_DEEP: 65,
      OI_BREAKOUT: 60,
      DEEP_CLIMBER: 55,
      FUNDING_FLIP: 50,
    }

    const t = Date.now()
    // Test CONTRIB_EXPLOSION confidence
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    const ce = pulse.scan([makeTicker({ openInterest: 115_000, volume24h: 5000, lastPrice: 102, timestamp: t + 1000 })])
    expect(ce.signals[0]!.confidence).toBe(confidenceMap['CONTRIB_EXPLOSION'])

    pulse.reset()

    // Test VOLUME_SURGE confidence
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    const vs = pulse.scan([makeTicker({ openInterest: 101_000, volume24h: 3000, lastPrice: 102, timestamp: t + 1000 })])
    expect(vs.signals[0]!.confidence).toBe(confidenceMap['VOLUME_SURGE'])
  })

  it('should determine LONG direction on positive price change', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    const result = pulse.scan([makeTicker({ openInterest: 115_000, volume24h: 1000, lastPrice: 105, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.direction).toBe('LONG')
  })

  it('should determine SHORT direction on negative price change', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    const result = pulse.scan([makeTicker({ openInterest: 115_000, volume24h: 1000, lastPrice: 95, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.direction).toBe('SHORT')
  })

  it('should only emit highest-tier signal per symbol', () => {
    const t = Date.now()
    pulse.scan([makeTicker({ openInterest: 100_000, volume24h: 1000, lastPrice: 100, timestamp: t })])
    // This triggers CONTRIB_EXPLOSION (OI>=15% AND vol>=5x), which also matches
    // IMMEDIATE_MOVER, VOLUME_SURGE, OI_BREAKOUT — only CONTRIB_EXPLOSION should be emitted
    const result = pulse.scan([makeTicker({ openInterest: 116_000, volume24h: 5500, lastPrice: 102, timestamp: t + 1000 })])

    expect(result.signals).toHaveLength(1)
    expect(result.signals[0]!.type).toBe('CONTRIB_EXPLOSION')
  })

  it('should maintain rolling window of max 5 snapshots', () => {
    const t = Date.now()
    // Do 7 scans — window should only keep 5
    for (let i = 0; i < 7; i++) {
      pulse.scan([makeTicker({
        openInterest: 100_000 + i * 1000,
        volume24h: 1000,
        lastPrice: 100 + i,
        timestamp: t + i * 1000,
      })])
    }
    // Access internal state to verify window size
    // We use scan count by verifying behavior still works (DEEP_CLIMBER needs history)
    // Alternative: test via reset behavior
    const snapshotCount = (pulse as unknown as { snapshots: Map<string, unknown[]> }).snapshots.get('XRP')?.length
    expect(snapshotCount).toBeLessThanOrEqual(5)
  })

  it('should detect FIRST_JUMP for first sector mover', () => {
    const t = Date.now()
    // Configure sectors: ETH is in L1
    pulse = new Pulse()

    // Baseline for ETH
    pulse.scan([makeTicker({ symbol: 'ETH', openInterest: 100_000, volume24h: 1000, lastPrice: 3000, timestamp: t })])

    // ETH triggers IMMEDIATE_MOVER (OI +15%) — first in L1 sector → FIRST_JUMP
    const result = pulse.scan([makeTicker({ symbol: 'ETH', openInterest: 115_000, volume24h: 1000, lastPrice: 3050, timestamp: t + 1000 })])

    const firstJump = result.signals.find((s: PulseSignal) => s.type === 'FIRST_JUMP')
    expect(firstJump).toBeDefined()
    expect(firstJump!.confidence).toBe(100)
    expect(firstJump!.symbol).toBe('ETH')
  })
})
