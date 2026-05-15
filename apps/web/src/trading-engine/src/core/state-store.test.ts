import { mkdtempSync, rmSync, existsSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { StateStore, createEmptySlot } from './state-store.js'
import type { ApexState, TradeRecord } from '../types.js'

describe('StateStore', () => {
  let dataDir: string
  let store: StateStore

  beforeEach(() => {
    dataDir = mkdtempSync(join(tmpdir(), 'state-store-test-'))
    store = new StateStore(dataDir)
  })

  afterEach(() => {
    rmSync(dataDir, { recursive: true, force: true })
  })

  it('should return default state when no file exists', () => {
    const state = store.loadState()

    expect(state.slots).toHaveLength(3)
    expect(state.slots[0]!.id).toBe(0)
    expect(state.slots[1]!.id).toBe(1)
    expect(state.slots[2]!.id).toBe(2)
    for (const slot of state.slots) {
      expect(slot.status).toBe('EMPTY')
      expect(slot.symbol).toBeNull()
      expect(slot.side).toBeNull()
      expect(slot.entryPrice).toBe(0)
      expect(slot.size).toBe(0)
      expect(slot.entryTime).toBe(0)
      expect(slot.peakRoe).toBe(0)
      expect(slot.currentRoe).toBe(0)
      expect(slot.tierLevel).toBe(0)
    }
    expect(state.tickNumber).toBe(0)
    expect(state.startedAt).toBeGreaterThan(0)
    expect(state.lastRadarScan).toBe(0)
    expect(state.lastReflect).toBe(0)
    expect(state.riskGuardian.gate).toBe('OPEN')
    expect(state.riskGuardian.consecutiveLosses).toBe(0)
    expect(state.riskGuardian.dailyPnl).toBe(0)
    expect(state.riskGuardian.dailyLossLimit).toBe(500)
    expect(state.riskGuardian.cooldownExpiresAt).toBeNull()
    // lastResetDate should be today in YYYY-MM-DD format
    const today = new Date().toISOString().slice(0, 10)
    expect(state.riskGuardian.lastResetDate).toBe(today)
  })

  it('should save and load state round-trip', () => {
    const state = store.loadState()
    state.tickNumber = 42
    state.slots[0]!.status = 'OPEN'
    state.slots[0]!.symbol = 'BTC-PERP'
    state.slots[0]!.side = 'LONG'
    state.slots[0]!.entryPrice = 50000
    state.riskGuardian.dailyPnl = 123.45

    store.saveState(state)
    const loaded = store.loadState()

    expect(loaded.tickNumber).toBe(42)
    expect(loaded.slots[0]!.status).toBe('OPEN')
    expect(loaded.slots[0]!.symbol).toBe('BTC-PERP')
    expect(loaded.slots[0]!.side).toBe('LONG')
    expect(loaded.slots[0]!.entryPrice).toBe(50000)
    expect(loaded.riskGuardian.dailyPnl).toBe(123.45)
  })

  it('should write atomically (state.json.tmp then rename)', () => {
    const state = store.loadState()
    store.saveState(state)

    // After save, state.json should exist but state.json.tmp should not
    expect(existsSync(join(dataDir, 'state.json'))).toBe(true)
    expect(existsSync(join(dataDir, 'state.json.tmp'))).toBe(false)
  })

  it('should append trade records to trades.jsonl', () => {
    const trade: TradeRecord = {
      id: 'trade-1',
      symbol: 'ETH-PERP',
      side: 'LONG',
      entryPrice: 3000,
      exitPrice: 3100,
      size: 1,
      entryTime: 1000,
      exitTime: 2000,
      pnl: 100,
      fees: 2,
      netPnl: 98,
      exitReason: 'TP',
      slotId: 0,
    }

    store.appendTrade(trade)

    const raw = readFileSync(join(dataDir, 'trades.jsonl'), 'utf-8')
    const lines = raw.trim().split('\n')
    expect(lines).toHaveLength(1)
    const parsed = JSON.parse(lines[0]!) as TradeRecord
    expect(parsed.id).toBe('trade-1')
    expect(parsed.netPnl).toBe(98)
  })

  it('should load multiple trade records', () => {
    const makeTrade = (id: string, exitTime: number): TradeRecord => ({
      id,
      symbol: 'BTC-PERP',
      side: 'LONG',
      entryPrice: 50000,
      exitPrice: 51000,
      size: 0.1,
      entryTime: exitTime - 1000,
      exitTime,
      pnl: 100,
      fees: 1,
      netPnl: 99,
      exitReason: 'TP',
      slotId: 0,
    })

    store.appendTrade(makeTrade('t1', 1000))
    store.appendTrade(makeTrade('t2', 2000))
    store.appendTrade(makeTrade('t3', 3000))

    const trades = store.loadTrades()
    expect(trades).toHaveLength(3)
    expect(trades[0]!.id).toBe('t1')
    expect(trades[1]!.id).toBe('t2')
    expect(trades[2]!.id).toBe('t3')
  })

  it('should filter trades by since timestamp', () => {
    const makeTrade = (id: string, exitTime: number): TradeRecord => ({
      id,
      symbol: 'BTC-PERP',
      side: 'SHORT',
      entryPrice: 50000,
      exitPrice: 49000,
      size: 0.1,
      entryTime: exitTime - 1000,
      exitTime,
      pnl: 100,
      fees: 1,
      netPnl: 99,
      exitReason: 'TP',
      slotId: 1,
    })

    store.appendTrade(makeTrade('t1', 1000))
    store.appendTrade(makeTrade('t2', 2000))
    store.appendTrade(makeTrade('t3', 3000))

    const filtered = store.loadTrades(2000)
    expect(filtered).toHaveLength(2)
    expect(filtered[0]!.id).toBe('t2')
    expect(filtered[1]!.id).toBe('t3')
  })

  it('should return empty array when trades.jsonl does not exist', () => {
    const trades = store.loadTrades()
    expect(trades).toEqual([])
  })

  it('should create data directory if it does not exist', () => {
    const nestedDir = join(dataDir, 'nested', 'deep', 'dir')
    const nestedStore = new StateStore(nestedDir)

    expect(existsSync(nestedDir)).toBe(true)

    // Should also work (load default state)
    const state = nestedStore.loadState()
    expect(state.tickNumber).toBe(0)
  })

  it('should create valid empty slots with createEmptySlot', () => {
    const slot = createEmptySlot(7)

    expect(slot.id).toBe(7)
    expect(slot.status).toBe('EMPTY')
    expect(slot.symbol).toBeNull()
    expect(slot.side).toBeNull()
    expect(slot.entryPrice).toBe(0)
    expect(slot.size).toBe(0)
    expect(slot.entryTime).toBe(0)
    expect(slot.guardPhase).toBe('PHASE_1')
    expect(slot.peakRoe).toBe(0)
    expect(slot.currentRoe).toBe(0)
    expect(slot.tierLevel).toBe(0)
  })
})
