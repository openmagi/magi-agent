import { jest, describe, it, expect, beforeEach, afterEach } from '@jest/globals'
import { Daemon } from './daemon.js'
import type { DaemonOptions } from './daemon.js'
import type { ApexState, RiskGuardianState } from './types.js'

// --- Injectable dependencies ---

interface MockFs {
  files: Map<string, string>
  dirs: Set<string>
}

function createMockFs(): MockFs {
  return {
    files: new Map(),
    dirs: new Set(),
  }
}

function createFsOps(mockFs: MockFs): DaemonOptions['fsOps'] {
  return {
    readFile(path: string): string | null {
      return mockFs.files.get(path) ?? null
    },
    writeFile(path: string, content: string): void {
      mockFs.files.set(path, content)
    },
    deleteFile(path: string): void {
      mockFs.files.delete(path)
    },
    existsFile(path: string): boolean {
      return mockFs.files.has(path)
    },
    mkdirp(path: string): void {
      mockFs.dirs.add(path)
    },
  }
}

function createMockState(overrides: Partial<ApexState> = {}): ApexState {
  const riskGuardian: RiskGuardianState = {
    gate: 'OPEN',
    consecutiveLosses: 0,
    dailyPnl: 0,
    dailyLossLimit: 500,
    cooldownExpiresAt: null,
    lastResetDate: '2026-03-14',
  }

  return {
    slots: [],
    tickNumber: 0,
    startedAt: Date.now(),
    lastRadarScan: 0,
    lastReflect: 0,
    riskGuardian,
    ...overrides,
  }
}

function mockTickFn(): jest.MockedFunction<() => Promise<void>> {
  return jest.fn<() => Promise<void>>().mockResolvedValue(undefined)
}

describe('Daemon', () => {
  let mockFs: MockFs
  let fsOps: DaemonOptions['fsOps']
  const configDir = '/fake/config'
  const dataDir = '/fake/data/trading'

  beforeEach(() => {
    mockFs = createMockFs()
    fsOps = createFsOps(mockFs)
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  describe('start', () => {
    it('should write PID file on start', async () => {
      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 12345,
      })

      await daemon.start()

      const pidContent = mockFs.files.get('/fake/data/trading/engine.pid')
      expect(pidContent).toBe('12345')

      await daemon.stop()
    })

    it('should call tickFn on each interval tick', async () => {
      jest.useFakeTimers()
      const tickFn = mockTickFn()

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn,
        getPid: () => 1,
        tickIntervalMs: 100,
      })

      await daemon.start()

      // Tick function is not called immediately
      expect(tickFn).not.toHaveBeenCalled()

      // Advance timer
      jest.advanceTimersByTime(100)
      await Promise.resolve() // flush microtasks

      expect(tickFn).toHaveBeenCalledTimes(1)

      jest.advanceTimersByTime(100)
      await Promise.resolve()

      expect(tickFn).toHaveBeenCalledTimes(2)

      await daemon.stop()
    })

    it('should not start if already running', async () => {
      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 1,
      })

      await daemon.start()
      await expect(daemon.start()).rejects.toThrow('already running')

      await daemon.stop()
    })

    it('should create data directory on start', async () => {
      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 1,
      })

      await daemon.start()
      expect(mockFs.dirs.has(dataDir)).toBe(true)

      await daemon.stop()
    })
  })

  describe('stop', () => {
    it('should clean PID file on stop', async () => {
      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 99,
      })

      await daemon.start()
      expect(mockFs.files.has('/fake/data/trading/engine.pid')).toBe(true)

      await daemon.stop()
      expect(mockFs.files.has('/fake/data/trading/engine.pid')).toBe(false)
    })

    it('should stop the tick interval', async () => {
      jest.useFakeTimers()
      const tickFn = mockTickFn()

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn,
        getPid: () => 1,
        tickIntervalMs: 100,
      })

      await daemon.start()
      await daemon.stop()

      jest.advanceTimersByTime(500)
      await Promise.resolve()

      expect(tickFn).not.toHaveBeenCalled()
    })

    it('should throw if not running', async () => {
      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 1,
      })

      await expect(daemon.stop()).rejects.toThrow('not running')
    })
  })

  describe('isRunning', () => {
    it('should return true when started', async () => {
      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 1,
      })

      expect(daemon.isRunning()).toBe(false)
      await daemon.start()
      expect(daemon.isRunning()).toBe(true)

      await daemon.stop()
      expect(daemon.isRunning()).toBe(false)
    })

    it('should detect running from PID file when checkPidAlive returns true', () => {
      // Simulate an externally-running daemon by writing a PID file
      mockFs.files.set('/fake/data/trading/engine.pid', '9999')

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 1,
        checkPidAlive: (_pid: number) => true,
      })

      expect(daemon.isRunningExternal()).toBe(true)
    })

    it('should return false when PID file exists but process is dead', () => {
      mockFs.files.set('/fake/data/trading/engine.pid', '9999')

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 1,
        checkPidAlive: (_pid: number) => false,
      })

      expect(daemon.isRunningExternal()).toBe(false)
    })
  })

  describe('getStatus', () => {
    it('should return stopped status when not running', () => {
      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 1,
      })

      const status = daemon.getStatus()
      expect(status.running).toBe(false)
      expect(status.pid).toBeNull()
      expect(status.uptimeMs).toBe(0)
      expect(status.tickCount).toBe(0)
    })

    it('should return running status with uptime and tick count', async () => {
      jest.useFakeTimers()
      const tickFn = mockTickFn()

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn,
        getPid: () => 42,
        tickIntervalMs: 100,
      })

      await daemon.start()
      jest.advanceTimersByTime(300)

      // Flush tick promises
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()

      const status = daemon.getStatus()
      expect(status.running).toBe(true)
      expect(status.pid).toBe(42)
      expect(status.uptimeMs).toBeGreaterThanOrEqual(300)
      expect(status.tickCount).toBe(3)

      await daemon.stop()
    })

    it('should read tick count from state file when not running locally', () => {
      const state = createMockState({ tickNumber: 150 })
      mockFs.files.set('/fake/data/trading/state.json', JSON.stringify(state))

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn: mockTickFn(),
        getPid: () => 1,
      })

      const status = daemon.getStatus()
      expect(status.tickCount).toBe(150)
    })
  })

  describe('auto-restart', () => {
    it('should restart up to MAX_RESTARTS times on tick error', async () => {
      jest.useFakeTimers()
      let callCount = 0
      const tickFn = jest.fn<() => Promise<void>>().mockImplementation(() => {
        callCount++
        if (callCount <= 3) {
          return Promise.reject(new Error('tick crash'))
        }
        return Promise.resolve()
      })

      const onRestart = jest.fn<() => void>()

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn,
        getPid: () => 1,
        tickIntervalMs: 100,
        maxRestarts: 3,
        restartDelayMs: 50,
        onRestart,
      })

      await daemon.start()

      // First tick fails
      jest.advanceTimersByTime(100)
      await Promise.resolve()
      await Promise.resolve()

      expect(onRestart).toHaveBeenCalledTimes(1)

      // After restart delay, tick fires again and fails again
      jest.advanceTimersByTime(50)
      await Promise.resolve()

      jest.advanceTimersByTime(100)
      await Promise.resolve()
      await Promise.resolve()

      expect(onRestart).toHaveBeenCalledTimes(2)

      await daemon.stop()
    })

    it('should stop after exceeding max restarts', async () => {
      jest.useFakeTimers()
      const tickFn = jest.fn<() => Promise<void>>().mockRejectedValue(new Error('persistent crash'))
      const onFatalError = jest.fn<(error: Error) => void>()

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn,
        getPid: () => 1,
        tickIntervalMs: 100,
        maxRestarts: 2,
        restartDelayMs: 10,
        onFatalError,
      })

      await daemon.start()

      // First tick fails -> restart 1
      jest.advanceTimersByTime(100)
      await Promise.resolve()
      await Promise.resolve()

      // restart delay + tick -> restart 2
      jest.advanceTimersByTime(10)
      await Promise.resolve()
      jest.advanceTimersByTime(100)
      await Promise.resolve()
      await Promise.resolve()

      // restart delay + tick -> exceeds max (3rd failure)
      jest.advanceTimersByTime(10)
      await Promise.resolve()
      jest.advanceTimersByTime(100)
      await Promise.resolve()
      await Promise.resolve()

      expect(onFatalError).toHaveBeenCalled()
      expect(daemon.isRunning()).toBe(false)
    })
  })

  describe('log capture', () => {
    it('should capture tick logs', async () => {
      jest.useFakeTimers()
      const tickFn = mockTickFn()

      const daemon = new Daemon(configDir, dataDir, {
        fsOps,
        tickFn,
        getPid: () => 1,
        tickIntervalMs: 100,
      })

      await daemon.start()

      jest.advanceTimersByTime(100)
      await Promise.resolve()

      const logs = daemon.getLogs()
      expect(logs.length).toBeGreaterThan(0)
      expect(logs.some((l: string) => l.includes('tick'))).toBe(true)

      await daemon.stop()
    })
  })
})
