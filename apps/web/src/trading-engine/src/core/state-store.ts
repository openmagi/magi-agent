import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync, appendFileSync } from 'node:fs'
import { join } from 'node:path'
import type { ApexState, ApexSlot, TradeRecord, RiskGuardianState } from '../types.js'

export function createEmptySlot(id: number): ApexSlot {
  return {
    id,
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
  }
}

function createDefaultState(): ApexState {
  const today = new Date().toISOString().slice(0, 10)
  const riskGuardian: RiskGuardianState = {
    gate: 'OPEN',
    consecutiveLosses: 0,
    dailyPnl: 0,
    dailyLossLimit: 500,
    cooldownExpiresAt: null,
    lastResetDate: today,
  }

  return {
    slots: [createEmptySlot(0), createEmptySlot(1), createEmptySlot(2)],
    tickNumber: 0,
    startedAt: Date.now(),
    lastRadarScan: 0,
    lastReflect: 0,
    riskGuardian,
  }
}

export class StateStore {
  private readonly stateFile: string
  private readonly tradesFile: string

  constructor(private dataDir: string) {
    if (!existsSync(dataDir)) {
      mkdirSync(dataDir, { recursive: true })
    }
    this.stateFile = join(dataDir, 'state.json')
    this.tradesFile = join(dataDir, 'trades.jsonl')
  }

  loadState(): ApexState {
    if (!existsSync(this.stateFile)) {
      return createDefaultState()
    }
    const raw = readFileSync(this.stateFile, 'utf-8')
    return JSON.parse(raw) as ApexState
  }

  saveState(state: ApexState): void {
    const tmpFile = join(this.dataDir, 'state.json.tmp')
    writeFileSync(tmpFile, JSON.stringify(state, null, 2), 'utf-8')
    renameSync(tmpFile, this.stateFile)
  }

  appendTrade(trade: TradeRecord): void {
    appendFileSync(this.tradesFile, JSON.stringify(trade) + '\n', 'utf-8')
  }

  loadTrades(since?: number): TradeRecord[] {
    if (!existsSync(this.tradesFile)) {
      return []
    }
    const raw = readFileSync(this.tradesFile, 'utf-8')
    const lines = raw.trim().split('\n').filter(Boolean)
    const trades = lines.map((line) => JSON.parse(line) as TradeRecord)

    if (since !== undefined) {
      return trades.filter((t) => t.exitTime >= since)
    }
    return trades
  }
}
