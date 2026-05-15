import { describe, it, expect, beforeEach } from '@jest/globals'
import { Heartbeat } from './heartbeat.js'
import type { ApexState, ApexSlot, RiskGuardianState } from './types.js'

interface MockFs {
  files: Map<string, string>
}

function createMockFs(): MockFs {
  return { files: new Map() }
}

function createFsOps(mockFs: MockFs) {
  return {
    readFile(path: string): string | null {
      return mockFs.files.get(path) ?? null
    },
    existsFile(path: string): boolean {
      return mockFs.files.has(path)
    },
  }
}

function createSlot(overrides: Partial<ApexSlot> = {}): ApexSlot {
  return {
    id: 0,
    status: 'EMPTY',
    symbol: null,
    side: null,
    entryPrice: 0,
    size: 0,
    entryTime: 0,
    guardPhase: 'PHASE_1',
    peakRoe: 0,
    currentRoe: 0,
    tierLevel: 0,
    ...overrides,
  }
}

function createState(overrides: Partial<ApexState> = {}): ApexState {
  const riskGuardian: RiskGuardianState = {
    gate: 'OPEN',
    consecutiveLosses: 0,
    dailyPnl: 0,
    dailyLossLimit: 500,
    cooldownExpiresAt: null,
    lastResetDate: '2026-03-14',
  }

  return {
    slots: [createSlot({ id: 0 }), createSlot({ id: 1 }), createSlot({ id: 2 })],
    tickNumber: 100,
    startedAt: Date.now() - 3600000,
    lastRadarScan: 90,
    lastReflect: 80,
    riskGuardian,
    ...overrides,
  }
}

describe('Heartbeat', () => {
  let mockFs: MockFs
  let fsOps: ReturnType<typeof createFsOps>
  const dataDir = '/fake/data/trading'

  beforeEach(() => {
    mockFs = createMockFs()
    fsOps = createFsOps(mockFs)
  })

  describe('alive check', () => {
    it('should report alive=true when PID file exists and process is alive', async () => {
      mockFs.files.set(`${dataDir}/engine.pid`, '1234')
      const state = createState()
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => true,
      })

      const result = await heartbeat.run()
      expect(result.alive).toBe(true)
      expect(result.restarted).toBe(false)
    })

    it('should report alive=false when no PID file exists', async () => {
      const state = createState()
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => false,
      })

      const result = await heartbeat.run()
      expect(result.alive).toBe(false)
      expect(result.restarted).toBe(true)
    })

    it('should report alive=false when PID file exists but process is dead', async () => {
      mockFs.files.set(`${dataDir}/engine.pid`, '9999')
      const state = createState()
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => false,
      })

      const result = await heartbeat.run()
      expect(result.alive).toBe(false)
      expect(result.restarted).toBe(true)
    })
  })

  describe('scratchpad update', () => {
    it('should generate markdown table with position details', async () => {
      const state = createState({
        slots: [
          createSlot({
            id: 0,
            status: 'OPEN',
            symbol: 'BTC-PERP',
            side: 'LONG',
            entryPrice: 50000,
            size: 0.1,
            currentRoe: 12.5,
            peakRoe: 15.0,
            guardPhase: 'PHASE_2',
            tierLevel: 1,
          }),
          createSlot({
            id: 1,
            status: 'OPEN',
            symbol: 'ETH-PERP',
            side: 'SHORT',
            entryPrice: 3000,
            size: 1.0,
            currentRoe: -2.5,
            peakRoe: 5.0,
            guardPhase: 'PHASE_1',
            tierLevel: 0,
          }),
          createSlot({ id: 2 }),
        ],
        tickNumber: 150,
        riskGuardian: {
          gate: 'OPEN',
          consecutiveLosses: 1,
          dailyPnl: 75.5,
          dailyLossLimit: 500,
          cooldownExpiresAt: null,
          lastResetDate: '2026-03-14',
        },
      })

      mockFs.files.set(`${dataDir}/engine.pid`, '1234')
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => true,
      })

      const result = await heartbeat.run()

      // Should contain position table headers
      expect(result.scratchpadUpdate).toContain('Slot')
      expect(result.scratchpadUpdate).toContain('Symbol')
      expect(result.scratchpadUpdate).toContain('Side')
      expect(result.scratchpadUpdate).toContain('ROE')

      // Should contain position data
      expect(result.scratchpadUpdate).toContain('BTC-PERP')
      expect(result.scratchpadUpdate).toContain('LONG')
      expect(result.scratchpadUpdate).toContain('ETH-PERP')
      expect(result.scratchpadUpdate).toContain('SHORT')

      // Should contain PnL summary
      expect(result.scratchpadUpdate).toContain('Daily PnL')
      expect(result.scratchpadUpdate).toContain('75.5')

      // Should contain tick count
      expect(result.scratchpadUpdate).toContain('150')
    })

    it('should show empty positions as EMPTY', async () => {
      const state = createState()
      mockFs.files.set(`${dataDir}/engine.pid`, '1234')
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => true,
      })

      const result = await heartbeat.run()
      expect(result.scratchpadUpdate).toContain('EMPTY')
    })

    it('should produce valid scratchpad even without state file', async () => {
      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => false,
      })

      const result = await heartbeat.run()
      expect(result.scratchpadUpdate).toContain('No state')
    })
  })

  describe('anomaly detection', () => {
    it('should alert when risk gate is COOLDOWN', async () => {
      const state = createState({
        riskGuardian: {
          gate: 'COOLDOWN',
          consecutiveLosses: 3,
          dailyPnl: -200,
          dailyLossLimit: 500,
          cooldownExpiresAt: Date.now() + 60000,
          lastResetDate: '2026-03-14',
        },
      })

      mockFs.files.set(`${dataDir}/engine.pid`, '1234')
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => true,
      })

      const result = await heartbeat.run()
      expect(result.alert).not.toBeNull()
      expect(result.alert).toContain('COOLDOWN')
    })

    it('should alert when risk gate is CLOSED', async () => {
      const state = createState({
        riskGuardian: {
          gate: 'CLOSED',
          consecutiveLosses: 5,
          dailyPnl: -500,
          dailyLossLimit: 500,
          cooldownExpiresAt: null,
          lastResetDate: '2026-03-14',
        },
      })

      mockFs.files.set(`${dataDir}/engine.pid`, '1234')
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => true,
      })

      const result = await heartbeat.run()
      expect(result.alert).not.toBeNull()
      expect(result.alert).toContain('CLOSED')
    })

    it('should alert when daily loss exceeds 80% of limit', async () => {
      const state = createState({
        riskGuardian: {
          gate: 'OPEN',
          consecutiveLosses: 2,
          dailyPnl: -420,
          dailyLossLimit: 500,
          cooldownExpiresAt: null,
          lastResetDate: '2026-03-14',
        },
      })

      mockFs.files.set(`${dataDir}/engine.pid`, '1234')
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => true,
      })

      const result = await heartbeat.run()
      expect(result.alert).not.toBeNull()
      expect(result.alert).toContain('loss')
    })

    it('should return null alert when everything is normal', async () => {
      const state = createState()
      mockFs.files.set(`${dataDir}/engine.pid`, '1234')
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => true,
      })

      const result = await heartbeat.run()
      expect(result.alert).toBeNull()
    })

    it('should alert when daemon is not alive', async () => {
      const state = createState()
      mockFs.files.set(`${dataDir}/state.json`, JSON.stringify(state))

      const heartbeat = new Heartbeat(dataDir, {
        fsOps,
        checkPidAlive: () => false,
      })

      const result = await heartbeat.run()
      expect(result.alert).not.toBeNull()
      expect(result.alert).toContain('not running')
    })
  })
})
