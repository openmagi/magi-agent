import { jest, describe, it, expect } from '@jest/globals'
import { parseCommand, runCommand } from './cli.js'
import type { CommandDaemon, CommandHeartbeat, RunCommandDeps } from './cli.js'
import type { DaemonStatus } from './daemon.js'
import type { HeartbeatResult } from './heartbeat.js'

function createMockDaemon(overrides: Partial<CommandDaemon> = {}): CommandDaemon {
  return {
    start: jest.fn<() => Promise<void>>().mockResolvedValue(undefined),
    stop: jest.fn<() => Promise<void>>().mockResolvedValue(undefined),
    getStatus: jest.fn<() => DaemonStatus>().mockReturnValue({
      running: false,
      pid: null,
      uptimeMs: 0,
      tickCount: 0,
    }),
    isRunning: jest.fn<() => boolean>().mockReturnValue(false),
    ...overrides,
  }
}

function createMockHeartbeat(overrides: Partial<CommandHeartbeat> = {}): CommandHeartbeat {
  return {
    run: jest.fn<() => Promise<HeartbeatResult>>().mockResolvedValue({
      alive: true,
      restarted: false,
      scratchpadUpdate: '',
      alert: null,
    }),
    ...overrides,
  }
}

function createDeps(
  daemonOverrides: Partial<CommandDaemon> = {},
  heartbeatOverrides: Partial<CommandHeartbeat> = {},
): { deps: RunCommandDeps; output: string[] } {
  const output: string[] = []
  return {
    deps: {
      daemon: createMockDaemon(daemonOverrides),
      heartbeat: createMockHeartbeat(heartbeatOverrides),
      log: (msg: string) => output.push(msg),
    },
    output,
  }
}

describe('CLI', () => {
  describe('parseCommand', () => {
    it('should parse start command', () => {
      const result = parseCommand(['node', 'engine', 'start'])
      expect(result.command).toBe('start')
    })

    it('should parse stop command', () => {
      const result = parseCommand(['node', 'engine', 'stop'])
      expect(result.command).toBe('stop')
    })

    it('should parse status command', () => {
      const result = parseCommand(['node', 'engine', 'status'])
      expect(result.command).toBe('status')
    })

    it('should parse heartbeat command', () => {
      const result = parseCommand(['node', 'engine', 'heartbeat'])
      expect(result.command).toBe('heartbeat')
    })

    it('should parse close-all command', () => {
      const result = parseCommand(['node', 'engine', 'close-all'])
      expect(result.command).toBe('close-all')
    })

    it('should parse reflect command', () => {
      const result = parseCommand(['node', 'engine', 'reflect'])
      expect(result.command).toBe('reflect')
    })

    it('should return empty string for no command', () => {
      const result = parseCommand(['node', 'engine'])
      expect(result.command).toBe('')
    })

    it('should parse --config option', () => {
      const result = parseCommand(['node', 'engine', 'start', '--config', '/path/to/config'])
      expect(result.command).toBe('start')
      expect(result.options['config']).toBe('/path/to/config')
    })

    it('should parse --data option', () => {
      const result = parseCommand(['node', 'engine', 'start', '--data', '/path/to/data'])
      expect(result.command).toBe('start')
      expect(result.options['data']).toBe('/path/to/data')
    })

    it('should parse multiple options', () => {
      const result = parseCommand([
        'node', 'engine', 'start',
        '--config', '/config',
        '--data', '/data',
      ])
      expect(result.command).toBe('start')
      expect(result.options['config']).toBe('/config')
      expect(result.options['data']).toBe('/data')
    })
  })

  describe('runCommand', () => {
    it('should call daemon.start() for start command', async () => {
      const { deps, output } = createDeps()

      await runCommand('start', {}, deps)

      expect(deps.daemon.start).toHaveBeenCalledTimes(1)
      expect(output.some(o => o.includes('start'))).toBe(true)
    })

    it('should call daemon.stop() for stop command', async () => {
      const { deps, output } = createDeps()

      await runCommand('stop', {}, deps)

      expect(deps.daemon.stop).toHaveBeenCalledTimes(1)
      expect(output.some(o => o.includes('stop'))).toBe(true)
    })

    it('should call daemon.getStatus() for status command', async () => {
      const status: DaemonStatus = {
        running: true,
        pid: 42,
        uptimeMs: 120000,
        tickCount: 50,
      }
      const { deps, output } = createDeps({
        getStatus: jest.fn<() => DaemonStatus>().mockReturnValue(status),
      })

      await runCommand('status', {}, deps)

      expect(deps.daemon.getStatus).toHaveBeenCalledTimes(1)
      expect(output.some(o => o.includes('Running'))).toBe(true)
      expect(output.some(o => o.includes('42'))).toBe(true)
    })

    it('should display stopped for status command when not running', async () => {
      const status: DaemonStatus = {
        running: false,
        pid: null,
        uptimeMs: 0,
        tickCount: 0,
      }
      const { deps, output } = createDeps({
        getStatus: jest.fn<() => DaemonStatus>().mockReturnValue(status),
      })

      await runCommand('status', {}, deps)

      expect(output.some(o => o.toLowerCase().includes('stopped') || o.toLowerCase().includes('not running'))).toBe(true)
    })

    it('should call heartbeat.run() for heartbeat command', async () => {
      const heartbeatResult: HeartbeatResult = {
        alive: true,
        restarted: false,
        scratchpadUpdate: '## Positions\n| Slot | Symbol |\n|---|---|\n| 0 | BTC-PERP |',
        alert: null,
      }
      const { deps, output } = createDeps({}, {
        run: jest.fn<() => Promise<HeartbeatResult>>().mockResolvedValue(heartbeatResult),
      })

      await runCommand('heartbeat', {}, deps)

      expect(deps.heartbeat.run).toHaveBeenCalledTimes(1)
      expect(output.some(o => o.includes('Positions') || o.includes('BTC-PERP'))).toBe(true)
    })

    it('should display alert from heartbeat when present', async () => {
      const heartbeatResult: HeartbeatResult = {
        alive: true,
        restarted: false,
        scratchpadUpdate: '## No positions',
        alert: 'Risk gate CLOSED - daily loss limit reached',
      }
      const { deps, output } = createDeps({}, {
        run: jest.fn<() => Promise<HeartbeatResult>>().mockResolvedValue(heartbeatResult),
      })

      await runCommand('heartbeat', {}, deps)

      expect(output.some(o => o.includes('CLOSED'))).toBe(true)
    })

    it('should print usage for unknown command', async () => {
      const { deps, output } = createDeps()

      await runCommand('unknown', {}, deps)

      expect(output.some(o => o.includes('Usage'))).toBe(true)
    })

    it('should print usage for empty command', async () => {
      const { deps, output } = createDeps()

      await runCommand('', {}, deps)

      expect(output.some(o => o.includes('Usage'))).toBe(true)
    })

    it('should handle start error gracefully', async () => {
      const { deps, output } = createDeps({
        start: jest.fn<() => Promise<void>>().mockRejectedValue(new Error('already running')),
      })

      await runCommand('start', {}, deps)

      expect(output.some(o => o.includes('Error') || o.includes('error'))).toBe(true)
    })

    it('should handle stop error gracefully', async () => {
      const { deps, output } = createDeps({
        stop: jest.fn<() => Promise<void>>().mockRejectedValue(new Error('not running')),
      })

      await runCommand('stop', {}, deps)

      expect(output.some(o => o.includes('Error') || o.includes('error'))).toBe(true)
    })
  })
})
