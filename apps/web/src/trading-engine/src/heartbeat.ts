import { join } from 'node:path'
import type { ApexState, ApexSlot } from './types.js'

export interface HeartbeatResult {
  alive: boolean
  restarted: boolean
  scratchpadUpdate: string
  alert: string | null
}

export interface HeartbeatFsOps {
  readFile(path: string): string | null
  existsFile(path: string): boolean
}

export interface HeartbeatOptions {
  fsOps: HeartbeatFsOps
  checkPidAlive: (pid: number) => boolean
}

const HIGH_LOSS_THRESHOLD = 0.8 // 80% of daily loss limit

export class Heartbeat {
  private readonly dataDir: string
  private readonly fsOps: HeartbeatFsOps
  private readonly checkPidAlive: (pid: number) => boolean

  constructor(dataDir: string, options: HeartbeatOptions) {
    this.dataDir = dataDir
    this.fsOps = options.fsOps
    this.checkPidAlive = options.checkPidAlive
  }

  async run(): Promise<HeartbeatResult> {
    const alive = this.isDaemonAlive()
    const state = this.loadState()
    const scratchpadUpdate = this.formatScratchpad(state, alive)
    const alerts = this.detectAnomalies(state, alive)

    return {
      alive,
      restarted: !alive,
      scratchpadUpdate,
      alert: alerts,
    }
  }

  private isDaemonAlive(): boolean {
    const pidFile = join(this.dataDir, 'engine.pid')
    const pidContent = this.fsOps.readFile(pidFile)
    if (pidContent === null) return false

    const pid = parseInt(pidContent, 10)
    if (isNaN(pid)) return false

    return this.checkPidAlive(pid)
  }

  private loadState(): ApexState | null {
    const stateFile = join(this.dataDir, 'state.json')
    const content = this.fsOps.readFile(stateFile)
    if (content === null) return null

    try {
      return JSON.parse(content) as ApexState
    } catch {
      return null
    }
  }

  private formatScratchpad(state: ApexState | null, alive: boolean): string {
    if (state === null) {
      const statusLine = alive ? 'Engine running' : 'Engine not running'
      return `## Trading Engine Status\n- ${statusLine}\n- No state file found\n`
    }

    const lines: string[] = []

    // Header
    lines.push('## Trading Engine Status')
    lines.push(`- Status: ${alive ? 'RUNNING' : 'STOPPED'}`)
    lines.push(`- Tick: ${state.tickNumber}`)
    lines.push(`- Risk Gate: ${state.riskGuardian.gate}`)
    lines.push(`- Daily PnL: $${state.riskGuardian.dailyPnl.toFixed(2)}`)
    lines.push(`- Daily Loss Limit: $${state.riskGuardian.dailyLossLimit.toFixed(2)}`)
    lines.push('')

    // Positions table
    lines.push('## Positions')
    lines.push('| Slot | Symbol | Side | Entry | ROE% | Peak ROE% | Guard | Tier |')
    lines.push('|------|--------|------|-------|------|-----------|-------|------|')

    for (const slot of state.slots) {
      if (slot.status === 'OPEN' && slot.symbol !== null) {
        lines.push(
          `| ${slot.id} | ${slot.symbol} | ${slot.side ?? '-'} | $${slot.entryPrice.toFixed(2)} | ${slot.currentRoe.toFixed(1)}% | ${slot.peakRoe.toFixed(1)}% | ${slot.guardPhase} | ${slot.tierLevel} |`
        )
      } else {
        lines.push(`| ${slot.id} | EMPTY | - | - | - | - | - | - |`)
      }
    }

    lines.push('')

    // PnL summary
    const openSlots = state.slots.filter((s: ApexSlot) => s.status === 'OPEN')
    lines.push(`## Summary`)
    lines.push(`- Open Positions: ${openSlots.length}/${state.slots.length}`)
    lines.push(`- Consecutive Losses: ${state.riskGuardian.consecutiveLosses}`)
    lines.push('')

    return lines.join('\n')
  }

  private detectAnomalies(state: ApexState | null, alive: boolean): string | null {
    const alerts: string[] = []

    if (!alive) {
      alerts.push('Engine is not running - restart needed')
    }

    if (state !== null) {
      const { riskGuardian } = state

      if (riskGuardian.gate === 'CLOSED') {
        alerts.push(`Risk gate CLOSED - daily loss limit reached (PnL: $${riskGuardian.dailyPnl.toFixed(2)})`)
      } else if (riskGuardian.gate === 'COOLDOWN') {
        alerts.push(`Risk gate in COOLDOWN - consecutive losses: ${riskGuardian.consecutiveLosses}`)
      }

      // Check if daily loss is approaching limit
      if (
        riskGuardian.gate === 'OPEN' &&
        riskGuardian.dailyPnl < 0 &&
        Math.abs(riskGuardian.dailyPnl) >= riskGuardian.dailyLossLimit * HIGH_LOSS_THRESHOLD
      ) {
        const pct = ((Math.abs(riskGuardian.dailyPnl) / riskGuardian.dailyLossLimit) * 100).toFixed(0)
        alerts.push(`High daily loss warning: $${Math.abs(riskGuardian.dailyPnl).toFixed(2)} (${pct}% of limit)`)
      }
    }

    return alerts.length > 0 ? alerts.join('\n') : null
  }
}
