import {
  APEX_PRESETS,
  GUARD_PRESETS,
} from './types.js'
import type {
  StrategyDecision,
  ApexSlot,
  RiskGuardianState,
} from './types.js'

describe('Core Types', () => {
  describe('StrategyDecision', () => {
    it('should create a valid StrategyDecision with confidence 0-100', () => {
      const decision: StrategyDecision = {
        action: 'BUY',
        symbol: 'BTC-PERP',
        size: 0.1,
        orderType: 'GTC',
        confidence: 85,
        reason: 'Strong uptrend with volume confirmation',
        stopLoss: 60000,
        takeProfit: 70000,
      }

      expect(decision.action).toBe('BUY')
      expect(decision.symbol).toBe('BTC-PERP')
      expect(decision.size).toBe(0.1)
      expect(decision.orderType).toBe('GTC')
      expect(decision.confidence).toBeGreaterThanOrEqual(0)
      expect(decision.confidence).toBeLessThanOrEqual(100)
      expect(decision.reason).toBe('Strong uptrend with volume confirmation')
      expect(decision.stopLoss).toBe(60000)
      expect(decision.takeProfit).toBe(70000)
    })
  })

  describe('ApexSlot', () => {
    it('should create a valid ApexSlot', () => {
      const slot: ApexSlot = {
        id: 1,
        status: 'OPEN',
        symbol: 'ETH-PERP',
        side: 'LONG',
        entryPrice: 3500,
        size: 1.5,
        entryTime: Date.now(),
        guardPhase: 'PHASE_1',
        peakRoe: 12.5,
        currentRoe: 8.3,
        tierLevel: 1,
        exchangeSlOrderId: 'sl-123',
      }

      expect(slot.id).toBe(1)
      expect(slot.status).toBe('OPEN')
      expect(slot.symbol).toBe('ETH-PERP')
      expect(slot.side).toBe('LONG')
      expect(slot.entryPrice).toBe(3500)
      expect(slot.size).toBe(1.5)
      expect(slot.guardPhase).toBe('PHASE_1')
      expect(slot.peakRoe).toBe(12.5)
      expect(slot.currentRoe).toBe(8.3)
      expect(slot.tierLevel).toBe(1)
      expect(slot.exchangeSlOrderId).toBe('sl-123')
    })
  })

  describe('RiskGuardianState', () => {
    it('should create a valid RiskGuardianState', () => {
      const state: RiskGuardianState = {
        gate: 'OPEN',
        consecutiveLosses: 0,
        dailyPnl: 150.5,
        dailyLossLimit: 500,
        cooldownExpiresAt: null,
        lastResetDate: '2026-03-14',
      }

      expect(state.gate).toBe('OPEN')
      expect(state.consecutiveLosses).toBe(0)
      expect(state.dailyPnl).toBe(150.5)
      expect(state.dailyLossLimit).toBe(500)
      expect(state.cooldownExpiresAt).toBeNull()
      expect(state.lastResetDate).toBe('2026-03-14')
    })
  })

  describe('APEX_PRESETS', () => {
    it('should have correct values for conservative preset', () => {
      const preset = APEX_PRESETS['conservative']
      expect(preset).toBeDefined()
      expect(preset!.maxSlots).toBe(2)
      expect(preset!.leverage).toBe(5)
      expect(preset!.radarThreshold).toBe(190)
      expect(preset!.dailyLossLimit).toBe(250)
    })

    it('should have correct values for default preset', () => {
      const preset = APEX_PRESETS['default']
      expect(preset).toBeDefined()
      expect(preset!.maxSlots).toBe(3)
      expect(preset!.leverage).toBe(10)
      expect(preset!.radarThreshold).toBe(170)
      expect(preset!.dailyLossLimit).toBe(500)
    })

    it('should have correct values for aggressive preset', () => {
      const preset = APEX_PRESETS['aggressive']
      expect(preset).toBeDefined()
      expect(preset!.maxSlots).toBe(3)
      expect(preset!.leverage).toBe(15)
      expect(preset!.radarThreshold).toBe(150)
      expect(preset!.dailyLossLimit).toBe(1000)
    })
  })

  describe('GUARD_PRESETS', () => {
    it('should have 6 tiers for moderate preset', () => {
      const moderate = GUARD_PRESETS['moderate']
      expect(moderate).toBeDefined()
      expect(moderate.tiers).toHaveLength(6)
      expect(moderate.preset).toBe('moderate')
      expect(moderate.stagnationTp).toBe(false)
    })

    it('should have 4 tiers for tight preset', () => {
      const tight = GUARD_PRESETS['tight']
      expect(tight).toBeDefined()
      expect(tight.tiers).toHaveLength(4)
      expect(tight.preset).toBe('tight')
      expect(tight.stagnationTp).toBe(true)
      expect(tight.stagnationRoe).toBe(8)
    })
  })
})
