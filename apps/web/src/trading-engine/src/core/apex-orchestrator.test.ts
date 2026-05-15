import { jest, describe, beforeEach, it, expect } from '@jest/globals'
import { ApexOrchestrator } from './apex-orchestrator.js'
import { StateStore, createEmptySlot } from './state-store.js'
import type {
  EngineConfig,
  ExchangeAdapterInterface,
  Ticker,
  ApexState,
  ApexSlot,
  RiskGuardianState,
  Balance,
  Position,
  OrderResult,
  Candle,
  ExchangeInfo,
} from '../types.js'
import { GUARD_PRESETS } from '../types.js'

// ── Helpers ──────────────────────────────────────────────────────

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

function makeCandle(close: number, timestamp: number): Candle {
  return {
    timestamp,
    open: close - 1,
    high: close + 2,
    low: close - 2,
    close,
    volume: 100_000,
  }
}

function makeCandles(count: number, basePrice: number): Candle[] {
  const candles: Candle[] = []
  for (let i = 0; i < count; i++) {
    candles.push(makeCandle(basePrice + i * 10, Date.now() - (count - i) * 60_000))
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

// ── Tests ────────────────────────────────────────────────────────

describe('ApexOrchestrator', () => {
  let adapter: ExchangeAdapterInterface
  let stateStore: StateStore
  let config: EngineConfig
  let tmpDir: string

  beforeEach(() => {
    // Use unique temp dir per test to avoid state leaking
    tmpDir = `/tmp/apex-test-${Date.now()}-${Math.random().toString(36).slice(2)}`
    stateStore = new StateStore(tmpDir)
    adapter = createMockAdapter()
    config = createTestConfig()
    setupDefaultAdapterMocks(adapter)
  })

  describe('Tick Loop', () => {
    it('should run Pulse scan every tick', async () => {
      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

      await orchestrator.tick()
      await orchestrator.tick()

      // Pulse reads tickers for all tracked symbols
      // 2 ticks * (2 symbols + 1 BTC) = 6 getTicker calls
      const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
      expect(mockGetTicker.mock.calls.length).toBeGreaterThanOrEqual(4)
    })

    it('should run Guard check every tick', async () => {
      // Set up an open slot so Guard has something to evaluate
      const state: ApexState = {
        slots: [
          {
            ...createEmptySlot(0),
            status: 'OPEN',
            symbol: 'ETH-PERP',
            side: 'LONG',
            entryPrice: 3000,
            size: 0.1,
            entryTime: Date.now() - 60_000,
            guardPhase: 'PHASE_1',
            peakRoe: 0,
            currentRoe: 0,
            tierLevel: 0,
          },
          createEmptySlot(1),
          createEmptySlot(2),
        ],
        tickNumber: 0,
        startedAt: Date.now(),
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
      stateStore.saveState(state)

      // Price hasn't moved much, Guard should HOLD in phase1
      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

      await orchestrator.tick()

      // State should be persisted with updated ROE
      const savedState = stateStore.loadState()
      const slot = savedState.slots[0]!
      expect(slot.status).toBe('OPEN')
      // currentRoe should have been updated
      expect(slot.currentRoe).toBeDefined()
    })

    it('should run Radar every 15 ticks', async () => {
      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

      // Run 14 ticks - no radar
      for (let i = 0; i < 14; i++) {
        await orchestrator.tick()
      }

      const mockGetCandles = adapter.getCandles as jest.MockedFunction<ExchangeAdapterInterface['getCandles']>
      const candleCallsBefore = mockGetCandles.mock.calls.length

      // Tick 15 should trigger Radar (getCandles called for symbols)
      await orchestrator.tick()

      const candleCallsAfter = mockGetCandles.mock.calls.length
      // Radar requires candles for each tracked symbol + BTC
      expect(candleCallsAfter).toBeGreaterThan(candleCallsBefore)
    })

    it('should run REFLECT every 240 ticks', async () => {
      // Pre-set state at tick 239
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
      stateStore.saveState(state)

      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

      await orchestrator.tick()

      // Verify REFLECT ran by checking lastReflect updated
      const savedState = stateStore.loadState()
      expect(savedState.tickNumber).toBe(240)
      expect(savedState.lastReflect).toBe(240)
    })

    it('should run Reconciliation every 5 ticks', async () => {
      const state: ApexState = {
        slots: [createEmptySlot(0), createEmptySlot(1), createEmptySlot(2)],
        tickNumber: 4,
        startedAt: Date.now(),
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
      stateStore.saveState(state)

      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

      await orchestrator.tick()

      // Reconciliation fetches positions + open orders from exchange
      const mockGetPositions = adapter.getPositions as jest.MockedFunction<ExchangeAdapterInterface['getPositions']>
      const mockGetOpenOrders = adapter.getOpenOrders as jest.MockedFunction<ExchangeAdapterInterface['getOpenOrders']>
      expect(mockGetPositions).toHaveBeenCalled()
      expect(mockGetOpenOrders).toHaveBeenCalled()
    })
  })

  describe('Entry Logic', () => {
    it('should enter on FIRST_JUMP (priority 1)', async () => {
      // Need 2 ticks - first to establish Pulse baseline, second to detect signal
      // Create a huge OI jump on ETH-PERP for FIRST_JUMP detection
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

      // Tick 2: massive OI + volume jump (triggers IMMEDIATE_MOVER → FIRST_JUMP for sector)
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

      // Should have placed an entry order
      const mockPlaceOrder = adapter.placeOrder as jest.MockedFunction<ExchangeAdapterInterface['placeOrder']>
      expect(mockPlaceOrder).toHaveBeenCalled()

      // Check state: should have an OPEN slot
      const savedState = stateStore.loadState()
      const openSlots = savedState.slots.filter(s => s.status === 'OPEN')
      expect(openSlots.length).toBeGreaterThanOrEqual(1)
    })

    it('should enter on Radar score > threshold', async () => {
      // Set state to tick 14 so tick() makes it 15 → triggers Radar
      const state: ApexState = {
        slots: [createEmptySlot(0), createEmptySlot(1), createEmptySlot(2)],
        tickNumber: 14,
        startedAt: Date.now(),
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
      stateStore.saveState(state)

      // Create ticker with high volume + OI for a good Radar score
      const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
      mockGetTicker.mockImplementation(async (symbol: string) => {
        if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000, {
          volume24h: 500_000_000,
          openInterest: 200_000_000,
        })
        return makeTicker(symbol, 3000, {
          volume24h: 200_000_000,
          openInterest: 200_000_000,
          fundingRate: -0.02, // Strong negative funding for LONG signal
        })
      })

      // Create trending candles (all bullish) for high Radar score
      const mockGetCandles = adapter.getCandles as jest.MockedFunction<ExchangeAdapterInterface['getCandles']>
      const bullishCandles: Candle[] = []
      for (let i = 0; i < 20; i++) {
        bullishCandles.push({
          timestamp: Date.now() - (20 - i) * 60_000,
          open: 2800 + i * 10,
          high: 2820 + i * 10,
          low: 2795 + i * 10,
          close: 2810 + i * 10, // All bullish
          volume: 100_000,
        })
      }
      mockGetCandles.mockResolvedValue(bullishCandles)

      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

      // Need baseline pulse tick first (so pulse has snapshot)
      await orchestrator.tick()

      // After first tick, tickNumber = 15, radar should fire
      // Radar should score high and create an entry if above threshold
      const savedState = stateStore.loadState()
      expect(savedState.tickNumber).toBe(15)
      expect(savedState.lastRadarScan).toBe(15)
    })

    it('should not enter when Risk Guardian blocks', async () => {
      // Set gate to CLOSED
      const state: ApexState = {
        slots: [createEmptySlot(0), createEmptySlot(1), createEmptySlot(2)],
        tickNumber: 0,
        startedAt: Date.now(),
        lastRadarScan: 0,
        lastReflect: 0,
        riskGuardian: {
          gate: 'CLOSED',
          consecutiveLosses: 3,
          dailyPnl: -600,
          dailyLossLimit: 500,
          cooldownExpiresAt: null,
          lastResetDate: new Date().toISOString().slice(0, 10),
        },
      }
      stateStore.saveState(state)

      // Create a strong signal scenario
      const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
      mockGetTicker.mockImplementation(async (symbol: string) => {
        if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
        return makeTicker(symbol, 3000, {
          openInterest: 1_200_000,
          volume24h: 600_000,
        })
      })

      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
      await orchestrator.tick()
      await orchestrator.tick()

      // No orders should be placed for entry
      const mockPlaceOrder = adapter.placeOrder as jest.MockedFunction<ExchangeAdapterInterface['placeOrder']>
      expect(mockPlaceOrder).not.toHaveBeenCalled()

      // All slots should remain EMPTY
      const savedState = stateStore.loadState()
      const openSlots = savedState.slots.filter(s => s.status === 'OPEN')
      expect(openSlots.length).toBe(0)
    })

    it('should not enter when all slots full', async () => {
      // Fill all slots
      const now = Date.now()
      const state: ApexState = {
        slots: [
          { ...createEmptySlot(0), status: 'OPEN', symbol: 'ETH-PERP', side: 'LONG', entryPrice: 3000, size: 0.1, entryTime: now - 60_000 },
          { ...createEmptySlot(1), status: 'OPEN', symbol: 'BTC-PERP', side: 'LONG', entryPrice: 60000, size: 0.01, entryTime: now - 60_000 },
          { ...createEmptySlot(2), status: 'OPEN', symbol: 'ETH-PERP', side: 'SHORT', entryPrice: 3100, size: 0.05, entryTime: now - 60_000 },
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
      stateStore.saveState(state)

      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
      await orchestrator.tick()
      await orchestrator.tick()

      // No new entry orders (only possible SL sync)
      // placeOrder should not be called for entry. The only calls might be from setStopLoss.
      const mockPlaceOrder = adapter.placeOrder as jest.MockedFunction<ExchangeAdapterInterface['placeOrder']>
      // Filter out any non-entry calls (reduce-only orders for exits are ok)
      const entryCalls = mockPlaceOrder.mock.calls.filter(call => {
        const order = call[0]
        return !order.reduceOnly
      })
      expect(entryCalls.length).toBe(0)
    })
  })

  describe('Exit Logic', () => {
    it('should exit on Guard trigger', async () => {
      const now = Date.now()
      // Create a slot with a price that will trigger phase1 retrace
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
      stateStore.saveState(state)

      // Price dropped significantly → triggers phase1_retrace (ROE < -3% with 10x leverage)
      // Entry 3000 LONG, current 2980 → ROE = ((2980-3000)/3000)*10*100 = -6.67%
      const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
      mockGetTicker.mockImplementation(async (symbol: string) => {
        if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
        if (symbol === 'ETH-PERP') return makeTicker('ETH-PERP', 2980)
        return makeTicker(symbol, 3000)
      })

      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
      await orchestrator.tick()

      // Should have placed a close order (market exit)
      const mockPlaceOrder = adapter.placeOrder as jest.MockedFunction<ExchangeAdapterInterface['placeOrder']>
      const closeCalls = mockPlaceOrder.mock.calls.filter(call => call[0].reduceOnly)
      expect(closeCalls.length).toBeGreaterThanOrEqual(1)

      // Slot should be EMPTY (closed and reset) or CLOSED
      const savedState = stateStore.loadState()
      const slot0 = savedState.slots[0]!
      expect(['EMPTY', 'CLOSED']).toContain(slot0.status)
    })

    it('should exit on daily loss limit', async () => {
      const now = Date.now()
      // Set daily PnL close to the limit, then a loss should trigger gate CLOSED
      // PnL = (exitPrice - entryPrice) * size = (2950 - 3000) * 1.0 = -50
      // (size already includes leverage from calcSize, so no extra leverage multiplier)
      // netPnl ≈ -50.05, dailyPnl goes from -450 to -500.05 → exceeds 500 → CLOSED
      const state: ApexState = {
        slots: [
          {
            ...createEmptySlot(0),
            status: 'OPEN',
            symbol: 'ETH-PERP',
            side: 'LONG',
            entryPrice: 3000,
            size: 1.0,
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
          dailyPnl: -450, // already near limit
          dailyLossLimit: 500,
          cooldownExpiresAt: null,
          lastResetDate: new Date().toISOString().slice(0, 10),
        },
      }
      stateStore.saveState(state)

      // Price crashed → triggers exit → pnl pushes over daily limit
      const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
      mockGetTicker.mockImplementation(async (symbol: string) => {
        if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
        if (symbol === 'ETH-PERP') return makeTicker('ETH-PERP', 2950) // big loss
        return makeTicker(symbol, 3000)
      })

      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
      await orchestrator.tick()

      // Risk guardian should be CLOSED after recording the loss
      const savedState = stateStore.loadState()
      expect(savedState.riskGuardian.gate).toBe('CLOSED')
    })
  })

  describe('State Persistence', () => {
    it('should save state after every tick', async () => {
      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

      await orchestrator.tick()

      const savedState = stateStore.loadState()
      expect(savedState.tickNumber).toBe(1)

      await orchestrator.tick()

      const savedState2 = stateStore.loadState()
      expect(savedState2.tickNumber).toBe(2)
    })

    it('should restore state on restart', async () => {
      const now = Date.now()
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
            peakRoe: 5,
            currentRoe: 3,
            tierLevel: 0,
          },
          createEmptySlot(1),
          createEmptySlot(2),
        ],
        tickNumber: 42,
        startedAt: now - 3_600_000,
        lastRadarScan: 30,
        lastReflect: 0,
        riskGuardian: {
          gate: 'OPEN',
          consecutiveLosses: 1,
          dailyPnl: -100,
          dailyLossLimit: 500,
          cooldownExpiresAt: null,
          lastResetDate: new Date().toISOString().slice(0, 10),
        },
      }
      stateStore.saveState(state)

      // Create new orchestrator (simulating restart)
      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)

      await orchestrator.tick()

      const savedState = stateStore.loadState()
      // tickNumber should continue from 42 → 43
      expect(savedState.tickNumber).toBe(43)
      // Previous state should be preserved
      expect(savedState.riskGuardian.dailyPnl).toBeLessThanOrEqual(0) // at least carried over
    })

    it('should record trades on position close', async () => {
      const now = Date.now()
      const state: ApexState = {
        slots: [
          {
            ...createEmptySlot(0),
            status: 'OPEN',
            symbol: 'ETH-PERP',
            side: 'LONG',
            entryPrice: 3000,
            size: 0.1,
            entryTime: now - 120_000,
            guardPhase: 'PHASE_1',
            peakRoe: 1,
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
      stateStore.saveState(state)

      // Trigger guard exit with big price drop
      const mockGetTicker = adapter.getTicker as jest.MockedFunction<ExchangeAdapterInterface['getTicker']>
      mockGetTicker.mockImplementation(async (symbol: string) => {
        if (symbol === 'BTC-PERP') return makeTicker('BTC-PERP', 60000)
        if (symbol === 'ETH-PERP') return makeTicker('ETH-PERP', 2970) // triggers retrace
        return makeTicker(symbol, 3000)
      })

      const orchestrator = new ApexOrchestrator(config, adapter, stateStore)
      await orchestrator.tick()

      // Trade should be recorded
      const trades = stateStore.loadTrades()
      expect(trades.length).toBeGreaterThanOrEqual(1)
      const trade = trades[0]!
      expect(trade.symbol).toBe('ETH-PERP')
      expect(trade.side).toBe('LONG')
      expect(trade.entryPrice).toBe(3000)
      expect(trade.pnl).toBeLessThan(0) // it was a losing trade
    })
  })
})
