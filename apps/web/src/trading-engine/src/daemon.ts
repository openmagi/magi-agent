import { join } from 'node:path'
import type { ApexState } from './types.js'

export interface DaemonStatus {
  running: boolean
  pid: number | null
  uptimeMs: number
  tickCount: number
}

export interface FsOps {
  readFile(path: string): string | null
  writeFile(path: string, content: string): void
  deleteFile(path: string): void
  existsFile(path: string): boolean
  mkdirp(path: string): void
}

export interface DaemonOptions {
  fsOps: FsOps
  tickFn: () => Promise<void>
  getPid: () => number
  tickIntervalMs?: number
  maxRestarts?: number
  restartDelayMs?: number
  checkPidAlive?: (pid: number) => boolean
  onRestart?: () => void
  onFatalError?: (error: Error) => void
}

const DEFAULT_TICK_INTERVAL_MS = 60000
const DEFAULT_MAX_RESTARTS = 3
const DEFAULT_RESTART_DELAY_MS = 5000

export class Daemon {
  private readonly configDir: string
  private readonly dataDir: string
  private readonly fsOps: FsOps
  private readonly tickFn: () => Promise<void>
  private readonly getPid: () => number
  private readonly tickIntervalMs: number
  private readonly maxRestarts: number
  private readonly restartDelayMs: number
  private readonly checkPidAlive: (pid: number) => boolean
  private readonly onRestart: () => void
  private readonly onFatalError: (error: Error) => void

  private running = false
  private intervalHandle: ReturnType<typeof setInterval> | null = null
  private startedAt: number | null = null
  private tickCount = 0
  private restartCount = 0
  private logs: string[] = []

  constructor(configDir: string, dataDir: string, options: DaemonOptions) {
    this.configDir = configDir
    this.dataDir = dataDir
    this.fsOps = options.fsOps
    this.tickFn = options.tickFn
    this.getPid = options.getPid
    this.tickIntervalMs = options.tickIntervalMs ?? DEFAULT_TICK_INTERVAL_MS
    this.maxRestarts = options.maxRestarts ?? DEFAULT_MAX_RESTARTS
    this.restartDelayMs = options.restartDelayMs ?? DEFAULT_RESTART_DELAY_MS
    this.checkPidAlive = options.checkPidAlive ?? (() => false)
    this.onRestart = options.onRestart ?? (() => {})
    this.onFatalError = options.onFatalError ?? (() => {})
  }

  async start(): Promise<void> {
    if (this.running) {
      throw new Error('Daemon already running')
    }

    this.fsOps.mkdirp(this.dataDir)

    const pid = this.getPid()
    const pidFile = join(this.dataDir, 'engine.pid')
    this.fsOps.writeFile(pidFile, String(pid))

    this.running = true
    this.startedAt = Date.now()
    this.tickCount = 0
    this.restartCount = 0
    this.logs = []

    this.addLog(`Engine started (PID: ${pid}, config: ${this.configDir})`)
    this.startTickLoop()
  }

  async stop(): Promise<void> {
    if (!this.running) {
      throw new Error('Daemon not running')
    }

    this.stopTickLoop()

    const pidFile = join(this.dataDir, 'engine.pid')
    this.fsOps.deleteFile(pidFile)

    this.running = false
    this.startedAt = null
    this.addLog('Engine stopped')
  }

  isRunning(): boolean {
    return this.running
  }

  isRunningExternal(): boolean {
    const pidFile = join(this.dataDir, 'engine.pid')
    const pidContent = this.fsOps.readFile(pidFile)
    if (pidContent === null) return false

    const pid = parseInt(pidContent, 10)
    if (isNaN(pid)) return false

    return this.checkPidAlive(pid)
  }

  getStatus(): DaemonStatus {
    if (this.running) {
      const uptimeMs = this.startedAt !== null ? Date.now() - this.startedAt : 0
      return {
        running: true,
        pid: this.getPid(),
        uptimeMs,
        tickCount: this.tickCount,
      }
    }

    // Read tick count from state file if available
    const stateFile = join(this.dataDir, 'state.json')
    const stateContent = this.fsOps.readFile(stateFile)
    let tickCount = 0
    if (stateContent !== null) {
      try {
        const state = JSON.parse(stateContent) as ApexState
        tickCount = state.tickNumber
      } catch {
        // ignore parse errors
      }
    }

    return {
      running: false,
      pid: null,
      uptimeMs: 0,
      tickCount,
    }
  }

  getLogs(): string[] {
    return [...this.logs]
  }

  private startTickLoop(): void {
    this.intervalHandle = setInterval(() => {
      void this.executeTick()
    }, this.tickIntervalMs)
  }

  private stopTickLoop(): void {
    if (this.intervalHandle !== null) {
      clearInterval(this.intervalHandle)
      this.intervalHandle = null
    }
  }

  private async executeTick(): Promise<void> {
    try {
      await this.tickFn()
      this.tickCount++
      this.restartCount = 0 // Reset restart count on successful tick
      this.addLog(`tick ${this.tickCount} completed`)
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error)
      this.addLog(`tick error: ${errMsg}`)

      this.restartCount++
      if (this.restartCount > this.maxRestarts) {
        this.addLog(`Fatal: exceeded max restarts (${this.maxRestarts})`)
        this.stopTickLoop()

        const pidFile = join(this.dataDir, 'engine.pid')
        this.fsOps.deleteFile(pidFile)

        this.running = false
        this.startedAt = null
        this.onFatalError(error instanceof Error ? error : new Error(errMsg))
        return
      }

      this.addLog(`Restarting tick loop (attempt ${this.restartCount}/${this.maxRestarts})`)
      this.onRestart()

      // Restart: stop current loop, wait, then start new loop
      this.stopTickLoop()
      setTimeout(() => {
        if (this.running) {
          this.startTickLoop()
        }
      }, this.restartDelayMs)
    }
  }

  private addLog(message: string): void {
    const timestamp = new Date().toISOString()
    this.logs.push(`[${timestamp}] ${message}`)

    // Keep last 1000 logs
    if (this.logs.length > 1000) {
      this.logs = this.logs.slice(-1000)
    }
  }
}
