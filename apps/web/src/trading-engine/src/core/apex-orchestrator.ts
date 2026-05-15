import type {
  EngineConfig,
  ExchangeAdapterInterface,
  ApexState,
  ApexSlot,
  Ticker,
  Candle,
  TradeRecord,
  PulseSignal,
  PulseSignalType,
  RadarScore,
} from '../types.js'
import { StateStore, createEmptySlot } from './state-store.js'
import { RiskGuardian } from './risk-guardian.js'
import { Guard } from '../guard/trailing-stop.js'
import { OrderManager } from './order-manager.js'
import { Pulse } from '../signals/pulse.js'
import { Radar, calculateRSI } from '../signals/radar.js'
import { ReflectAnalyzer } from '../reflect/analyzer.js'

const RADAR_INTERVAL = 15
const RECONCILE_INTERVAL = 5

/**
 * Priority ordering for Pulse signal types.
 * Lower index = higher priority.
 */
const SIGNAL_PRIORITY: PulseSignalType[] = [
  'FIRST_JUMP',
  'CONTRIB_EXPLOSION',
  'IMMEDIATE_MOVER',
  'NEW_ENTRY_DEEP',
  'DEEP_CLIMBER',
]

interface EntryCandidate {
  symbol: string
  direction: 'LONG' | 'SHORT'
  source: 'pulse' | 'radar'
  priority: number
  confidence: number
}

/**
 * ApexOrchestrator is the top-level engine loop that composes all subsystems:
 * Pulse (signals), Radar (screening), Guard (trailing stop), RiskGuardian (risk),
 * OrderManager (execution), ReflectAnalyzer (self-improvement), and StateStore (persistence).
 */
export class ApexOrchestrator {
  private state: ApexState
  private readonly config: EngineConfig
  private readonly adapter: ExchangeAdapterInterface
  private readonly stateStore: StateStore

  private readonly riskGuardian: RiskGuardian
  private readonly guard: Guard
  private readonly orderManager: OrderManager
  private readonly pulse: Pulse
  private readonly radar: Radar
  private readonly reflect: ReflectAnalyzer

  // Radar results cached between scans
  private lastRadarScores: RadarScore[] = []

  constructor(
    config: EngineConfig,
    adapter: ExchangeAdapterInterface,
    stateStore: StateStore,
  ) {
    this.config = config
    this.adapter = adapter
    this.stateStore = stateStore

    // Load persisted state
    this.state = stateStore.loadState()

    // Ensure correct number of slots
    while (this.state.slots.length < config.apex.maxSlots) {
      this.state.slots.push(createEmptySlot(this.state.slots.length))
    }

    // Initialize subsystems
    this.riskGuardian = new RiskGuardian(this.state.riskGuardian)
    const guardConfig = { ...config.guard, leverage: config.apex.leverage }
    this.guard = new Guard(guardConfig)
    this.orderManager = new OrderManager()
    this.pulse = new Pulse()
    this.radar = new Radar()
    this.reflect = new ReflectAnalyzer()
  }

  /**
   * Main tick loop. Called once per tick interval.
   */
  async tick(): Promise<void> {
    this.state.tickNumber++
    const now = Date.now()

    // 1. Fetch market data
    const tickers = await this.fetchTickers()

    // 2. Update slots' ROE based on current prices
    this.updateSlotROE(tickers)

    // 3. Risk Guardian tick (cooldown expiry, daily reset)
    this.riskGuardian.tick(now)

    // 4. Pulse scan (every tick) - detect momentum signals
    const allTickers = Array.from(tickers.values())
    const pulseResult = this.pulse.scan(allTickers)

    // 5. Guard check (every tick, per active slot) - exit if triggered
    await this.evaluateGuards(tickers, now)

    // 6. Strategy execution - evaluate entries if risk guardian allows
    const entryCandidates: EntryCandidate[] = []

    // Collect Pulse entry candidates
    for (const signal of pulseResult.signals) {
      const priority = this.getSignalPriority(signal)
      if (priority >= 0) {
        entryCandidates.push({
          symbol: signal.symbol,
          direction: signal.direction,
          source: 'pulse',
          priority,
          confidence: signal.confidence,
        })
      }
    }

    // 7. Radar (every RADAR_INTERVAL ticks) - opportunity screening
    if (this.state.tickNumber % RADAR_INTERVAL === 0) {
      await this.runRadar(tickers)
      this.state.lastRadarScan = this.state.tickNumber
    }

    // Collect Radar entry candidates (lower priority than pulse)
    for (const score of this.lastRadarScores) {
      if (score.total >= this.config.apex.radarThreshold) {
        entryCandidates.push({
          symbol: score.symbol,
          direction: score.direction,
          source: 'radar',
          priority: SIGNAL_PRIORITY.length, // lower than all pulse signals
          confidence: score.total,
        })
      }
    }

    // Sort candidates by priority (lower = better) then confidence (higher = better)
    entryCandidates.sort((a, b) => {
      if (a.priority !== b.priority) return a.priority - b.priority
      return b.confidence - a.confidence
    })

    // Execute entries
    await this.executeEntries(entryCandidates, tickers)

    // 8. Reconciliation (every RECONCILE_INTERVAL ticks) - sync exchange state
    if (this.state.tickNumber % RECONCILE_INTERVAL === 0) {
      await this.reconcile()
    }

    // 9. REFLECT (every config.reflect.intervalTicks ticks) - self-improvement
    if (
      this.config.reflect.intervalTicks > 0 &&
      this.state.tickNumber % this.config.reflect.intervalTicks === 0
    ) {
      this.runReflect()
      this.state.lastReflect = this.state.tickNumber
    }

    // 10. Persist state
    this.state.riskGuardian = this.riskGuardian.getState()
    this.stateStore.saveState(this.state)
  }

  // ── Market Data ──────────────────────────────────────────────

  private async fetchTickers(): Promise<Map<string, Ticker>> {
    const tickers = new Map<string, Ticker>()
    const symbols = new Set(this.config.strategy.symbols)
    symbols.add('BTC-PERP') // Always track BTC for macro trend

    // Also track symbols of open slots
    for (const slot of this.state.slots) {
      if (slot.status === 'OPEN' && slot.symbol) {
        symbols.add(slot.symbol)
      }
    }

    const promises = Array.from(symbols).map(async (symbol) => {
      try {
        const ticker = await this.adapter.getTicker(symbol)
        tickers.set(symbol, ticker)
      } catch {
        // Skip symbols that fail to fetch
      }
    })

    await Promise.all(promises)
    return tickers
  }

  // ── ROE Update ───────────────────────────────────────────────

  private updateSlotROE(tickers: Map<string, Ticker>): void {
    for (const slot of this.state.slots) {
      if (slot.status !== 'OPEN' || !slot.symbol) continue

      const ticker = tickers.get(slot.symbol)
      if (!ticker) continue

      const leverage = this.config.apex.leverage
      if (slot.side === 'LONG') {
        slot.currentRoe = ((ticker.lastPrice - slot.entryPrice) / slot.entryPrice) * leverage * 100
      } else {
        slot.currentRoe = ((slot.entryPrice - ticker.lastPrice) / slot.entryPrice) * leverage * 100
      }

      if (slot.currentRoe > slot.peakRoe) {
        slot.peakRoe = slot.currentRoe
      }
    }
  }

  // ── Guard Evaluation ─────────────────────────────────────────

  private async evaluateGuards(tickers: Map<string, Ticker>, now: number): Promise<void> {
    for (const slot of this.state.slots) {
      if (slot.status !== 'OPEN' || !slot.symbol) continue

      const ticker = tickers.get(slot.symbol)
      if (!ticker) continue

      const result = this.guard.evaluate(slot, ticker.lastPrice, now)

      if (result.action === 'EXIT') {
        await this.closeSlot(slot, ticker.lastPrice, result.reason, now)
      } else {
        // Update guard state on slot
        if (result.newTierLevel !== undefined) {
          if (slot.guardPhase === 'PHASE_1' && result.reason === 'graduated_phase2') {
            slot.guardPhase = 'PHASE_2'
          }
          slot.tierLevel = result.newTierLevel
        }
      }
    }
  }

  // ── Close Slot ───────────────────────────────────────────────

  private async closeSlot(
    slot: ApexSlot,
    exitPrice: number,
    reason: string,
    now: number,
  ): Promise<void> {
    slot.status = 'CLOSING'

    // Place market close order
    try {
      const closeSide = slot.side === 'LONG' ? 'SELL' : 'BUY'
      await this.adapter.placeOrder({
        symbol: slot.symbol!,
        side: closeSide,
        size: slot.size,
        price: exitPrice,
        orderType: 'IOC',
        reduceOnly: true,
      })
    } catch {
      // Log: close order failed, position may still be open
      // Still proceed with trade recording since we've committed to closing
    }

    // Cancel any stop loss orders
    try {
      await this.adapter.cancelAllOrders(slot.symbol!)
    } catch {
      // Best effort
    }

    // PnL calculation: size is raw asset quantity (from OrderManager.calcSize),
    // NOT leveraged exposure. The leverage multiplier is NOT applied here because:
    // calcSize: size = (equity * pct/100 * leverage) / price
    //   -> this gives us the leveraged quantity in asset units
    // closeSlot: pnl = priceDelta * size
    //   -> size already includes leverage effect, so do NOT multiply by leverage again
    const pnlPerUnit = slot.side === 'LONG'
      ? (exitPrice - slot.entryPrice)
      : (slot.entryPrice - exitPrice)
    const pnl = pnlPerUnit * slot.size
    const fees = Math.abs(pnl) * 0.001 // Estimate 0.1% fees
    const netPnl = pnl - fees

    // Record trade
    const trade: TradeRecord = {
      id: `${slot.id}-${now}`,
      symbol: slot.symbol!,
      side: slot.side!,
      entryPrice: slot.entryPrice,
      exitPrice,
      size: slot.size,
      entryTime: slot.entryTime,
      exitTime: now,
      pnl,
      fees,
      netPnl,
      exitReason: reason,
      slotId: slot.id,
    }

    this.stateStore.appendTrade(trade)

    // Update risk guardian
    this.riskGuardian.recordTrade(netPnl, now)

    // Reset slot to EMPTY
    const emptySlot = createEmptySlot(slot.id)
    Object.assign(slot, emptySlot)
  }

  // ── Entry Execution ──────────────────────────────────────────

  private async executeEntries(
    candidates: EntryCandidate[],
    tickers: Map<string, Ticker>,
  ): Promise<void> {
    if (!this.riskGuardian.canEnter()) return

    for (const candidate of candidates) {
      // Find an empty slot
      const emptySlot = this.state.slots.find(s => s.status === 'EMPTY')
      if (!emptySlot) break // No more slots available

      if (!this.riskGuardian.canEnter()) break // Re-check after each entry

      const ticker = tickers.get(candidate.symbol)
      if (!ticker) continue

      // Don't enter a symbol we already have an open position in
      const alreadyOpen = this.state.slots.some(
        s => s.status === 'OPEN' && s.symbol === candidate.symbol
      )
      if (alreadyOpen) continue

      // Calculate position size
      let balances
      try {
        balances = await this.adapter.getBalances()
      } catch {
        break // Can't size without balances
      }

      const size = this.orderManager.calcSize(
        balances,
        10, // 10% of equity per position
        ticker.lastPrice,
        this.config.apex.leverage,
      )

      // Check minimum size
      let exchangeInfo
      try {
        exchangeInfo = await this.adapter.getExchangeInfo()
      } catch {
        continue
      }

      const finalSize = this.orderManager.enforceMinSize(size, candidate.symbol, exchangeInfo)
      if (finalSize === null) continue

      // Place entry order
      const entrySide = candidate.direction === 'LONG' ? 'BUY' : 'SELL'
      try {
        const result = await this.orderManager.placeWithFallback(
          {
            symbol: candidate.symbol,
            side: entrySide,
            size: finalSize,
            price: ticker.lastPrice,
            orderType: 'ALO',
          },
          this.adapter,
        )

        if (result.status === 'FILLED' || result.status === 'PARTIAL' || result.status === 'OPEN') {
          const filledSize = result.filledSize > 0 ? result.filledSize : finalSize
          const filledPrice = result.filledPrice > 0 ? result.filledPrice : ticker.lastPrice

          // Fill the slot
          emptySlot.status = 'OPEN'
          emptySlot.symbol = candidate.symbol
          emptySlot.side = candidate.direction
          emptySlot.entryPrice = filledPrice
          emptySlot.size = filledSize
          emptySlot.entryTime = Date.now()
          emptySlot.guardPhase = 'PHASE_1'
          emptySlot.peakRoe = 0
          emptySlot.currentRoe = 0
          emptySlot.tierLevel = 0
        }
      } catch {
        // Entry failed, skip this candidate
        continue
      }
    }
  }

  // ── Radar ────────────────────────────────────────────────────

  private async runRadar(
    tickers: Map<string, Ticker>,
  ): Promise<void> {
    // Determine BTC trend from RSI
    let btcTrend: 'BULLISH' | 'BEARISH' | 'NEUTRAL' = 'NEUTRAL'
    try {
      const btcCandles = await this.adapter.getCandles('BTC-PERP', '1h', 30)
      const rsi = calculateRSI(btcCandles)
      if (rsi > 60) btcTrend = 'BULLISH'
      else if (rsi < 40) btcTrend = 'BEARISH'
    } catch {
      // Use NEUTRAL if we can't get BTC candles
    }

    // Fetch candles for all tracked symbols
    const candlesMap = new Map<string, Candle[]>()
    const symbols = this.config.strategy.symbols

    const promises = symbols.map(async (symbol) => {
      try {
        const candles = await this.adapter.getCandles(symbol, '1h', 30)
        candlesMap.set(symbol, candles)
      } catch {
        // Skip symbols that fail
      }
    })
    await Promise.all(promises)

    const allTickers = symbols
      .map(s => tickers.get(s))
      .filter((t): t is Ticker => t !== undefined)

    this.lastRadarScores = this.radar.scan(allTickers, candlesMap, btcTrend)
  }

  // ── Reconciliation ──────────────────────────────────────────

  private async reconcile(): Promise<void> {
    try {
      const positions = await this.adapter.getPositions()
      await this.adapter.getOpenOrders() // Fetch for future SL sync use

      // Check for orphaned slots: slot says OPEN but no matching exchange position
      for (const slot of this.state.slots) {
        if (slot.status !== 'OPEN' || !slot.symbol) continue

        const hasPosition = positions.some(
          p => p.symbol === slot.symbol && p.side === slot.side
        )

        if (!hasPosition) {
          // Position was closed externally — clean up slot
          const emptySlot = createEmptySlot(slot.id)
          Object.assign(slot, emptySlot)
        }
      }
    } catch {
      // Reconciliation is best-effort
    }
  }

  // ── REFLECT ──────────────────────────────────────────────────

  private runReflect(): void {
    const trades = this.stateStore.loadTrades()
    if (trades.length === 0) return

    const metrics = this.reflect.analyze(trades)
    const adjustments = this.reflect.suggest(metrics)

    if (this.config.reflect.autoAdjust && adjustments.length > 0) {
      // Apply adjustments to risk guardian state
      const newConfig = this.reflect.applyAdjustments(this.config, adjustments)
      // Update mutable config fields
      ;(this.config as { apex: EngineConfig['apex'] }).apex = newConfig.apex
    }
  }

  // ── Signal Priority ──────────────────────────────────────────

  private getSignalPriority(signal: PulseSignal): number {
    const idx = SIGNAL_PRIORITY.indexOf(signal.type)
    if (idx >= 0) return idx

    // Smart money signals with high confidence get priority 2 (between CONTRIB_EXPLOSION and IMMEDIATE_MOVER)
    if (signal.confidence > 90) return 2

    return -1 // Not an entry-worthy signal
  }

  // ── Public Accessors ─────────────────────────────────────────

  getState(): ApexState {
    return JSON.parse(JSON.stringify(this.state)) as ApexState
  }
}
