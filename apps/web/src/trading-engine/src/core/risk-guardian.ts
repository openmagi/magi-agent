import type { RiskGuardianState, RiskGate } from '../types.js'

const COOLDOWN_DURATION_MS = 30 * 60 * 1000 // 30 minutes

function todayUTC(): string {
  return new Date().toISOString().slice(0, 10)
}

function dateStringFromMs(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

export class RiskGuardian {
  private state: RiskGuardianState

  constructor(state?: RiskGuardianState) {
    this.state = state
      ? { ...state }
      : {
          gate: 'OPEN',
          consecutiveLosses: 0,
          dailyPnl: 0,
          dailyLossLimit: 500,
          cooldownExpiresAt: null,
          lastResetDate: todayUTC(),
        }
  }

  canEnter(): boolean {
    return this.state.gate === 'OPEN'
  }

  canExit(): boolean {
    return this.state.gate !== 'CLOSED'
  }

  recordTrade(pnl: number, now?: number): void {
    const timestamp = now ?? Date.now()
    this.state.dailyPnl += pnl

    if (pnl < 0) {
      this.state.consecutiveLosses++
    } else {
      this.state.consecutiveLosses = 0
    }

    // Check transitions in priority order:
    // 1. Daily loss limit hit → CLOSED (highest priority)
    if (this.state.dailyPnl <= -this.state.dailyLossLimit) {
      this.state.gate = 'CLOSED'
      this.state.cooldownExpiresAt = null
      return
    }

    // 2. Consecutive losses >= 2 → COOLDOWN
    if (this.state.consecutiveLosses >= 2) {
      this.transitionToCooldown(timestamp)
      return
    }

    // 3. Drawdown >= 50% of daily limit AND currently OPEN → COOLDOWN
    if (
      -this.state.dailyPnl >= this.state.dailyLossLimit * 0.5 &&
      this.state.gate === 'OPEN'
    ) {
      this.transitionToCooldown(timestamp)
      return
    }
  }

  tick(now: number): void {
    // Check daily reset: if lastResetDate !== today (UTC)
    const currentDate = dateStringFromMs(now)
    if (this.state.lastResetDate !== currentDate) {
      this.state.gate = 'OPEN'
      this.state.consecutiveLosses = 0
      this.state.dailyPnl = 0
      this.state.cooldownExpiresAt = null
      this.state.lastResetDate = currentDate
      return
    }

    // If COOLDOWN and now >= cooldownExpiresAt → OPEN
    if (
      this.state.gate === 'COOLDOWN' &&
      this.state.cooldownExpiresAt !== null &&
      now >= this.state.cooldownExpiresAt
    ) {
      this.state.gate = 'OPEN'
      this.state.cooldownExpiresAt = null
    }
  }

  getState(): RiskGuardianState {
    return { ...this.state }
  }

  private transitionToCooldown(now: number): void {
    this.state.gate = 'COOLDOWN'
    this.state.cooldownExpiresAt = now + COOLDOWN_DURATION_MS
  }
}
