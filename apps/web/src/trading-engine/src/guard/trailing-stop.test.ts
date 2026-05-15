import { Guard, DEFAULT_LEVERAGE } from './trailing-stop.js'
import type { GuardResult } from './trailing-stop.js'
import type { ApexSlot, GuardConfig } from '../types.js'
import { GUARD_PRESETS } from '../types.js'

function makeSlot(overrides: Partial<ApexSlot> = {}): ApexSlot {
  return {
    id: 1,
    status: 'OPEN',
    symbol: 'BTC-PERP',
    side: 'LONG',
    entryPrice: 100_000,
    size: 0.1,
    entryTime: 0,
    guardPhase: 'PHASE_1',
    peakRoe: 0,
    currentRoe: 0,
    tierLevel: -1,
    ...overrides,
  }
}

describe('Guard (Trailing Stop)', () => {
  const moderate = GUARD_PRESETS.moderate
  const tight = GUARD_PRESETS.tight

  describe('Phase 1 — Breathe', () => {
    it('should HOLD when ROE is within retrace tolerance', () => {
      const guard = new Guard(moderate)
      // LONG entry at 100k, current price 99_800 → ROE = (99800-100000)/100000*10*100 = -2%
      // moderate phase1RetracePct = 3, so -2% is within tolerance
      const slot = makeSlot({ entryTime: 0 })
      const now = 10 * 60_000 // 10 minutes in
      const result = guard.evaluate(slot, 99_800, now)

      expect(result.action).toBe('HOLD')
    })

    it('should EXIT on retrace exceeding phase1RetracePct', () => {
      const guard = new Guard(moderate)
      // moderate phase1RetracePct = 3
      // LONG entry at 100k, need ROE < -3%
      // ROE = (price - 100000) / 100000 * 10 * 100 = -3.1%
      // price = 100000 * (1 - 3.1 / 10 / 100) = 100000 * 0.9969 = 99690
      const slot = makeSlot({ entryTime: 0 })
      const now = 5 * 60_000
      const result = guard.evaluate(slot, 99_690, now)

      expect(result.action).toBe('EXIT')
      expect(result.reason).toBe('phase1_retrace')
    })

    it('should EXIT on stagnation (phase1MaxDurationMs exceeded, no tier hit)', () => {
      const guard = new Guard(moderate)
      // moderate phase1MaxDurationMs = 90 * 60_000 = 5_400_000
      // slot still in PHASE_1 (tierLevel = -1), elapsed > 90min
      const slot = makeSlot({ entryTime: 0, tierLevel: -1 })
      const now = 91 * 60_000 // 91 minutes
      // Price barely above entry so no retrace exit
      const result = guard.evaluate(slot, 100_010, now)

      expect(result.action).toBe('EXIT')
      expect(result.reason).toBe('phase1_stagnation')
    })

    it('should EXIT on weak peak (peak ROE < 3%, 45min elapsed)', () => {
      const guard = new Guard(moderate)
      // moderate phase1WeakPeakRoe = 3, phase1WeakPeakDurationMs = 45 * 60_000
      // Peak ROE of 2% and 46 minutes elapsed
      const slot = makeSlot({ entryTime: 0, peakRoe: 2 })
      const now = 46 * 60_000
      // Current price at entry so ROE ~ 0 (no retrace exit)
      const result = guard.evaluate(slot, 100_010, now)

      expect(result.action).toBe('EXIT')
      expect(result.reason).toBe('phase1_weak_peak')
    })

    it('should graduate to PHASE_2 when ROE hits first tier', () => {
      const guard = new Guard(moderate)
      // moderate tiers[0].roePct = 10
      // LONG: ROE = (price - 100000) / 100000 * 10 * 100 = 10%
      // price = 100000 * (1 + 10 / 10 / 100) = 100000 * 1.01 = 101_000
      const slot = makeSlot({ entryTime: 0 })
      const now = 10 * 60_000
      const result = guard.evaluate(slot, 101_000, now)

      expect(result.action).toBe('HOLD')
      expect(result.newTierLevel).toBe(0)
    })
  })

  describe('Phase 2 — Lock', () => {
    it('should ratchet tier up as ROE grows (tier 0 → tier 1 → tier 2)', () => {
      const guard = new Guard(moderate)
      // moderate tiers: [10, 20, 35, 50, 75, 100]
      // Start at tier 0 (PHASE_2)
      const slot = makeSlot({
        entryTime: 0,
        guardPhase: 'PHASE_2',
        tierLevel: 0,
        peakRoe: 10,
      })

      // ROE = 20% → should hit tier 1 (roePct=20)
      // price = 100000 * (1 + 20/10/100) = 100000 * 1.02 = 102_000
      const now = 20 * 60_000
      const r1 = guard.evaluate(slot, 102_000, now)
      expect(r1.action).toBe('HOLD')
      expect(r1.newTierLevel).toBe(1)

      // Now simulate tier 1 active, ROE = 35% → tier 2
      slot.tierLevel = 1
      slot.guardPhase = 'PHASE_2'
      slot.peakRoe = 20
      // price = 100000 * (1 + 35/10/100) = 100000 * 1.035 = 103_500
      const r2 = guard.evaluate(slot, 103_500, now)
      expect(r2.action).toBe('HOLD')
      expect(r2.newTierLevel).toBe(2)
    })

    it('should EXIT when ROE drops below current tier floor', () => {
      const guard = new Guard(moderate)
      // tier 1: roePct=20, floorPct=12
      // ROE drops to 11% → below floor 12 → EXIT
      const slot = makeSlot({
        entryTime: 0,
        guardPhase: 'PHASE_2',
        tierLevel: 1,
        peakRoe: 25,
      })
      // ROE = 11% → price = 100000 * (1 + 11/10/100) = 100000 * 1.011 = 101_100
      const now = 30 * 60_000
      const result = guard.evaluate(slot, 101_100, now)

      expect(result.action).toBe('EXIT')
      expect(result.reason).toBe('tier_floor_breach')
    })

    it('should never lower tier level (even if ROE drops between tiers)', () => {
      const guard = new Guard(moderate)
      // tier 2: roePct=35, floorPct=22
      // ROE drops to 23% → above floor 22, but below tier 2 roePct 35
      // Tier should NOT go down to tier 1
      const slot = makeSlot({
        entryTime: 0,
        guardPhase: 'PHASE_2',
        tierLevel: 2,
        peakRoe: 40,
      })
      // ROE = 23% → price = 100000 * (1 + 23/10/100) = 100000 * 1.023 = 102_300
      const now = 30 * 60_000
      const result = guard.evaluate(slot, 102_300, now)

      expect(result.action).toBe('HOLD')
      // tierLevel should stay at 2, not drop to 1
      expect(result.newTierLevel).toBe(2)
    })

    it('should EXIT on stagnation TP (tight preset, ROE >= 8%, 1h elapsed)', () => {
      const guard = new Guard(tight)
      // tight: stagnationTp=true, stagnationRoe=8, stagnationDurationMs=60*60_000
      // tier 0: roePct=10, floorPct=5
      // ROE = 9% → above stagnationRoe(8), above floor(5)
      // tier hasn't changed for > 60 minutes
      const entryTime = 0
      const tierChangeTime = 10 * 60_000 // tier reached at 10 min
      const slot = makeSlot({
        entryTime,
        guardPhase: 'PHASE_2',
        tierLevel: 0,
        peakRoe: 9,
      })
      // ROE = 9% → price = 100000 * (1 + 9/10/100) = 100000 * 1.009 = 100_900
      // now = tierChangeTime + 61 minutes
      // But since we don't track tier change time externally, we use entryTime + elapsed
      // stagnation checks elapsed since last tier change — we need to consider how the Guard
      // tracks this. The guard can track lastTierChangeTime internally or use slot.entryTime
      // Since the slot was in tier 0 from the beginning of PHASE_2, the elapsed time since
      // entering this tier is effectively now - entryTime (for first tier) or tracked internally.
      // Let's set now to 71 minutes (entry at 0, so plenty of stagnation time)
      const now = 71 * 60_000
      const result = guard.evaluate(slot, 100_900, now)

      expect(result.action).toBe('EXIT')
      expect(result.reason).toBe('stagnation_tp')
    })
  })

  describe('Exchange SL Price', () => {
    it('should calculate correct SL price for LONG in Phase 1', () => {
      const guard = new Guard(moderate)
      // moderate phase1RetracePct = 3, leverage = 10 (DEFAULT_LEVERAGE)
      // LONG SL = entryPrice * (1 - phase1RetracePct / 100 / leverage)
      //         = 100000 * (1 - 3 / 100 / 10)
      //         = 100000 * (1 - 0.003) = 100000 * 0.997 = 99_700
      const slot = makeSlot({
        guardPhase: 'PHASE_1',
        tierLevel: -1,
      })
      const sl = guard.getSLPrice(slot)
      expect(sl).toBe(99_700)
    })

    it('should calculate correct SL price for SHORT in Phase 1', () => {
      const guard = new Guard(moderate)
      // SHORT SL = entryPrice * (1 + phase1RetracePct / 100 / leverage)
      //          = 100000 * (1 + 3 / 100 / 10)
      //          = 100000 * 1.003 = 100_300
      const slot = makeSlot({
        side: 'SHORT',
        guardPhase: 'PHASE_1',
        tierLevel: -1,
      })
      const sl = guard.getSLPrice(slot)
      expect(sl).toBeCloseTo(100_300, 2)
    })

    it('should calculate correct SL price for LONG in Phase 2 (with tier floor)', () => {
      const guard = new Guard(moderate)
      // tier 1: floorPct = 12
      // LONG SL = entryPrice * (1 + floorPct / 100 / leverage)
      //         = 100000 * (1 + 12 / 100 / 10)
      //         = 100000 * 1.012 = 101_200
      const slot = makeSlot({
        guardPhase: 'PHASE_2',
        tierLevel: 1,
      })
      const sl = guard.getSLPrice(slot)
      expect(sl).toBe(101_200)
    })
  })
})
