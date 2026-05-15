import type { ApexSlot, GuardConfig, GuardTier } from '../types.js'

export const DEFAULT_LEVERAGE = 10

export interface GuardResult {
  action: 'HOLD' | 'EXIT'
  reason: string
  newTierLevel?: number
  slPrice?: number
}

export class Guard {
  private lastTierChangeTime: number | null = null

  constructor(private config: GuardConfig) {}

  evaluate(slot: ApexSlot, currentPrice: number, now: number): GuardResult {
    const leverage = this.config.leverage ?? DEFAULT_LEVERAGE
    const entryPrice = slot.entryPrice

    // 1. Calculate current ROE
    const currentROE =
      slot.side === 'LONG'
        ? ((currentPrice - entryPrice) / entryPrice) * leverage * 100
        : ((entryPrice - currentPrice) / entryPrice) * leverage * 100

    // 2. Update peak ROE
    const peakRoe = Math.max(slot.peakRoe, currentROE)

    const elapsed = now - slot.entryTime

    // Determine if slot is in PHASE_2
    if (slot.guardPhase === 'PHASE_2') {
      return this.evaluatePhase2(slot, currentROE, peakRoe, now)
    }

    // 3. PHASE_1 (Breathe)
    // 3a. Retrace check
    if (currentROE < -this.config.phase1RetracePct) {
      return {
        action: 'EXIT',
        reason: 'phase1_retrace',
        slPrice: this.getSLPrice(slot),
      }
    }

    // 3b. Stagnation check — no tier hit and exceeded max duration
    if (elapsed > this.config.phase1MaxDurationMs && slot.tierLevel < 0) {
      return {
        action: 'EXIT',
        reason: 'phase1_stagnation',
        slPrice: this.getSLPrice(slot),
      }
    }

    // 3c. Weak peak check
    if (
      peakRoe < this.config.phase1WeakPeakRoe &&
      elapsed > this.config.phase1WeakPeakDurationMs
    ) {
      return {
        action: 'EXIT',
        reason: 'phase1_weak_peak',
        slPrice: this.getSLPrice(slot),
      }
    }

    // 3d. Graduate to PHASE_2 if ROE >= first tier
    const firstTier = this.config.tiers[0]
    if (firstTier && currentROE >= firstTier.roePct) {
      this.lastTierChangeTime = now
      return {
        action: 'HOLD',
        reason: 'graduated_phase2',
        newTierLevel: 0,
      }
    }

    // 5. Otherwise HOLD
    return {
      action: 'HOLD',
      reason: 'phase1_ok',
    }
  }

  private evaluatePhase2(
    slot: ApexSlot,
    currentROE: number,
    peakRoe: number,
    now: number,
  ): GuardResult {
    // 4a. Find highest tier where currentROE >= tier.roePct
    let newTierLevel = slot.tierLevel
    for (let i = 0; i < this.config.tiers.length; i++) {
      const tier = this.config.tiers[i]!
      if (currentROE >= tier.roePct) {
        if (i > newTierLevel) {
          newTierLevel = i
          this.lastTierChangeTime = now
        }
      }
    }

    // 4b. Get current tier's floorPct
    const currentTier = this.config.tiers[newTierLevel]
    if (!currentTier) {
      return { action: 'HOLD', reason: 'phase2_ok', newTierLevel }
    }
    const floorPct = currentTier.floorPct

    // 4c. Floor breach check
    if (currentROE < floorPct) {
      return {
        action: 'EXIT',
        reason: 'tier_floor_breach',
        slPrice: this.getSLPrice(slot),
      }
    }

    // 4d. Stagnation TP check
    if (this.config.stagnationTp && currentROE >= this.config.stagnationRoe) {
      // Time since last tier change
      const lastChange = this.lastTierChangeTime ?? slot.entryTime
      const stagnationElapsed = now - lastChange
      if (stagnationElapsed > this.config.stagnationDurationMs) {
        return {
          action: 'EXIT',
          reason: 'stagnation_tp',
          newTierLevel,
        }
      }
    }

    // 5. HOLD
    return {
      action: 'HOLD',
      reason: 'phase2_ok',
      newTierLevel,
    }
  }

  getSLPrice(slot: ApexSlot): number {
    const leverage = this.config.leverage ?? DEFAULT_LEVERAGE
    const entryPrice = slot.entryPrice

    if (slot.guardPhase === 'PHASE_1' || slot.tierLevel < 0) {
      // Phase 1 SL based on retrace pct
      const retraceFraction = this.config.phase1RetracePct / 100 / leverage
      if (slot.side === 'LONG') {
        return entryPrice * (1 - retraceFraction)
      } else {
        return entryPrice * (1 + retraceFraction)
      }
    }

    // Phase 2 SL based on current tier floor
    const tier = this.config.tiers[slot.tierLevel]
    if (!tier) {
      // Fallback to phase 1 calculation
      const retraceFraction = this.config.phase1RetracePct / 100 / leverage
      if (slot.side === 'LONG') {
        return entryPrice * (1 - retraceFraction)
      } else {
        return entryPrice * (1 + retraceFraction)
      }
    }

    const floorFraction = tier.floorPct / 100 / leverage
    if (slot.side === 'LONG') {
      return entryPrice * (1 + floorFraction)
    } else {
      return entryPrice * (1 - floorFraction)
    }
  }
}
