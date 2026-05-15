import { RiskGuardian } from './risk-guardian.js'
import type { RiskGuardianState } from '../types.js'

function utcDateString(date: Date): string {
  return date.toISOString().slice(0, 10)
}

describe('RiskGuardian', () => {
  let guardian: RiskGuardian

  beforeEach(() => {
    guardian = new RiskGuardian()
  })

  it('should start in OPEN state', () => {
    const state = guardian.getState()
    expect(state.gate).toBe('OPEN')
    expect(state.consecutiveLosses).toBe(0)
    expect(state.dailyPnl).toBe(0)
    expect(state.dailyLossLimit).toBe(500)
    expect(state.cooldownExpiresAt).toBeNull()
    expect(state.lastResetDate).toBe(utcDateString(new Date()))
  })

  it('should allow entries in OPEN state', () => {
    expect(guardian.canEnter()).toBe(true)
    expect(guardian.canExit()).toBe(true)
  })

  it('should transition to COOLDOWN on 2 consecutive losses', () => {
    guardian.recordTrade(-10)
    expect(guardian.getState().gate).toBe('OPEN')

    guardian.recordTrade(-10)
    expect(guardian.getState().gate).toBe('COOLDOWN')
    expect(guardian.getState().consecutiveLosses).toBe(2)
    expect(guardian.getState().cooldownExpiresAt).not.toBeNull()
  })

  it('should block entries in COOLDOWN', () => {
    guardian.recordTrade(-10)
    guardian.recordTrade(-10)
    expect(guardian.getState().gate).toBe('COOLDOWN')
    expect(guardian.canEnter()).toBe(false)
  })

  it('should allow exits in COOLDOWN', () => {
    guardian.recordTrade(-10)
    guardian.recordTrade(-10)
    expect(guardian.getState().gate).toBe('COOLDOWN')
    expect(guardian.canExit()).toBe(true)
  })

  it('should auto-expire COOLDOWN after 30 min', () => {
    guardian.recordTrade(-10)
    guardian.recordTrade(-10)
    expect(guardian.getState().gate).toBe('COOLDOWN')

    const thirtyMinMs = 30 * 60 * 1000
    const future = Date.now() + thirtyMinMs + 1
    guardian.tick(future)

    expect(guardian.getState().gate).toBe('OPEN')
    expect(guardian.getState().cooldownExpiresAt).toBeNull()
  })

  it('should transition to CLOSED on daily loss limit', () => {
    guardian.recordTrade(-500)
    expect(guardian.getState().gate).toBe('CLOSED')
    expect(guardian.getState().dailyPnl).toBe(-500)
  })

  it('should block entries AND exits in CLOSED', () => {
    guardian.recordTrade(-500)
    expect(guardian.getState().gate).toBe('CLOSED')
    expect(guardian.canEnter()).toBe(false)
    expect(guardian.canExit()).toBe(false)
  })

  it('should reset to OPEN on daily reset (new UTC date)', () => {
    guardian.recordTrade(-500)
    expect(guardian.getState().gate).toBe('CLOSED')

    // Simulate next day
    const tomorrow = new Date()
    tomorrow.setUTCDate(tomorrow.getUTCDate() + 1)
    guardian.tick(tomorrow.getTime())

    const state = guardian.getState()
    expect(state.gate).toBe('OPEN')
    expect(state.dailyPnl).toBe(0)
    expect(state.consecutiveLosses).toBe(0)
    expect(state.cooldownExpiresAt).toBeNull()
    expect(state.lastResetDate).toBe(utcDateString(tomorrow))
  })

  it('should transition COOLDOWN -> CLOSED on daily loss limit hit', () => {
    // First get into COOLDOWN
    guardian.recordTrade(-10)
    guardian.recordTrade(-10)
    expect(guardian.getState().gate).toBe('COOLDOWN')

    // Now record a trade that hits the daily loss limit
    guardian.recordTrade(-480)
    expect(guardian.getState().gate).toBe('CLOSED')
    expect(guardian.getState().dailyPnl).toBe(-500)
  })

  it('should reset consecutive losses on a win', () => {
    guardian.recordTrade(-10)
    expect(guardian.getState().consecutiveLosses).toBe(1)

    guardian.recordTrade(20)
    expect(guardian.getState().consecutiveLosses).toBe(0)
    expect(guardian.getState().gate).toBe('OPEN')
  })

  it('should transition to COOLDOWN on drawdown >= 50% of daily limit', () => {
    // dailyLossLimit is 500, so 50% = 250
    guardian.recordTrade(-250)
    expect(guardian.getState().gate).toBe('COOLDOWN')
    expect(guardian.getState().dailyPnl).toBe(-250)
  })
})
