// === Market Data ===
export interface Ticker {
  symbol: string
  mid: number
  bid: number
  ask: number
  lastPrice: number
  volume24h: number
  openInterest: number
  fundingRate: number
  timestamp: number
}

export interface OrderBookLevel {
  price: number
  size: number
}

export interface OrderBook {
  symbol: string
  bids: OrderBookLevel[]
  asks: OrderBookLevel[]
  timestamp: number
}

export interface Candle {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// === Account ===
export interface Balance {
  currency: string
  available: number
  total: number
  unrealizedPnl: number
}

export interface Position {
  symbol: string
  side: 'LONG' | 'SHORT'
  size: number
  entryPrice: number
  markPrice: number
  unrealizedPnl: number
  leverage: number
  liquidationPrice: number | null
}

// === Orders ===
export type OrderSide = 'BUY' | 'SELL'
export type OrderType = 'ALO' | 'GTC' | 'IOC'

export interface OrderRequest {
  symbol: string
  side: OrderSide
  size: number
  price: number
  orderType: OrderType
  reduceOnly?: boolean
  clientOrderId?: string
}

export interface OrderResult {
  orderId: string
  status: 'FILLED' | 'PARTIAL' | 'OPEN' | 'REJECTED'
  filledSize: number
  filledPrice: number
  timestamp: number
}

export interface OpenOrder {
  orderId: string
  symbol: string
  side: OrderSide
  price: number
  size: number
  filledSize: number
  orderType: OrderType
  timestamp: number
}

// === Strategy ===
export type StrategyAction = 'BUY' | 'SELL' | 'HOLD'

export interface StrategyDecision {
  action: StrategyAction
  symbol: string
  size: number
  orderType: OrderType
  confidence: number
  reason: string
  stopLoss?: number
  takeProfit?: number
}

export interface StrategyConfig {
  name: string
  symbols: string[]
  params: Record<string, number | string | boolean>
  strategyPrompt?: string
}

export interface TickContext {
  adapter: ExchangeAdapterInterface
  positions: Position[]
  balances: Balance[]
  ticker: Ticker
  orderBook: OrderBook
  candles: Candle[]
  config: StrategyConfig
  tickNumber: number
  timestamp: number
}

// === Exchange Adapter Interface ===
export interface ExchangeAdapterInterface {
  name: string
  getTicker(symbol: string): Promise<Ticker>
  getOrderBook(symbol: string, depth?: number): Promise<OrderBook>
  getCandles(symbol: string, interval: string, limit: number): Promise<Candle[]>
  getBalances(): Promise<Balance[]>
  getPositions(): Promise<Position[]>
  placeOrder(order: OrderRequest): Promise<OrderResult>
  cancelOrder(orderId: string): Promise<void>
  cancelAllOrders(symbol?: string): Promise<void>
  setStopLoss(symbol: string, side: OrderSide, triggerPrice: number, size: number): Promise<OrderResult>
  getOpenOrders(symbol?: string): Promise<OpenOrder[]>
  getExchangeInfo(): Promise<ExchangeInfo>
}

export interface ExchangeInfo {
  name: string
  testnet: boolean
  supportedSymbols: string[]
  minOrderSizes: Record<string, number>
  tickSizes: Record<string, number>
}

// === APEX ===
export type SlotStatus = 'EMPTY' | 'OPEN' | 'CLOSING' | 'CLOSED'
export type GuardPhase = 'PHASE_1' | 'PHASE_2'
export type RiskGate = 'OPEN' | 'COOLDOWN' | 'CLOSED'

export interface ApexSlot {
  id: number
  status: SlotStatus
  symbol: string | null
  side: 'LONG' | 'SHORT' | null
  entryPrice: number
  size: number
  entryTime: number
  guardPhase: GuardPhase
  peakRoe: number
  currentRoe: number
  tierLevel: number
  exchangeSlOrderId?: string
  closedAt?: number
  closedReason?: string
}

export interface ApexState {
  slots: ApexSlot[]
  tickNumber: number
  startedAt: number
  lastRadarScan: number
  lastReflect: number
  riskGuardian: RiskGuardianState
}

export interface RiskGuardianState {
  gate: RiskGate
  consecutiveLosses: number
  dailyPnl: number
  dailyLossLimit: number
  cooldownExpiresAt: number | null
  lastResetDate: string
}

// === Guard ===
export type GuardPreset = 'moderate' | 'tight'

export interface GuardConfig {
  preset: GuardPreset
  phase1RetracePct: number
  phase1MaxDurationMs: number
  phase1WeakPeakRoe: number
  phase1WeakPeakDurationMs: number
  tiers: GuardTier[]
  stagnationTp: boolean
  stagnationRoe: number
  stagnationDurationMs: number
  leverage?: number
}

export interface GuardTier {
  roePct: number
  floorPct: number
}

// === Signals ===
export interface RadarScore {
  symbol: string
  total: number
  marketStructure: number
  technicals: number
  funding: number
  btcMacro: number
  direction: 'LONG' | 'SHORT'
  timestamp: number
}

export type PulseSignalType =
  | 'FIRST_JUMP'
  | 'CONTRIB_EXPLOSION'
  | 'IMMEDIATE_MOVER'
  | 'NEW_ENTRY_DEEP'
  | 'DEEP_CLIMBER'
  | 'VOLUME_SURGE'
  | 'OI_BREAKOUT'
  | 'FUNDING_FLIP'

export interface PulseSignal {
  symbol: string
  type: PulseSignalType
  confidence: number
  direction: 'LONG' | 'SHORT'
  data: {
    oiChangePct?: number
    volumeMultiple?: number
    fundingRate?: number
    priceChangePct?: number
  }
  timestamp: number
}

// === REFLECT ===
export interface TradeRecord {
  id: string
  symbol: string
  side: 'LONG' | 'SHORT'
  entryPrice: number
  exitPrice: number
  size: number
  entryTime: number
  exitTime: number
  pnl: number
  fees: number
  netPnl: number
  exitReason: string
  slotId: number
}

export interface ReflectMetrics {
  totalTrades: number
  winRate: number
  netPnl: number
  grossWins: number
  grossLosses: number
  fdr: number
  avgHoldingPeriodMs: number
  longestWinStreak: number
  longestLoseStreak: number
  monsterDependency: number
  directionSplit: {
    longWinRate: number
    shortWinRate: number
    longPnl: number
    shortPnl: number
  }
  periodStart: number
  periodEnd: number
}

// === Engine Config ===
export interface EngineConfig {
  exchange: {
    name: 'hyperliquid' | 'binance' | 'alpaca' | 'polymarket' | 'kium' | 'kis'
    testnet: boolean
  }
  apex: {
    preset: 'conservative' | 'default' | 'aggressive'
    maxSlots: number
    leverage: number
    radarThreshold: number
    dailyLossLimit: number
    tickIntervalMs: number
  }
  guard: GuardConfig
  strategy: StrategyConfig
  reflect: {
    autoAdjust: boolean
    intervalTicks: number
  }
}

// === Presets ===
export const APEX_PRESETS: Record<string, Partial<EngineConfig['apex']>> = {
  conservative: { maxSlots: 2, leverage: 5, radarThreshold: 190, dailyLossLimit: 250 },
  default: { maxSlots: 3, leverage: 10, radarThreshold: 170, dailyLossLimit: 500 },
  aggressive: { maxSlots: 3, leverage: 15, radarThreshold: 150, dailyLossLimit: 1000 },
}

export const GUARD_PRESETS: Record<GuardPreset, GuardConfig> = {
  moderate: {
    preset: 'moderate',
    phase1RetracePct: 3,
    phase1MaxDurationMs: 90 * 60_000,
    phase1WeakPeakRoe: 3,
    phase1WeakPeakDurationMs: 45 * 60_000,
    tiers: [
      { roePct: 10, floorPct: 5 },
      { roePct: 20, floorPct: 12 },
      { roePct: 35, floorPct: 22 },
      { roePct: 50, floorPct: 35 },
      { roePct: 75, floorPct: 55 },
      { roePct: 100, floorPct: 75 },
    ],
    stagnationTp: false,
    stagnationRoe: 0,
    stagnationDurationMs: 0,
  },
  tight: {
    preset: 'tight',
    phase1RetracePct: 5,
    phase1MaxDurationMs: 90 * 60_000,
    phase1WeakPeakRoe: 3,
    phase1WeakPeakDurationMs: 45 * 60_000,
    tiers: [
      { roePct: 10, floorPct: 5 },
      { roePct: 25, floorPct: 15 },
      { roePct: 50, floorPct: 35 },
      { roePct: 75, floorPct: 55 },
    ],
    stagnationTp: true,
    stagnationRoe: 8,
    stagnationDurationMs: 60 * 60_000,
  },
}
