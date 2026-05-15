// Types
export type {
  Ticker,
  OrderBookLevel,
  OrderBook,
  Candle,
  Balance,
  Position,
  OrderSide,
  OrderType,
  OrderRequest,
  OrderResult,
  OpenOrder,
  StrategyAction,
  StrategyDecision,
  StrategyConfig,
  TickContext,
  ExchangeAdapterInterface,
  ExchangeInfo,
  SlotStatus,
  GuardPhase,
  RiskGate,
  ApexSlot,
  ApexState,
  RiskGuardianState,
  GuardPreset,
  GuardConfig,
  GuardTier,
  RadarScore,
  PulseSignalType,
  PulseSignal,
  TradeRecord,
  ReflectMetrics,
  EngineConfig,
} from './types.js'

export { APEX_PRESETS, GUARD_PRESETS } from './types.js'

// Core
export { ApexOrchestrator } from './core/apex-orchestrator.js'
export { loadConfig, saveConfig, createDefaultConfig, validateConfig } from './core/config.js'
export { OrderManager } from './core/order-manager.js'
export { RiskGuardian } from './core/risk-guardian.js'
export { StateStore, createEmptySlot } from './core/state-store.js'

// Exchanges
export { HyperliquidAdapter } from './exchanges/hyperliquid.js'
export type { HyperliquidConfig } from './exchanges/hyperliquid.js'
export { HlSigner } from './exchanges/hl-signer.js'
export type { HlSignature, HlOrderWire, HlOrderType, HlAction } from './exchanges/hl-signer.js'
export { HlWebSocket } from './exchanges/hl-websocket.js'
export type { HlWebSocketConfig, SubscriptionEntry } from './exchanges/hl-websocket.js'
export { BinanceAdapter } from './exchanges/binance.js'
export type { BinanceConfig } from './exchanges/binance.js'
export { BnSigner } from './exchanges/bn-signer.js'
export { BnWebSocket } from './exchanges/bn-websocket.js'
export type { BnWebSocketConfig } from './exchanges/bn-websocket.js'
export { AlpacaAdapter } from './exchanges/alpaca.js'
export type { AlpacaConfig } from './exchanges/alpaca.js'
export { getCurrentSession, isMarketOpen, getNextMarketOpen, canTrade } from './exchanges/alpaca-market-hours.js'
export type { MarketSession, MarketCalendarDay } from './exchanges/alpaca-market-hours.js'

// Guard
export { Guard, DEFAULT_LEVERAGE } from './guard/trailing-stop.js'
export type { GuardResult } from './guard/trailing-stop.js'

// Signals
export { Pulse } from './signals/pulse.js'
export { Radar, calculateRSI, calculateEMA } from './signals/radar.js'

// Reflect
export { ReflectAnalyzer } from './reflect/analyzer.js'
export type { ReflectAdjustment } from './reflect/analyzer.js'

// Strategies — Base + Registry
export { BaseStrategy } from './strategies/base-strategy.js'
export { createStrategy, listStrategies } from './strategies/registry.js'

// Strategies — Market Making
export { SimpleMM } from './strategies/mm/simple-mm.js'
export { AvellanedaMM } from './strategies/mm/avellaneda-mm.js'
export { EngineMM } from './strategies/mm/engine-mm.js'
export { RegimeMM } from './strategies/mm/regime-mm.js'
export { GridMM } from './strategies/mm/grid-mm.js'
export { LiquidationMM } from './strategies/mm/liquidation-mm.js'

// Strategies — Arbitrage
export { FundingArb } from './strategies/arb/funding-arb.js'
export { BasisArb } from './strategies/arb/basis-arb.js'

// Strategies — Signal/Directional
export { MomentumBreakout, computeATR } from './strategies/signal/momentum-breakout.js'
export { MeanReversion, computeSMA, computeStdDev, computeBollingerBands } from './strategies/signal/mean-reversion.js'
export { AggressiveTaker } from './strategies/signal/aggressive-taker.js'
export type { ConvictionFactors, ConvictionResult } from './strategies/signal/aggressive-taker.js'

// Strategies — LLM
export { LlmCustom } from './strategies/llm-custom.js'

// Exchanges — Polymarket
export { PolymarketAdapter } from './exchanges/polymarket.js'
export type { PolymarketConfig } from './exchanges/polymarket.js'

// Strategies — Prediction Markets
export { PredictionMM, computeFairValue, computeInventorySkew, generateQuotes } from './strategies/prediction/prediction-mm.js'

// Exchanges — MCP Bridge
export { McpBridgeAdapter } from './exchanges/mcp-bridge.js'
export type { McpToolCaller, McpToolMapping, McpBridgeConfig } from './exchanges/mcp-bridge.js'

// Exchanges — KRX Calendar
export { getCurrentKrxSession, isKrxMarketOpen, isKrxHoliday, getKrxHolidayName, getNextKrxMarketOpen, canTradeKrx } from './exchanges/krx-calendar.js'
export type { KrxSession, KrxHoliday } from './exchanges/krx-calendar.js'

// Exchanges — 키움증권
export { KiumAdapter, parseKoreanNumber } from './exchanges/kium.js'
export type { KiumConfig, KiumComBridge, KiumOrderParams } from './exchanges/kium.js'

// Exchanges — 한국투자증권 (MCP)
export { KisMcpAdapter } from './exchanges/kis-mcp.js'
export type { KisMcpConfig } from './exchanges/kis-mcp.js'
