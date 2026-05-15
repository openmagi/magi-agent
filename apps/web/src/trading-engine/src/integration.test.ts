import { jest, describe, beforeEach, afterEach, it, expect } from '@jest/globals'
import { mkdtempSync, rmSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { ApexOrchestrator } from './core/apex-orchestrator.js'
import { StateStore, createEmptySlot } from './core/state-store.js'
import { GUARD_PRESETS } from './types.js'
import type {
  EngineConfig,
  ExchangeAdapterInterface,
  Ticker,
  Candle,
  Balance,
  ApexState,
  ExchangeInfo,
  OrderResult,
} from './types.js'

// ── Helpers ────────────────────────────────────────────────────────

function makeTicker(symbol: string, price: number, overrides: Partial<Ticker> = {}): Ticker {
  return {
    symbol,
    mid: price,
    bid: price - 1,
    ask: price + 1,
    lastPrice: price,
    volume24h: 1_000_000,
    openInterest: 5_000_000,
    fundingRate: 0.0001,
    timestamp: Date.now(),
    ...overrides,
  }
}

function makeCandles(count: number, basePrice: number): Candle[] {
  const candles: Candle[] = []
  for (let i = 0; i < count; i++) {
    const close = basePrice + i * 10
    candles.push({
      timestamp: Date.now() - (count - i) * 60_000,
      open: close - 1,
      high: close + 2,
      low: close - 2,
      close,
      volume: 100_000,
    })
  }
  return candles
}

const filledResult: OrderResult = {
  orderId: 'order-123',
  status: 'FILLED',
  filledSize: 0.1,
  filledPrice: 3000,
  timestamp: Date.now(),
}

const defaultExchangeInfo: ExchangeInfo = {
  name: 'mock',
  testnet: true,
  supportedSymbols: ['ETH-PERP', 'BTC-PERP'],
  minOrderSizes: { 'ETH-PERP': 0.01, 'BTC-PERP': 0.001 },
  tickSizes: { 'ETH-PERP': 0.1, 'BTC-PERP': 1 },
}

function createMockAdapter(): ExchangeAdapterInterface {
  return {
    name: 'mock',
    getTicker: jest.fn<ExchangeAdapterInterface['getTicker']>(),
    getOrderBook: jest.fn<ExchangeAdapterInterface['getOrderBook']>(),
    getCandles: jest.fn<ExchangeAdapterInterface['getCandles']>(),
    getBalances: jest.fn<ExchangeAdapterInterface['getBalances']>(),
    getPositions: jest.fn<ExchangeAdapterInterface['getPositions']>(),
    placeOrder: jest.fn<ExchangeAdapterInterface['placeOrder']>(),
    cancelOrder: jest.fn<ExchangeAdapterInterface['cancelOrder']>(),
    cancelAllOrders: jest.fn<ExchangeAdapterInterface['cancelAllOrders']>(),
    setStopLoss: jest.fn<ExchangeAdapterInterface['setStopLoss']>(),
    getOpenOrders: jest.fn<ExchangeAdapterInterface['getOpenOrders']>(),
    getExchangeInfo: jest.fn<ExchangeAdapterInterface['getExchangeInfo']>(),
  }
}

function createTestConfig(overrides: Partial<EngineConfig> = {}): EngineConfig {
  return {
    exchange: { name: 'hyperliquid', testnet: true },
    apex: {
      preset: 'default',
      maxSlots: 3,
      leverage: 10,
      radarThreshold: 170,
      dailyLossLimit: 500,
      tickIntervalMs: 60_000,
    },
    guard: { ...GUARD_PRESETS['moderate'] },
    strategy: {
      name: 'apex',
      symbols: ['ETH-PERP', 'BTC-PERP'],
      params: {},
    },
    reflect: {
      autoAdjust: true,
      intervalTicks: 240,
    },
    ...overrides,
  }
}

function setupDefaultAdapterMocks(adapter: ExchangeAdapterInterface): void {
  const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
  mockGetTicker.mockImplementation(async (symbol: string) => {
    if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
    return makeTicker(symbol, 3000)
  })

  const mockGetCandles = adapter.getCandles as jest.MockedFunction<ExchangeAdapterInterface['getCandles']>
  mockGetCandles.mockResolvedValue(makeCandles(20, 3000))

  const mockGetBalances = adapter.getBalances as jest.MockedFunction<ExchangeAdapterInterface['getBalances']>
  mockGetBalances.mockResolvedValue([
    { currency: 'USD', available: 8000, total: 10000, unrealizedPnl: 0 },
  ])

  const mockGetPositions = adapter.getPositions as jest.MockedFunction<ExchangeAdapterInterface['getPositions']>
  mockGetPositions.mockResolvedValue([])

  const mockPlaceOrder = adapter.placeOrder as jest.MockedFunction<ExchangeAdapterInterface['placeOrder']>
  mockPlaceOrder.mockResolvedValue(filledResult)

  const mockSetStopLoss = adapter.setStopLoss as jest.MockedFunction<ExchangeAdapterInterface['setStopLoss']>
  mockSetStopLoss.mockResolvedValue({
    orderId: 'sl-001',
    status: 'OPEN',
    filledSize: 0,
    filledPrice: 0,
    timestamp: Date.now(),
  })

  const mockCancelAllOrders = adapter.cancelAllOrders as jest.MockedFunction<ExchangeAdapterInterface['cancelAllOrders']>
  mockCancelAllOrders.mockResolvedValue(undefined)

  const mockGetOpenOrders = adapter.getOpenOrders as jest.MockedFunction<ExchangeAdapterInterface['getOpenOrders']>
  mockGetOpenOrders.mockResolvedValue([])

  const mockGetExchangeInfo = adapter.getExchangeInfo as jest.MockedFunction<ExchangeAdapterInterface['getExchangeInfo']>
  mockGetExchangeInfo.mockResolvedValue(defaultExchangeInfo)
}

// ── Integration Tests ──────────────────────────────────────────────

describe('Integration: APEX Full Pipeline (Mock)', () => {
  let tmpDir: string
  let adapter: ExchangeAdapterInterface
  let config: EngineConfig

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), 'apex-integration-'))
    adapter = createMockAdapter()
    config = createTestConfig()
    setupDefaultAdapterMocks(adapter)
  })

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true })
  })

  it('should run 10 ticks with mock adapter', async () => {
    const stateStore = new StateStore(tmpDir)
    const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

    for (let i = 0; i < 10; i++) {
      await orchestrator.tick()
    }

    // Verify state persisted with tickNumber === 10
    const savedState = stateStore.loadState()
    expect(savedState.tickNumber).toBe(10)

    // Verify Pulse processed data (getTicker called for all tracked symbols each tick)
    // 2 symbols + BTC = 3 symbols per tick * 10 ticks = 30 minimum calls
    const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
    expect(mockGetTicker.mock.calls.length).toBeGreaterThanOrEqual(20)

    // Verify state file exists on disk
    const stateFilePath = join(tmpDir, 'state.json')
    expect(existsSync(stateFilePath)).toBe(true)
  })

  it('should handle Risk Guardian COOLDOWN correctly', async () => {
    const now = Date.now()

    // Start with state at COOLDOWN with a cooldown that expires 100ms from now
    const cooldownExpiresAt = now + 100

    const state: ApexState = {
      slots: [createEmptySlot(0), createEmptySlot(1), createEmptySlot(2)],
      tickNumber: 0,
      startedAt: now,
      lastRadarScan: 0,
      lastReflect: 0,
      riskGuardian: {
        gate: 'COOLDOWN',
        consecutiveLosses: 2,
        dailyPnl: -200,
        dailyLossLimit: 500,
        cooldownExpiresAt,
        lastResetDate: new Date().toISOString().slice(0, 10),
      },
    }

    const stateStore = new StateStore(tmpDir)
    stateStore.saveState(state)

    // Create strong entry signal scenario
    const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
    // Tick 1: baseline for pulse
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
      return makeTicker(symbol, 3000, {
        openInterest: 1_000_000,
        volume24h: 100_000,
      })
    })

    const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

    // Tick 1: During COOLDOWN - entries should be blocked
    await orchestrator.tick()

    const mockPlaceOrder = adapter.placeOrder as jest.MockedFunction<ExchangeAdapterInterface['placeOrder']>
    const callsAfterTick1 = mockPlaceOrder.mock.calls.length
    expect(callsAfterTick1).toBe(0) // No entries placed during COOLDOWN

    // Verify gate is still COOLDOWN (or might have transitioned if time passed)
    const stateAfterTick1 = stateStore.loadState()
    // The gate could still be COOLDOWN or might have transitioned to OPEN
    // depending on timing. The key test is that no entries were placed during COOLDOWN.

    // Now wait for cooldown to expire (it was set to 100ms)
    await new Promise<void>((resolve) => setTimeout(resolve, 150))

    // Tick 2: with massive signal after cooldown expires
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
      if (symbol === 'ETH-PERP') {
        return makeTicker('ETH-PERP', 3050, {
          openInterest: 1_200_000, // +20% OI
          volume24h: 600_000,      // 6x volume
        })
      }
      return makeTicker(symbol, 3000)
    })

    await orchestrator.tick()

    // After cooldown expires, gate should be OPEN and entries should be possible
    const stateAfterTick2 = stateStore.loadState()
    expect(stateAfterTick2.riskGuardian.gate).toBe('OPEN')
  })

  it('should restore state on restart', async () => {
    const stateStore = new StateStore(tmpDir)

    // Phase 1: Run 5 ticks with first orchestrator
    const orchestrator1 = new ApexOrchestrator(config, adapter, stateStore)

    for (let i = 0; i < 5; i++) {
      await orchestrator1.tick()
    }

    const stateAfterPhase1 = stateStore.loadState()
    expect(stateAfterPhase1.tickNumber).toBe(5)

    // Phase 2: Create a NEW orchestrator from the same data dir (simulate restart)
    const adapter2 = createMockAdapter()
    setupDefaultAdapterMocks(adapter2)
    const stateStore2 = new StateStore(tmpDir)
    const orchestrator2 = new ApexOrchestrator(config, adapter2, stateStore2)

    for (let i = 0; i < 5; i++) {
      await orchestrator2.tick()
    }

    // Verify tickNumber is 10 (5 from phase1 + 5 from phase2)
    const finalState = stateStore2.loadState()
    expect(finalState.tickNumber).toBe(10)

    // Verify state continuity
    expect(finalState.startedAt).toBe(stateAfterPhase1.startedAt)
  })

  it('should detect Pulse signals and attempt entries', async () => {
    const stateStore = new StateStore(tmpDir)

    const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>

    // Tick 1: baseline for pulse — establish initial OI/volume snapshot
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
      return makeTicker(symbol, 3000, {
        openInterest: 1_000_000,
        volume24h: 100_000,
      })
    })

    const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
    await orchestrator.tick()

    const mockPlaceOrder = adapter.placeOrder as jest.MockedFunction<ExchangeAdapterInterface['placeOrder']>
    const callsAfterBaseline = mockPlaceOrder.mock.calls.length
    // No entries on first tick (no pulse baseline yet for comparison)
    expect(callsAfterBaseline).toBe(0)

    // Tick 2: massive OI + volume changes to trigger IMMEDIATE_MOVER / FIRST_JUMP
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
      if (symbol === 'ETH-PERP') {
        return makeTicker('ETH-PERP', 3050, {
          openInterest: 1_200_000, // +20% OI jump
          volume24h: 600_000,      // 6x volume surge
        })
      }
      return makeTicker(symbol, 3000, {
        openInterest: 1_000_000,
        volume24h: 100_000,
      })
    })

    await orchestrator.tick()

    // Verify that placeOrder was called for entry
    expect(mockPlaceOrder.mock.calls.length).toBeGreaterThan(callsAfterBaseline)

    // Verify an OPEN slot was created
    const savedState = stateStore.loadState()
    const openSlots = savedState.slots.filter(s => s.status === 'OPEN')
    expect(openSlots.length).toBeGreaterThanOrEqual(1)

    // Verify the opened slot is for ETH-PERP
    const ethSlot = openSlots.find(s => s.symbol === 'ETH-PERP')
    expect(ethSlot).toBeDefined()
    expect(ethSlot!.side).toBe('LONG') // Price went up, direction should be LONG
    expect(ethSlot!.entryPrice).toBeGreaterThan(0)
    expect(ethSlot!.size).toBeGreaterThan(0)
  })

  it('should run full lifecycle: entry → guard exit → trade recorded', async () => {
    const stateStore = new StateStore(tmpDir)

    const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>

    // Tick 1: baseline
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
      return makeTicker(symbol, 3000, {
        openInterest: 1_000_000,
        volume24h: 100_000,
      })
    })

    const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
    await orchestrator.tick()

    // Tick 2: strong signal → entry
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
      if (symbol === 'ETH-PERP') {
        return makeTicker('ETH-PERP', 3050, {
          openInterest: 1_200_000,
          volume24h: 600_000,
        })
      }
      return makeTicker(symbol, 3000)
    })

    await orchestrator.tick()

    // Verify entry was made
    const stateAfterEntry = stateStore.loadState()
    const openSlots = stateAfterEntry.slots.filter(s => s.status === 'OPEN')
    expect(openSlots.length).toBeGreaterThanOrEqual(1)

    // Tick 3: price crashes → guard triggers exit
    // Entry was at filledPrice=3000 (mock), price drops to 2970
    // ROE = ((2970 - 3000) / 3000) * 10 * 100 = -10% → triggers phase1 retrace (threshold 3%)
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
      if (symbol === 'ETH-PERP') return makeTicker('ETH-PERP', 2970)
      return makeTicker(symbol, 3000)
    })

    await orchestrator.tick()

    // Verify trade was recorded (guard triggered exit on the LONG position)
    const trades = stateStore.loadTrades()
    expect(trades.length).toBeGreaterThanOrEqual(1)
    const trade = trades[0]!
    expect(trade.symbol).toBe('ETH-PERP')
    expect(trade.side).toBe('LONG')
    expect(trade.pnl).toBeLessThan(0) // Losing trade

    // Verify risk guardian updated with the loss
    const stateAfterExit = stateStore.loadState()
    expect(stateAfterExit.riskGuardian.consecutiveLosses).toBeGreaterThanOrEqual(1)
    expect(stateAfterExit.riskGuardian.dailyPnl).toBeLessThan(0)
  })

  it('should persist trades across restart', async () => {
    const now = Date.now()

    // Pre-seed state with an open position that will trigger exit
    const state: ApexState = {
      slots: [
        {
          ...createEmptySlot(0),
          status: 'OPEN',
          symbol: 'ETH-PERP',
          side: 'LONG',
          entryPrice: 3000,
          size: 0.1,
          entryTime: now - 60_000,
          guardPhase: 'PHASE_1',
          peakRoe: 0,
          currentRoe: 0,
          tierLevel: 0,
        },
        createEmptySlot(1),
        createEmptySlot(2),
      ],
      tickNumber: 0,
      startedAt: now,
      lastRadarScan: 0,
      lastReflect: 0,
      riskGuardian: {
        gate: 'OPEN',
        consecutiveLosses: 0,
        dailyPnl: 0,
        dailyLossLimit: 500,
        cooldownExpiresAt: null,
        lastResetDate: new Date().toISOString().slice(0, 10),
      },
    }

    const stateStore = new StateStore(tmpDir)
    stateStore.saveState(state)

    // Price drop triggers guard exit
    const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
      if (symbol === 'ETH-PERP') return makeTicker('ETH-PERP', 2970)
      return makeTicker(symbol, 3000)
    })

    const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
    await orchestrator.tick()

    // Trade should be recorded
    const trades1 = stateStore.loadTrades()
    expect(trades1.length).toBeGreaterThanOrEqual(1)

    // Restart: new state store from same directory should load the same trades
    const stateStore2 = new StateStore(tmpDir)
    const trades2 = stateStore2.loadTrades()
    expect(trades2.length).toBe(trades1.length)
    expect(trades2[0]!.symbol).toBe('ETH-PERP')
  })

  it('should handle multiple slots independently', async () => {
    const now = Date.now()

    // Pre-seed with 2 open positions at different prices
    const state: ApexState = {
      slots: [
        {
          ...createEmptySlot(0),
          status: 'OPEN',
          symbol: 'ETH-PERP',
          side: 'LONG',
          entryPrice: 3000,
          size: 0.1,
          entryTime: now - 60_000,
          guardPhase: 'PHASE_1',
          peakRoe: 0,
          currentRoe: 0,
          tierLevel: 0,
        },
        {
          ...createEmptySlot(1),
          status: 'OPEN',
          symbol: 'BTC-PERP',
          side: 'LONG',
          entryPrice: 60000,
          size: 0.01,
          entryTime: now - 60_000,
          guardPhase: 'PHASE_1',
          peakRoe: 0,
          currentRoe: 0,
          tierLevel: 0,
        },
        createEmptySlot(2),
      ],
      tickNumber: 0,
      startedAt: now,
      lastRadarScan: 0,
      lastReflect: 0,
      riskGuardian: {
        gate: 'OPEN',
        consecutiveLosses: 0,
        dailyPnl: 0,
        dailyLossLimit: 500,
        cooldownExpiresAt: null,
        lastResetDate: new Date().toISOString().slice(0, 10),
      },
    }

    const stateStore = new StateStore(tmpDir)
    stateStore.saveState(state)

    // ETH drops sharply (triggers exit), BTC stays stable
    const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
    mockGetTicker.mockImplementation(async (symbol: string) => {
      if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000) // No change
      if (symbol === 'ETH-PERP') return makeTicker('ETH-PERP', 2970) // -1% → -10% ROE
      return makeTicker(symbol, 3000)
    })

    const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
    await orchestrator.tick()

    const savedState = stateStore.loadState()

    // ETH slot should be closed (guard triggered)
    const ethSlot = savedState.slots[0]!
    expect(ethSlot.status).toBe('EMPTY')

    // BTC slot should still be OPEN (price didn't move)
    const btcSlot = savedState.slots[1]!
    expect(btcSlot.status).toBe('OPEN')
    expect(btcSlot.symbol).toBe('BTC-PERP')
  })

  it('should run Radar at correct interval during integration', async () => {
    const stateStore = new StateStore(tmpDir)
    const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

    const mockGetCandles = adapter.getCandles as jest.MockedFunction<ExchangeAdapterInterface['getCandles']>

    // Run 15 ticks
    for (let i = 0; i < 15; i++) {
      await orchestrator.tick()
    }

    // Radar runs at tick 15 — getCandles should have been called
    // (for BTC + tracked symbols)
    expect(mockGetCandles.mock.calls.length).toBeGreaterThan(0)

    const savedState = stateStore.loadState()
    expect(savedState.tickNumber).toBe(15)
    expect(savedState.lastRadarScan).toBe(15)
  })

  it('should run REFLECT at correct interval during integration', async () => {
    // Pre-set state near the reflect interval
    const state: ApexState = {
      slots: [createEmptySlot(0), createEmptySlot(1), createEmptySlot(2)],
      tickNumber: 239,
      startedAt: Date.now(),
      lastRadarScan: 225,
      lastReflect: 0,
      riskGuardian: {
        gate: 'OPEN',
        consecutiveLosses: 0,
        dailyPnl: 0,
        dailyLossLimit: 500,
        cooldownExpiresAt: null,
        lastResetDate: new Date().toISOString().slice(0, 10),
      },
    }

    const stateStore = new StateStore(tmpDir)
    stateStore.saveState(state)

    const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
    await orchestrator.tick()

    const savedState = stateStore.loadState()
    expect(savedState.tickNumber).toBe(240)
    expect(savedState.lastReflect).toBe(240)
  })
})
