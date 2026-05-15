// src/types.ts
var APEX_PRESETS = {
  conservative: { maxSlots: 2, leverage: 5, radarThreshold: 190, dailyLossLimit: 250 },
  default: { maxSlots: 3, leverage: 10, radarThreshold: 170, dailyLossLimit: 500 },
  aggressive: { maxSlots: 3, leverage: 15, radarThreshold: 150, dailyLossLimit: 1e3 }
};
var GUARD_PRESETS = {
  moderate: {
    preset: "moderate",
    phase1RetracePct: 3,
    phase1MaxDurationMs: 90 * 6e4,
    phase1WeakPeakRoe: 3,
    phase1WeakPeakDurationMs: 45 * 6e4,
    tiers: [
      { roePct: 10, floorPct: 5 },
      { roePct: 20, floorPct: 12 },
      { roePct: 35, floorPct: 22 },
      { roePct: 50, floorPct: 35 },
      { roePct: 75, floorPct: 55 },
      { roePct: 100, floorPct: 75 }
    ],
    stagnationTp: false,
    stagnationRoe: 0,
    stagnationDurationMs: 0
  },
  tight: {
    preset: "tight",
    phase1RetracePct: 5,
    phase1MaxDurationMs: 90 * 6e4,
    phase1WeakPeakRoe: 3,
    phase1WeakPeakDurationMs: 45 * 6e4,
    tiers: [
      { roePct: 10, floorPct: 5 },
      { roePct: 25, floorPct: 15 },
      { roePct: 50, floorPct: 35 },
      { roePct: 75, floorPct: 55 }
    ],
    stagnationTp: true,
    stagnationRoe: 8,
    stagnationDurationMs: 60 * 6e4
  }
};

// src/core/state-store.ts
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync, appendFileSync } from "node:fs";
import { join } from "node:path";
function createEmptySlot(id) {
  return {
    id,
    status: "EMPTY",
    symbol: null,
    side: null,
    entryPrice: 0,
    size: 0,
    entryTime: 0,
    guardPhase: "PHASE_1",
    peakRoe: 0,
    currentRoe: 0,
    tierLevel: 0
  };
}
function createDefaultState() {
  const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  const riskGuardian = {
    gate: "OPEN",
    consecutiveLosses: 0,
    dailyPnl: 0,
    dailyLossLimit: 500,
    cooldownExpiresAt: null,
    lastResetDate: today
  };
  return {
    slots: [createEmptySlot(0), createEmptySlot(1), createEmptySlot(2)],
    tickNumber: 0,
    startedAt: Date.now(),
    lastRadarScan: 0,
    lastReflect: 0,
    riskGuardian
  };
}
var StateStore = class {
  constructor(dataDir) {
    this.dataDir = dataDir;
    if (!existsSync(dataDir)) {
      mkdirSync(dataDir, { recursive: true });
    }
    this.stateFile = join(dataDir, "state.json");
    this.tradesFile = join(dataDir, "trades.jsonl");
  }
  stateFile;
  tradesFile;
  loadState() {
    if (!existsSync(this.stateFile)) {
      return createDefaultState();
    }
    const raw = readFileSync(this.stateFile, "utf-8");
    return JSON.parse(raw);
  }
  saveState(state) {
    const tmpFile = join(this.dataDir, "state.json.tmp");
    writeFileSync(tmpFile, JSON.stringify(state, null, 2), "utf-8");
    renameSync(tmpFile, this.stateFile);
  }
  appendTrade(trade) {
    appendFileSync(this.tradesFile, JSON.stringify(trade) + "\n", "utf-8");
  }
  loadTrades(since) {
    if (!existsSync(this.tradesFile)) {
      return [];
    }
    const raw = readFileSync(this.tradesFile, "utf-8");
    const lines = raw.trim().split("\n").filter(Boolean);
    const trades = lines.map((line) => JSON.parse(line));
    if (since !== void 0) {
      return trades.filter((t) => t.exitTime >= since);
    }
    return trades;
  }
};

// src/core/risk-guardian.ts
var COOLDOWN_DURATION_MS = 30 * 60 * 1e3;
function todayUTC() {
  return (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
}
function dateStringFromMs(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}
var RiskGuardian = class {
  state;
  constructor(state) {
    this.state = state ? { ...state } : {
      gate: "OPEN",
      consecutiveLosses: 0,
      dailyPnl: 0,
      dailyLossLimit: 500,
      cooldownExpiresAt: null,
      lastResetDate: todayUTC()
    };
  }
  canEnter() {
    return this.state.gate === "OPEN";
  }
  canExit() {
    return this.state.gate !== "CLOSED";
  }
  recordTrade(pnl, now) {
    const timestamp = now ?? Date.now();
    this.state.dailyPnl += pnl;
    if (pnl < 0) {
      this.state.consecutiveLosses++;
    } else {
      this.state.consecutiveLosses = 0;
    }
    if (this.state.dailyPnl <= -this.state.dailyLossLimit) {
      this.state.gate = "CLOSED";
      this.state.cooldownExpiresAt = null;
      return;
    }
    if (this.state.consecutiveLosses >= 2) {
      this.transitionToCooldown(timestamp);
      return;
    }
    if (-this.state.dailyPnl >= this.state.dailyLossLimit * 0.5 && this.state.gate === "OPEN") {
      this.transitionToCooldown(timestamp);
      return;
    }
  }
  tick(now) {
    const currentDate = dateStringFromMs(now);
    if (this.state.lastResetDate !== currentDate) {
      this.state.gate = "OPEN";
      this.state.consecutiveLosses = 0;
      this.state.dailyPnl = 0;
      this.state.cooldownExpiresAt = null;
      this.state.lastResetDate = currentDate;
      return;
    }
    if (this.state.gate === "COOLDOWN" && this.state.cooldownExpiresAt !== null && now >= this.state.cooldownExpiresAt) {
      this.state.gate = "OPEN";
      this.state.cooldownExpiresAt = null;
    }
  }
  getState() {
    return { ...this.state };
  }
  transitionToCooldown(now) {
    this.state.gate = "COOLDOWN";
    this.state.cooldownExpiresAt = now + COOLDOWN_DURATION_MS;
  }
};

// src/guard/trailing-stop.ts
var DEFAULT_LEVERAGE = 10;
var Guard = class {
  constructor(config) {
    this.config = config;
  }
  lastTierChangeTime = null;
  evaluate(slot, currentPrice, now) {
    const leverage = this.config.leverage ?? DEFAULT_LEVERAGE;
    const entryPrice = slot.entryPrice;
    const currentROE = slot.side === "LONG" ? (currentPrice - entryPrice) / entryPrice * leverage * 100 : (entryPrice - currentPrice) / entryPrice * leverage * 100;
    const peakRoe = Math.max(slot.peakRoe, currentROE);
    const elapsed = now - slot.entryTime;
    if (slot.guardPhase === "PHASE_2") {
      return this.evaluatePhase2(slot, currentROE, peakRoe, now);
    }
    if (currentROE < -this.config.phase1RetracePct) {
      return {
        action: "EXIT",
        reason: "phase1_retrace",
        slPrice: this.getSLPrice(slot)
      };
    }
    if (elapsed > this.config.phase1MaxDurationMs && slot.tierLevel < 0) {
      return {
        action: "EXIT",
        reason: "phase1_stagnation",
        slPrice: this.getSLPrice(slot)
      };
    }
    if (peakRoe < this.config.phase1WeakPeakRoe && elapsed > this.config.phase1WeakPeakDurationMs) {
      return {
        action: "EXIT",
        reason: "phase1_weak_peak",
        slPrice: this.getSLPrice(slot)
      };
    }
    const firstTier = this.config.tiers[0];
    if (firstTier && currentROE >= firstTier.roePct) {
      this.lastTierChangeTime = now;
      return {
        action: "HOLD",
        reason: "graduated_phase2",
        newTierLevel: 0
      };
    }
    return {
      action: "HOLD",
      reason: "phase1_ok"
    };
  }
  evaluatePhase2(slot, currentROE, peakRoe, now) {
    let newTierLevel = slot.tierLevel;
    for (let i = 0; i < this.config.tiers.length; i++) {
      const tier = this.config.tiers[i];
      if (currentROE >= tier.roePct) {
        if (i > newTierLevel) {
          newTierLevel = i;
          this.lastTierChangeTime = now;
        }
      }
    }
    const currentTier = this.config.tiers[newTierLevel];
    if (!currentTier) {
      return { action: "HOLD", reason: "phase2_ok", newTierLevel };
    }
    const floorPct = currentTier.floorPct;
    if (currentROE < floorPct) {
      return {
        action: "EXIT",
        reason: "tier_floor_breach",
        slPrice: this.getSLPrice(slot)
      };
    }
    if (this.config.stagnationTp && currentROE >= this.config.stagnationRoe) {
      const lastChange = this.lastTierChangeTime ?? slot.entryTime;
      const stagnationElapsed = now - lastChange;
      if (stagnationElapsed > this.config.stagnationDurationMs) {
        return {
          action: "EXIT",
          reason: "stagnation_tp",
          newTierLevel
        };
      }
    }
    return {
      action: "HOLD",
      reason: "phase2_ok",
      newTierLevel
    };
  }
  getSLPrice(slot) {
    const leverage = this.config.leverage ?? DEFAULT_LEVERAGE;
    const entryPrice = slot.entryPrice;
    if (slot.guardPhase === "PHASE_1" || slot.tierLevel < 0) {
      const retraceFraction = this.config.phase1RetracePct / 100 / leverage;
      if (slot.side === "LONG") {
        return entryPrice * (1 - retraceFraction);
      } else {
        return entryPrice * (1 + retraceFraction);
      }
    }
    const tier = this.config.tiers[slot.tierLevel];
    if (!tier) {
      const retraceFraction = this.config.phase1RetracePct / 100 / leverage;
      if (slot.side === "LONG") {
        return entryPrice * (1 - retraceFraction);
      } else {
        return entryPrice * (1 + retraceFraction);
      }
    }
    const floorFraction = tier.floorPct / 100 / leverage;
    if (slot.side === "LONG") {
      return entryPrice * (1 + floorFraction);
    } else {
      return entryPrice * (1 - floorFraction);
    }
  }
};

// src/core/order-manager.ts
var OrderManager = class {
  /**
   * Convert a StrategyDecision into an OrderRequest and execute it.
   * Returns null for HOLD decisions.
   * Uses bid price for BUY ALO (post on bid side) and ask price for SELL ALO.
   * Uses mid price for non-ALO order types.
   */
  async executeDecision(decision, adapter, ticker) {
    if (decision.action === "HOLD") {
      return null;
    }
    const side = decision.action === "BUY" ? "BUY" : "SELL";
    const price = this.resolvePrice(side, decision.orderType, ticker);
    const order = {
      symbol: decision.symbol,
      side,
      size: decision.size,
      price,
      orderType: decision.orderType
    };
    return this.placeWithFallback(order, adapter);
  }
  /**
   * Resolve the order price based on side and order type.
   * ALO BUY → bid (post on bid side to be a maker)
   * ALO SELL → ask (post on ask side to be a maker)
   * GTC / IOC → mid price
   */
  resolvePrice(side, orderType, ticker) {
    if (orderType === "ALO") {
      return side === "BUY" ? ticker.bid : ticker.ask;
    }
    return ticker.mid;
  }
  /**
   * Try to place an ALO order first. If rejected, retry with GTC.
   * Preserves all other order fields; only the orderType changes.
   */
  async placeWithFallback(order, adapter) {
    const result = await adapter.placeOrder(order);
    if (result.status === "REJECTED" && order.orderType === "ALO") {
      const gtcOrder = { ...order, orderType: "GTC" };
      return adapter.placeOrder(gtcOrder);
    }
    return result;
  }
  /**
   * Place / update an exchange-native stop-loss trigger for a position.
   */
  async syncStopLoss(symbol, side, triggerPrice, size, adapter) {
    return adapter.setStopLoss(symbol, side, triggerPrice, size);
  }
  /**
   * Cancel all outstanding stop-loss orders for a given symbol.
   */
  async cancelStopLoss(symbol, adapter) {
    await adapter.cancelAllOrders(symbol);
  }
  /**
   * Calculate position size (in asset units) from a percentage of equity.
   *
   * equity   = sum of all balances' total values
   * notional = equity * (pct / 100)
   * size     = (notional * leverage) / price
   */
  calcSize(balances, pct, price, leverage) {
    const equity = balances.reduce((acc, b) => acc + b.total, 0);
    const notional = equity * (pct / 100);
    return notional * leverage / price;
  }
  /**
   * Enforce minimum order size from exchange info.
   * Returns the size if it meets the minimum, or null if it is below the minimum.
   * If no minimum is defined for the symbol, the size is returned unchanged.
   */
  enforceMinSize(size, symbol, exchangeInfo) {
    const minSize = exchangeInfo.minOrderSizes[symbol];
    if (minSize !== void 0 && size < minSize) {
      return null;
    }
    return size;
  }
};

// src/signals/pulse.ts
var MAX_WINDOW = 5;
var CONFIDENCE = {
  FIRST_JUMP: 100,
  CONTRIB_EXPLOSION: 95,
  IMMEDIATE_MOVER: 80,
  VOLUME_SURGE: 70,
  NEW_ENTRY_DEEP: 65,
  OI_BREAKOUT: 60,
  DEEP_CLIMBER: 55,
  FUNDING_FLIP: 50
};
var TIER_ORDER = [
  "FUNDING_FLIP",
  "DEEP_CLIMBER",
  "OI_BREAKOUT",
  "NEW_ENTRY_DEEP",
  "VOLUME_SURGE",
  "IMMEDIATE_MOVER",
  "CONTRIB_EXPLOSION",
  "FIRST_JUMP"
];
var DEFAULT_SECTORS = {
  L1: ["BTC", "ETH", "SOL"],
  DEFI: ["UNI", "AAVE", "MKR"],
  MEME: ["DOGE", "SHIB", "PEPE"],
  L2: ["ARB", "OP", "MATIC"],
  AI: ["FET", "RENDER", "TAO"]
};
var Pulse = class {
  snapshots;
  sectorMap;
  sectorSignalHistory;
  // sector → last scan number with a signal
  scanCount;
  constructor() {
    this.snapshots = /* @__PURE__ */ new Map();
    this.sectorMap = /* @__PURE__ */ new Map();
    this.sectorSignalHistory = /* @__PURE__ */ new Map();
    this.scanCount = 0;
    for (const [sector, symbols] of Object.entries(DEFAULT_SECTORS)) {
      this.sectorMap.set(sector, symbols);
    }
  }
  scan(tickers) {
    const now = Date.now();
    this.scanCount++;
    const signals = [];
    for (const ticker of tickers) {
      const current = {
        symbol: ticker.symbol,
        openInterest: ticker.openInterest,
        volume24h: ticker.volume24h,
        fundingRate: ticker.fundingRate,
        lastPrice: ticker.lastPrice,
        timestamp: ticker.timestamp
      };
      const history = this.snapshots.get(ticker.symbol);
      if (!history || history.length === 0) {
        this.snapshots.set(ticker.symbol, [current]);
        continue;
      }
      const prev = history[history.length - 1];
      const signal = this.detectSignal(current, prev, history, ticker.timestamp);
      history.push(current);
      if (history.length > MAX_WINDOW) {
        history.shift();
      }
      if (signal) {
        signals.push(signal);
      }
    }
    this.applyFirstJump(signals);
    signals.sort((a, b) => b.confidence - a.confidence);
    return { signals, timestamp: now };
  }
  reset() {
    this.snapshots.clear();
    this.sectorSignalHistory.clear();
    this.scanCount = 0;
  }
  detectSignal(current, prev, history, timestamp) {
    const oiChangePct = (current.openInterest - prev.openInterest) / prev.openInterest * 100;
    const volumeMultiple = prev.volume24h > 0 ? current.volume24h / prev.volume24h : 0;
    const fundingChange = current.fundingRate - prev.fundingRate;
    const priceChangePct = (current.lastPrice - prev.lastPrice) / prev.lastPrice * 100;
    const direction = priceChangePct > 0 ? "LONG" : "SHORT";
    const data = {
      oiChangePct,
      volumeMultiple,
      fundingRate: current.fundingRate,
      priceChangePct
    };
    const matched = [];
    if (oiChangePct >= 15 && volumeMultiple >= 5) {
      matched.push("CONTRIB_EXPLOSION");
    }
    if (oiChangePct >= 15 || volumeMultiple >= 5) {
      matched.push("IMMEDIATE_MOVER");
    }
    if (oiChangePct >= 8 && volumeMultiple < 2) {
      matched.push("NEW_ENTRY_DEEP");
    }
    if (oiChangePct >= 5 && this.hasConsecutiveOiClimbs(current.symbol, history, current, 3)) {
      matched.push("DEEP_CLIMBER");
    }
    if (volumeMultiple >= 3) {
      matched.push("VOLUME_SURGE");
    }
    if (oiChangePct >= 8) {
      matched.push("OI_BREAKOUT");
    }
    if (prev.fundingRate !== 0 && Math.abs(fundingChange) >= Math.abs(prev.fundingRate) * 0.5) {
      matched.push("FUNDING_FLIP");
    }
    if (matched.length === 0) {
      return null;
    }
    const best = this.highestTier(matched);
    return {
      symbol: current.symbol,
      type: best,
      confidence: CONFIDENCE[best],
      direction,
      data,
      timestamp
    };
  }
  highestTier(types) {
    let bestIdx = -1;
    let bestType = types[0];
    for (const t of types) {
      const idx = TIER_ORDER.indexOf(t);
      if (idx > bestIdx) {
        bestIdx = idx;
        bestType = t;
      }
    }
    return bestType;
  }
  hasConsecutiveOiClimbs(_symbol, history, current, required) {
    if (history.length < required - 1) {
      return false;
    }
    let consecutiveCount = 1;
    for (let i = history.length - 1; i >= 1; i--) {
      const curr = history[i];
      const prev = history[i - 1];
      const changePct = (curr.openInterest - prev.openInterest) / prev.openInterest * 100;
      if (changePct >= 5) {
        consecutiveCount++;
        if (consecutiveCount >= required) {
          return true;
        }
      } else {
        break;
      }
    }
    return consecutiveCount >= required;
  }
  applyFirstJump(signals) {
    for (let i = 0; i < signals.length; i++) {
      const signal = signals[i];
      const tierIdx = TIER_ORDER.indexOf(signal.type);
      const immediateMoverIdx = TIER_ORDER.indexOf("IMMEDIATE_MOVER");
      if (tierIdx < immediateMoverIdx) {
        continue;
      }
      const sector = this.findSector(signal.symbol);
      if (!sector) {
        continue;
      }
      const lastSectorScan = this.sectorSignalHistory.get(sector);
      if (lastSectorScan === void 0 || this.scanCount - lastSectorScan > 3) {
        signals[i] = {
          ...signal,
          type: "FIRST_JUMP",
          confidence: CONFIDENCE["FIRST_JUMP"]
        };
        this.sectorSignalHistory.set(sector, this.scanCount);
      } else {
        this.sectorSignalHistory.set(sector, this.scanCount);
      }
    }
  }
  findSector(symbol) {
    const baseCoin = symbol.replace(/-(?:PERP|USD|USDT)$/, "");
    for (const [sector, symbols] of this.sectorMap) {
      if (symbols.includes(baseCoin)) {
        return sector;
      }
    }
    return null;
  }
};

// src/signals/radar.ts
function calculateRSI(candles, period = 14) {
  if (candles.length < period + 1) {
    return 50;
  }
  const changes = [];
  for (let i = 1; i < candles.length; i++) {
    changes.push(candles[i].close - candles[i - 1].close);
  }
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 0; i < period; i++) {
    const change = changes[i];
    if (change > 0) {
      avgGain += change;
    } else {
      avgLoss += Math.abs(change);
    }
  }
  avgGain /= period;
  avgLoss /= period;
  for (let i = period; i < changes.length; i++) {
    const change = changes[i];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? Math.abs(change) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
  }
  if (avgLoss === 0) {
    return avgGain === 0 ? 50 : 100;
  }
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}
function calculateEMA(values, period) {
  if (values.length === 0) {
    return 0;
  }
  const k = 2 / (period + 1);
  let ema = values[0];
  for (let i = 1; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
  }
  return ema;
}
var Radar = class {
  constructor() {
  }
  scoreSymbol(ticker, candles, btcTrend) {
    const rsi = calculateRSI(candles);
    const closes = candles.map((c) => c.close);
    const ema12 = calculateEMA(closes, 12);
    const ema26 = calculateEMA(closes, 26);
    const direction = this.determineDirection(rsi, ema12, ema26);
    const marketStructure = this.scoreMarketStructure(ticker);
    const technicals = this.scoreTechnicals(rsi, ema12, ema26, candles, direction);
    const funding = this.scoreFunding(ticker.fundingRate, direction);
    const btcMacro = this.scoreBtcMacro(btcTrend, direction);
    const total = marketStructure + technicals + funding + btcMacro;
    return {
      symbol: ticker.symbol,
      total,
      marketStructure,
      technicals,
      funding,
      btcMacro,
      direction,
      timestamp: Date.now()
    };
  }
  scan(tickers, candlesMap, btcTrend, topN = 10) {
    const scores = [];
    for (const ticker of tickers) {
      const candles = candlesMap.get(ticker.symbol);
      if (!candles || candles.length === 0) {
        continue;
      }
      scores.push(this.scoreSymbol(ticker, candles, btcTrend));
    }
    scores.sort((a, b) => b.total - a.total);
    return scores.slice(0, topN);
  }
  determineDirection(rsi, ema12, ema26) {
    if (rsi < 40) return "LONG";
    if (rsi > 60) return "SHORT";
    return ema12 > ema26 ? "LONG" : "SHORT";
  }
  scoreMarketStructure(ticker) {
    let volumeScore;
    if (ticker.volume24h > 1e8) volumeScore = 50;
    else if (ticker.volume24h > 5e7) volumeScore = 40;
    else if (ticker.volume24h > 1e7) volumeScore = 30;
    else if (ticker.volume24h > 1e6) volumeScore = 15;
    else volumeScore = 0;
    let oiScore;
    if (ticker.openInterest > 1e8) oiScore = 50;
    else if (ticker.openInterest > 5e7) oiScore = 40;
    else if (ticker.openInterest > 1e7) oiScore = 30;
    else if (ticker.openInterest > 1e6) oiScore = 15;
    else oiScore = 0;
    const spreadPct = (ticker.ask - ticker.bid) / ticker.mid * 100;
    let spreadScore;
    if (spreadPct < 0.01) spreadScore = 40;
    else if (spreadPct < 0.05) spreadScore = 30;
    else if (spreadPct < 0.1) spreadScore = 20;
    else if (spreadPct < 0.5) spreadScore = 10;
    else spreadScore = 0;
    return volumeScore + oiScore + spreadScore;
  }
  scoreTechnicals(rsi, ema12, ema26, candles, direction) {
    let rsiScore;
    if (rsi < 30) rsiScore = 40;
    else if (rsi < 35) rsiScore = 30;
    else if (rsi > 70) rsiScore = 40;
    else if (rsi > 65) rsiScore = 30;
    else rsiScore = 10;
    let emaScore;
    if (ema12 > ema26) {
      emaScore = direction === "LONG" ? 40 : 10;
    } else {
      emaScore = direction === "SHORT" ? 40 : 10;
    }
    let trendScore = 15;
    if (candles.length >= 4) {
      const last4 = candles.slice(-4);
      const allUp = last4.every((c) => c.close >= c.open);
      const allDown = last4.every((c) => c.close <= c.open);
      if (allUp) trendScore = 40;
      else if (allDown) trendScore = 40;
    }
    return rsiScore + emaScore + trendScore;
  }
  scoreFunding(fundingRate, direction) {
    const absFunding = Math.abs(fundingRate);
    let magnitudeScore;
    if (absFunding > 0.01) magnitudeScore = 40;
    else if (absFunding > 5e-3) magnitudeScore = 30;
    else if (absFunding > 1e-3) magnitudeScore = 20;
    else magnitudeScore = 5;
    let biasScore;
    if (fundingRate < 0 && direction === "LONG") {
      biasScore = 40;
    } else if (fundingRate > 0 && direction === "SHORT") {
      biasScore = 40;
    } else {
      biasScore = 10;
    }
    return magnitudeScore + biasScore;
  }
  scoreBtcMacro(btcTrend, direction) {
    if (btcTrend === "NEUTRAL") return 30;
    if (btcTrend === "BULLISH" && direction === "LONG") return 60;
    if (btcTrend === "BEARISH" && direction === "SHORT") return 60;
    return 10;
  }
};

// src/reflect/analyzer.ts
var GUARDRAILS = {
  radarThreshold: { min: 130, max: 250 },
  dailyLossLimit: { min: 100, max: 2e3 }
};
function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}
var ReflectAnalyzer = class {
  analyze(trades) {
    if (trades.length === 0) {
      return {
        totalTrades: 0,
        winRate: 0,
        netPnl: 0,
        grossWins: 0,
        grossLosses: 0,
        fdr: 0,
        avgHoldingPeriodMs: 0,
        longestWinStreak: 0,
        longestLoseStreak: 0,
        monsterDependency: 0,
        directionSplit: {
          longWinRate: 0,
          shortWinRate: 0,
          longPnl: 0,
          shortPnl: 0
        },
        periodStart: 0,
        periodEnd: 0
      };
    }
    const totalTrades = trades.length;
    const wins = trades.filter((t) => t.netPnl > 0);
    const losses = trades.filter((t) => t.netPnl <= 0);
    const winRate = wins.length / totalTrades;
    const grossWins = wins.reduce((sum, t) => sum + t.pnl, 0);
    const grossLosses = losses.reduce((sum, t) => sum + t.pnl, 0);
    const totalFees = trades.reduce((sum, t) => sum + t.fees, 0);
    const fdr = grossWins > 0 ? totalFees / grossWins : 0;
    const netPnl = trades.reduce((sum, t) => sum + t.netPnl, 0);
    const avgHoldingPeriodMs = trades.reduce((sum, t) => sum + (t.exitTime - t.entryTime), 0) / totalTrades;
    let longestWinStreak = 0;
    let longestLoseStreak = 0;
    let currentWinStreak = 0;
    let currentLoseStreak = 0;
    for (const trade of trades) {
      if (trade.netPnl > 0) {
        currentWinStreak++;
        currentLoseStreak = 0;
        if (currentWinStreak > longestWinStreak) {
          longestWinStreak = currentWinStreak;
        }
      } else {
        currentLoseStreak++;
        currentWinStreak = 0;
        if (currentLoseStreak > longestLoseStreak) {
          longestLoseStreak = currentLoseStreak;
        }
      }
    }
    const bestTrade = trades.reduce((best, t) => t.netPnl > best.netPnl ? t : best, trades[0]);
    const monsterDependency = netPnl > 0 ? bestTrade.netPnl / netPnl * 100 : 0;
    const longTrades = trades.filter((t) => t.side === "LONG");
    const shortTrades = trades.filter((t) => t.side === "SHORT");
    const longWins = longTrades.filter((t) => t.netPnl > 0);
    const shortWins = shortTrades.filter((t) => t.netPnl > 0);
    const longWinRate = longTrades.length > 0 ? longWins.length / longTrades.length : 0;
    const shortWinRate = shortTrades.length > 0 ? shortWins.length / shortTrades.length : 0;
    const longPnl = longTrades.reduce((sum, t) => sum + t.netPnl, 0);
    const shortPnl = shortTrades.reduce((sum, t) => sum + t.netPnl, 0);
    const periodStart = Math.min(...trades.map((t) => t.entryTime));
    const periodEnd = Math.max(...trades.map((t) => t.exitTime));
    return {
      totalTrades,
      winRate,
      netPnl,
      grossWins,
      grossLosses,
      fdr,
      avgHoldingPeriodMs,
      longestWinStreak,
      longestLoseStreak,
      monsterDependency,
      directionSplit: {
        longWinRate,
        shortWinRate,
        longPnl,
        shortPnl
      },
      periodStart,
      periodEnd
    };
  }
  suggest(metrics) {
    const DEFAULT_RADAR = 170;
    const DEFAULT_DAILY_LOSS = 500;
    const adjustments = [];
    const upsertRadar = (delta, reason) => {
      const existing = adjustments.find((a) => a.field === "apex.radarThreshold");
      if (existing) {
        const existingDelta = existing.newValue - existing.currentValue;
        if (Math.abs(delta) > Math.abs(existingDelta)) {
          existing.newValue = existing.currentValue + delta;
          existing.reason = reason;
        }
      } else {
        adjustments.push({
          field: "apex.radarThreshold",
          currentValue: DEFAULT_RADAR,
          newValue: DEFAULT_RADAR + delta,
          reason
        });
      }
    };
    if (metrics.fdr > 0.3) {
      upsertRadar(10, "Too many low-quality entries dragging fees");
    }
    if (metrics.winRate < 0.4 && metrics.totalTrades > 0) {
      upsertRadar(15, "Entry quality too low");
    }
    if (metrics.winRate > 0.7 && metrics.totalTrades > 0) {
      upsertRadar(-10, "Can afford more entries");
    }
    if (metrics.longestLoseStreak >= 5) {
      adjustments.push({
        field: "apex.dailyLossLimit",
        currentValue: DEFAULT_DAILY_LOSS,
        newValue: DEFAULT_DAILY_LOSS * 0.8,
        reason: "Extended losing streak protection"
      });
    }
    if (metrics.monsterDependency > 50) {
      upsertRadar(5, "Over-reliant on single trade");
    }
    if (metrics.directionSplit.longWinRate < 0.3 && metrics.directionSplit.longWinRate > 0) {
      adjustments.push({
        field: "strategy.longExposure",
        currentValue: 1,
        newValue: 1,
        reason: "Consider reducing long exposure"
      });
    }
    if (metrics.directionSplit.shortWinRate < 0.3 && metrics.directionSplit.shortWinRate > 0) {
      adjustments.push({
        field: "strategy.shortExposure",
        currentValue: 1,
        newValue: 1,
        reason: "Consider reducing short exposure"
      });
    }
    return adjustments;
  }
  applyAdjustments(config, adjustments) {
    const newConfig = {
      ...config,
      apex: { ...config.apex }
    };
    for (const adj of adjustments) {
      if (adj.field === "apex.radarThreshold") {
        const delta = adj.newValue - adj.currentValue;
        const applied = config.apex.radarThreshold + delta;
        newConfig.apex.radarThreshold = clamp(
          applied,
          GUARDRAILS.radarThreshold.min,
          GUARDRAILS.radarThreshold.max
        );
      } else if (adj.field === "apex.dailyLossLimit") {
        const delta = adj.newValue - adj.currentValue;
        const applied = config.apex.dailyLossLimit + delta;
        newConfig.apex.dailyLossLimit = clamp(
          applied,
          GUARDRAILS.dailyLossLimit.min,
          GUARDRAILS.dailyLossLimit.max
        );
      }
    }
    return newConfig;
  }
  generateReport(metrics, adjustments) {
    const date = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
    const winRatePct = (metrics.winRate * 100).toFixed(1);
    const netPnlStr = metrics.netPnl >= 0 ? `$${metrics.netPnl.toFixed(2)}` : `-$${Math.abs(metrics.netPnl).toFixed(2)}`;
    const grossWinsStr = `$${metrics.grossWins.toFixed(2)}`;
    const grossLossesStr = metrics.grossLosses <= 0 ? `-$${Math.abs(metrics.grossLosses).toFixed(2)}` : `$${metrics.grossLosses.toFixed(2)}`;
    const fdrPct = (metrics.fdr * 100).toFixed(1);
    const avgHoldH = (metrics.avgHoldingPeriodMs / 36e5).toFixed(1);
    const monsterPct = metrics.monsterDependency.toFixed(1);
    const longWinRatePct = (metrics.directionSplit.longWinRate * 100).toFixed(1);
    const shortWinRatePct = (metrics.directionSplit.shortWinRate * 100).toFixed(1);
    const longPnlStr = metrics.directionSplit.longPnl >= 0 ? `+$${metrics.directionSplit.longPnl.toFixed(2)}` : `-$${Math.abs(metrics.directionSplit.longPnl).toFixed(2)}`;
    const shortPnlStr = metrics.directionSplit.shortPnl >= 0 ? `+$${metrics.directionSplit.shortPnl.toFixed(2)}` : `-$${Math.abs(metrics.directionSplit.shortPnl).toFixed(2)}`;
    const adjustmentsSection = adjustments.length === 0 ? "- No adjustments needed" : adjustments.map((a) => {
      if (a.newValue === a.currentValue) {
        return `- ${a.reason}`;
      }
      return `- \`${a.field}\`: ${a.currentValue} \u2192 ${a.newValue} (${a.reason})`;
    }).join("\n");
    return `# REFLECT Report \u2014 ${date}

## Summary
- Trades: ${metrics.totalTrades} | Win Rate: ${winRatePct}%
- Net PnL: ${netPnlStr} | Gross Wins: ${grossWinsStr} | Gross Losses: ${grossLossesStr}
- FDR: ${fdrPct}% | Avg Hold: ${avgHoldH}h

## Streaks
- Longest Win: ${metrics.longestWinStreak} | Longest Loss: ${metrics.longestLoseStreak}
- Monster Dependency: ${monsterPct}%

## Direction Split
- Long: ${longWinRatePct}% win rate, ${longPnlStr}
- Short: ${shortWinRatePct}% win rate, ${shortPnlStr}

## Adjustments
${adjustmentsSection}
`;
  }
};

// src/core/apex-orchestrator.ts
var RADAR_INTERVAL = 15;
var RECONCILE_INTERVAL = 5;
var SIGNAL_PRIORITY = [
  "FIRST_JUMP",
  "CONTRIB_EXPLOSION",
  "IMMEDIATE_MOVER",
  "NEW_ENTRY_DEEP",
  "DEEP_CLIMBER"
];
var ApexOrchestrator = class {
  state;
  config;
  adapter;
  stateStore;
  riskGuardian;
  guard;
  orderManager;
  pulse;
  radar;
  reflect;
  // Radar results cached between scans
  lastRadarScores = [];
  constructor(config, adapter, stateStore) {
    this.config = config;
    this.adapter = adapter;
    this.stateStore = stateStore;
    this.state = stateStore.loadState();
    while (this.state.slots.length < config.apex.maxSlots) {
      this.state.slots.push(createEmptySlot(this.state.slots.length));
    }
    this.riskGuardian = new RiskGuardian(this.state.riskGuardian);
    const guardConfig = { ...config.guard, leverage: config.apex.leverage };
    this.guard = new Guard(guardConfig);
    this.orderManager = new OrderManager();
    this.pulse = new Pulse();
    this.radar = new Radar();
    this.reflect = new ReflectAnalyzer();
  }
  /**
   * Main tick loop. Called once per tick interval.
   */
  async tick() {
    this.state.tickNumber++;
    const now = Date.now();
    const tickers = await this.fetchTickers();
    this.updateSlotROE(tickers);
    this.riskGuardian.tick(now);
    const allTickers = Array.from(tickers.values());
    const pulseResult = this.pulse.scan(allTickers);
    await this.evaluateGuards(tickers, now);
    const entryCandidates = [];
    for (const signal of pulseResult.signals) {
      const priority = this.getSignalPriority(signal);
      if (priority >= 0) {
        entryCandidates.push({
          symbol: signal.symbol,
          direction: signal.direction,
          source: "pulse",
          priority,
          confidence: signal.confidence
        });
      }
    }
    if (this.state.tickNumber % RADAR_INTERVAL === 0) {
      await this.runRadar(tickers);
      this.state.lastRadarScan = this.state.tickNumber;
    }
    for (const score of this.lastRadarScores) {
      if (score.total >= this.config.apex.radarThreshold) {
        entryCandidates.push({
          symbol: score.symbol,
          direction: score.direction,
          source: "radar",
          priority: SIGNAL_PRIORITY.length,
          // lower than all pulse signals
          confidence: score.total
        });
      }
    }
    entryCandidates.sort((a, b) => {
      if (a.priority !== b.priority) return a.priority - b.priority;
      return b.confidence - a.confidence;
    });
    await this.executeEntries(entryCandidates, tickers);
    if (this.state.tickNumber % RECONCILE_INTERVAL === 0) {
      await this.reconcile();
    }
    if (this.config.reflect.intervalTicks > 0 && this.state.tickNumber % this.config.reflect.intervalTicks === 0) {
      this.runReflect();
      this.state.lastReflect = this.state.tickNumber;
    }
    this.state.riskGuardian = this.riskGuardian.getState();
    this.stateStore.saveState(this.state);
  }
  // ── Market Data ──────────────────────────────────────────────
  async fetchTickers() {
    const tickers = /* @__PURE__ */ new Map();
    const symbols = new Set(this.config.strategy.symbols);
    symbols.add("BTC-PERP");
    for (const slot of this.state.slots) {
      if (slot.status === "OPEN" && slot.symbol) {
        symbols.add(slot.symbol);
      }
    }
    const promises = Array.from(symbols).map(async (symbol) => {
      try {
        const ticker = await this.adapter.getTicker(symbol);
        tickers.set(symbol, ticker);
      } catch {
      }
    });
    await Promise.all(promises);
    return tickers;
  }
  // ── ROE Update ───────────────────────────────────────────────
  updateSlotROE(tickers) {
    for (const slot of this.state.slots) {
      if (slot.status !== "OPEN" || !slot.symbol) continue;
      const ticker = tickers.get(slot.symbol);
      if (!ticker) continue;
      const leverage = this.config.apex.leverage;
      if (slot.side === "LONG") {
        slot.currentRoe = (ticker.lastPrice - slot.entryPrice) / slot.entryPrice * leverage * 100;
      } else {
        slot.currentRoe = (slot.entryPrice - ticker.lastPrice) / slot.entryPrice * leverage * 100;
      }
      if (slot.currentRoe > slot.peakRoe) {
        slot.peakRoe = slot.currentRoe;
      }
    }
  }
  // ── Guard Evaluation ─────────────────────────────────────────
  async evaluateGuards(tickers, now) {
    for (const slot of this.state.slots) {
      if (slot.status !== "OPEN" || !slot.symbol) continue;
      const ticker = tickers.get(slot.symbol);
      if (!ticker) continue;
      const result = this.guard.evaluate(slot, ticker.lastPrice, now);
      if (result.action === "EXIT") {
        await this.closeSlot(slot, ticker.lastPrice, result.reason, now);
      } else {
        if (result.newTierLevel !== void 0) {
          if (slot.guardPhase === "PHASE_1" && result.reason === "graduated_phase2") {
            slot.guardPhase = "PHASE_2";
          }
          slot.tierLevel = result.newTierLevel;
        }
      }
    }
  }
  // ── Close Slot ───────────────────────────────────────────────
  async closeSlot(slot, exitPrice, reason, now) {
    slot.status = "CLOSING";
    try {
      const closeSide = slot.side === "LONG" ? "SELL" : "BUY";
      await this.adapter.placeOrder({
        symbol: slot.symbol,
        side: closeSide,
        size: slot.size,
        price: exitPrice,
        orderType: "IOC",
        reduceOnly: true
      });
    } catch {
    }
    try {
      await this.adapter.cancelAllOrders(slot.symbol);
    } catch {
    }
    const pnlPerUnit = slot.side === "LONG" ? exitPrice - slot.entryPrice : slot.entryPrice - exitPrice;
    const pnl = pnlPerUnit * slot.size;
    const fees = Math.abs(pnl) * 1e-3;
    const netPnl = pnl - fees;
    const trade = {
      id: `${slot.id}-${now}`,
      symbol: slot.symbol,
      side: slot.side,
      entryPrice: slot.entryPrice,
      exitPrice,
      size: slot.size,
      entryTime: slot.entryTime,
      exitTime: now,
      pnl,
      fees,
      netPnl,
      exitReason: reason,
      slotId: slot.id
    };
    this.stateStore.appendTrade(trade);
    this.riskGuardian.recordTrade(netPnl, now);
    const emptySlot = createEmptySlot(slot.id);
    Object.assign(slot, emptySlot);
  }
  // ── Entry Execution ──────────────────────────────────────────
  async executeEntries(candidates, tickers) {
    if (!this.riskGuardian.canEnter()) return;
    for (const candidate of candidates) {
      const emptySlot = this.state.slots.find((s) => s.status === "EMPTY");
      if (!emptySlot) break;
      if (!this.riskGuardian.canEnter()) break;
      const ticker = tickers.get(candidate.symbol);
      if (!ticker) continue;
      const alreadyOpen = this.state.slots.some(
        (s) => s.status === "OPEN" && s.symbol === candidate.symbol
      );
      if (alreadyOpen) continue;
      let balances;
      try {
        balances = await this.adapter.getBalances();
      } catch {
        break;
      }
      const size = this.orderManager.calcSize(
        balances,
        10,
        // 10% of equity per position
        ticker.lastPrice,
        this.config.apex.leverage
      );
      let exchangeInfo;
      try {
        exchangeInfo = await this.adapter.getExchangeInfo();
      } catch {
        continue;
      }
      const finalSize = this.orderManager.enforceMinSize(size, candidate.symbol, exchangeInfo);
      if (finalSize === null) continue;
      const entrySide = candidate.direction === "LONG" ? "BUY" : "SELL";
      try {
        const result = await this.orderManager.placeWithFallback(
          {
            symbol: candidate.symbol,
            side: entrySide,
            size: finalSize,
            price: ticker.lastPrice,
            orderType: "ALO"
          },
          this.adapter
        );
        if (result.status === "FILLED" || result.status === "PARTIAL" || result.status === "OPEN") {
          const filledSize = result.filledSize > 0 ? result.filledSize : finalSize;
          const filledPrice = result.filledPrice > 0 ? result.filledPrice : ticker.lastPrice;
          emptySlot.status = "OPEN";
          emptySlot.symbol = candidate.symbol;
          emptySlot.side = candidate.direction;
          emptySlot.entryPrice = filledPrice;
          emptySlot.size = filledSize;
          emptySlot.entryTime = Date.now();
          emptySlot.guardPhase = "PHASE_1";
          emptySlot.peakRoe = 0;
          emptySlot.currentRoe = 0;
          emptySlot.tierLevel = 0;
        }
      } catch {
        continue;
      }
    }
  }
  // ── Radar ────────────────────────────────────────────────────
  async runRadar(tickers) {
    let btcTrend = "NEUTRAL";
    try {
      const btcCandles = await this.adapter.getCandles("BTC-PERP", "1h", 30);
      const rsi = calculateRSI(btcCandles);
      if (rsi > 60) btcTrend = "BULLISH";
      else if (rsi < 40) btcTrend = "BEARISH";
    } catch {
    }
    const candlesMap = /* @__PURE__ */ new Map();
    const symbols = this.config.strategy.symbols;
    const promises = symbols.map(async (symbol) => {
      try {
        const candles = await this.adapter.getCandles(symbol, "1h", 30);
        candlesMap.set(symbol, candles);
      } catch {
      }
    });
    await Promise.all(promises);
    const allTickers = symbols.map((s) => tickers.get(s)).filter((t) => t !== void 0);
    this.lastRadarScores = this.radar.scan(allTickers, candlesMap, btcTrend);
  }
  // ── Reconciliation ──────────────────────────────────────────
  async reconcile() {
    try {
      const positions = await this.adapter.getPositions();
      await this.adapter.getOpenOrders();
      for (const slot of this.state.slots) {
        if (slot.status !== "OPEN" || !slot.symbol) continue;
        const hasPosition = positions.some(
          (p) => p.symbol === slot.symbol && p.side === slot.side
        );
        if (!hasPosition) {
          const emptySlot = createEmptySlot(slot.id);
          Object.assign(slot, emptySlot);
        }
      }
    } catch {
    }
  }
  // ── REFLECT ──────────────────────────────────────────────────
  runReflect() {
    const trades = this.stateStore.loadTrades();
    if (trades.length === 0) return;
    const metrics = this.reflect.analyze(trades);
    const adjustments = this.reflect.suggest(metrics);
    if (this.config.reflect.autoAdjust && adjustments.length > 0) {
      const newConfig = this.reflect.applyAdjustments(this.config, adjustments);
      this.config.apex = newConfig.apex;
    }
  }
  // ── Signal Priority ──────────────────────────────────────────
  getSignalPriority(signal) {
    const idx = SIGNAL_PRIORITY.indexOf(signal.type);
    if (idx >= 0) return idx;
    if (signal.confidence > 90) return 2;
    return -1;
  }
  // ── Public Accessors ─────────────────────────────────────────
  getState() {
    return JSON.parse(JSON.stringify(this.state));
  }
};

// src/core/config.ts
import { readFileSync as readFileSync2, writeFileSync as writeFileSync2, mkdirSync as mkdirSync2, existsSync as existsSync2 } from "node:fs";
import { join as join2 } from "node:path";
var CONFIG_FILENAME = "engine.json";
var VALID_EXCHANGES = ["hyperliquid", "binance", "alpaca", "polymarket", "kium", "kis"];
function loadConfig(configDir) {
  const filePath = join2(configDir, CONFIG_FILENAME);
  if (!existsSync2(filePath)) {
    throw new Error(`Config not found: ${filePath}`);
  }
  const raw = readFileSync2(filePath, "utf-8");
  return JSON.parse(raw);
}
function saveConfig(configDir, config) {
  if (!existsSync2(configDir)) {
    mkdirSync2(configDir, { recursive: true });
  }
  const filePath = join2(configDir, CONFIG_FILENAME);
  writeFileSync2(filePath, JSON.stringify(config, null, 2), "utf-8");
}
function createDefaultConfig(exchangeName, preset = "default", guardPreset = "moderate") {
  const apexPreset = APEX_PRESETS[preset] ?? {};
  return {
    exchange: {
      name: exchangeName,
      testnet: true
    },
    apex: {
      preset,
      tickIntervalMs: 6e4,
      ...apexPreset
    },
    guard: { ...GUARD_PRESETS[guardPreset] },
    strategy: {
      name: "apex",
      symbols: [],
      params: {}
    },
    reflect: {
      autoAdjust: true,
      intervalTicks: 240
    }
  };
}
function validateConfig(config) {
  const errors = [];
  if (!VALID_EXCHANGES.includes(config.exchange.name)) {
    errors.push(`exchange.name must be one of: ${VALID_EXCHANGES.join(", ")}`);
  }
  if (config.apex.maxSlots < 1 || config.apex.maxSlots > 5) {
    errors.push("apex.maxSlots must be between 1 and 5");
  }
  if (config.apex.leverage < 1 || config.apex.leverage > 100) {
    errors.push("apex.leverage must be between 1 and 100");
  }
  if (config.apex.dailyLossLimit <= 0) {
    errors.push("apex.dailyLossLimit must be greater than 0");
  }
  if (config.apex.tickIntervalMs < 1e3) {
    errors.push("apex.tickIntervalMs must be at least 1000");
  }
  if (!config.guard.tiers || config.guard.tiers.length < 1) {
    errors.push("guard.tiers must have at least 1 entry");
  }
  if (!config.strategy.name || config.strategy.name.trim() === "") {
    errors.push("strategy.name must be a non-empty string");
  }
  return errors;
}

// src/exchanges/hl-signer.ts
import { createSign, createPrivateKey } from "crypto";
var KECCAK_ROUND_CONSTANTS = [
  0x0000000000000001n,
  0x0000000000008082n,
  0x800000000000808an,
  0x8000000080008000n,
  0x000000000000808bn,
  0x0000000080000001n,
  0x8000000080008081n,
  0x8000000000008009n,
  0x000000000000008an,
  0x0000000000000088n,
  0x0000000080008009n,
  0x000000008000000an,
  0x000000008000808bn,
  0x800000000000008bn,
  0x8000000000008089n,
  0x8000000000008003n,
  0x8000000000008002n,
  0x8000000000000080n,
  0x000000000000800an,
  0x800000008000000an,
  0x8000000080008081n,
  0x8000000000008080n,
  0x0000000080000001n,
  0x8000000080008008n
];
var ROTATION_CONSTANTS = [
  1,
  3,
  6,
  10,
  15,
  21,
  28,
  36,
  45,
  55,
  2,
  14,
  27,
  41,
  56,
  8,
  25,
  43,
  62,
  18,
  39,
  61,
  20,
  44
];
var PILN = [
  10,
  7,
  11,
  17,
  18,
  3,
  5,
  16,
  8,
  21,
  24,
  4,
  15,
  23,
  19,
  13,
  12,
  2,
  20,
  14,
  22,
  9,
  6,
  1
];
function keccak256(message) {
  const rate = 136;
  const msgLen = message.length;
  const padLen = rate - msgLen % rate;
  const padded = Buffer.alloc(msgLen + padLen, 0);
  message.copy(padded);
  padded[msgLen] = 1;
  padded[msgLen + padLen - 1] = (padded[msgLen + padLen - 1] ?? 0) | 128;
  const stateHi = new Uint32Array(25);
  const stateLo = new Uint32Array(25);
  for (let block = 0; block < padded.length; block += rate) {
    for (let i = 0; i < rate / 8; i++) {
      const lo = padded.readUInt32LE(block + i * 8);
      const hi = padded.readUInt32LE(block + i * 8 + 4);
      stateLo[i] ^= lo;
      stateHi[i] ^= hi;
    }
    keccakF1600(stateHi, stateLo);
  }
  const output = Buffer.alloc(32);
  for (let i = 0; i < 4; i++) {
    output.writeUInt32LE(stateLo[i], i * 8);
    output.writeUInt32LE(stateHi[i], i * 8 + 4);
  }
  return output;
}
function rot64(hi, lo, n) {
  if (n === 0) return [hi, lo];
  if (n < 32) {
    return [
      (hi << n | lo >>> 32 - n) >>> 0,
      (lo << n | hi >>> 32 - n) >>> 0
    ];
  }
  const swappedHi = lo;
  const swappedLo = hi;
  const m = n - 32;
  if (m === 0) return [swappedHi >>> 0, swappedLo >>> 0];
  return [
    (swappedHi << m | swappedLo >>> 32 - m) >>> 0,
    (swappedLo << m | swappedHi >>> 32 - m) >>> 0
  ];
}
function keccakF1600(hiArr, loArr) {
  const bcHi = new Uint32Array(5);
  const bcLo = new Uint32Array(5);
  let tHi = 0;
  let tLo = 0;
  for (let round = 0; round < 24; round++) {
    for (let x = 0; x < 5; x++) {
      bcHi[x] = hiArr[x] ^ hiArr[x + 5] ^ hiArr[x + 10] ^ hiArr[x + 15] ^ hiArr[x + 20];
      bcLo[x] = loArr[x] ^ loArr[x + 5] ^ loArr[x + 10] ^ loArr[x + 15] ^ loArr[x + 20];
    }
    for (let x = 0; x < 5; x++) {
      const rx = (x + 1) % 5;
      const lx = (x + 4) % 5;
      const [th, tl] = rot64(bcHi[rx], bcLo[rx], 1);
      const dh = bcHi[lx] ^ th;
      const dl = bcLo[lx] ^ tl;
      for (let y = 0; y < 5; y++) {
        hiArr[x + y * 5] ^= dh;
        loArr[x + y * 5] ^= dl;
      }
    }
    let curHi = hiArr[1];
    let curLo = loArr[1];
    for (let i = 0; i < 24; i++) {
      const j = PILN[i];
      [curHi, curLo] = rot64(curHi, curLo, ROTATION_CONSTANTS[i]);
      tHi = hiArr[j];
      tLo = loArr[j];
      hiArr[j] = curHi;
      loArr[j] = curLo;
      curHi = tHi;
      curLo = tLo;
    }
    for (let y = 0; y < 5; y++) {
      for (let x = 0; x < 5; x++) {
        bcHi[x] = hiArr[x + y * 5];
        bcLo[x] = loArr[x + y * 5];
      }
      for (let x = 0; x < 5; x++) {
        hiArr[x + y * 5] ^= ~bcHi[(x + 1) % 5] & bcHi[(x + 2) % 5];
        loArr[x + y * 5] ^= ~bcLo[(x + 1) % 5] & bcLo[(x + 2) % 5];
      }
    }
    const rc = KECCAK_ROUND_CONSTANTS[round];
    loArr[0] ^= Number(rc & 0xffffffffn);
    hiArr[0] ^= Number(rc >> 32n & 0xffffffffn);
  }
}
function keccak256Str(str) {
  return keccak256(Buffer.from(str, "utf8"));
}
function padUint256(value) {
  const hex = value.toString(16).padStart(64, "0");
  return Buffer.from(hex, "hex");
}
function padAddress(address) {
  const hex = address.replace(/^0x/i, "").padStart(64, "0");
  return Buffer.from(hex, "hex");
}
var AGENT_TYPE_HASH = keccak256Str(
  "Agent(address source,string connectionId)"
);
var TESTNET_CHAIN_ID = 421614;
var MAINNET_CHAIN_ID = 42161;
function buildSecp256k1DerKey(privateKey) {
  const oid = Buffer.from([6, 5, 43, 129, 4, 0, 10]);
  const version = Buffer.from([2, 1, 1]);
  const privKeyOctet = Buffer.concat([Buffer.from([4, 32]), privateKey]);
  const oidTagged = Buffer.concat([Buffer.from([160, oid.length]), oid]);
  const inner = Buffer.concat([version, privKeyOctet, oidTagged]);
  return Buffer.concat([Buffer.from([48, inner.length]), inner]);
}
function parseDerSignature(der) {
  let offset = 2;
  offset++;
  const rLen = der[offset++];
  let r = der.slice(offset, offset + rLen);
  offset += rLen;
  offset++;
  const sLen = der[offset++];
  let s = der.slice(offset, offset + sLen);
  if (r[0] === 0) r = r.slice(1);
  if (s[0] === 0) s = s.slice(1);
  return { r, s };
}
function secp256k1Sign(hash, privateKey) {
  const derKey = buildSecp256k1DerKey(privateKey);
  let keyObject;
  try {
    keyObject = createPrivateKey({ key: derKey, format: "der", type: "sec1" });
  } catch {
    keyObject = createPrivateKey({
      key: {
        kty: "EC",
        crv: "secp256k1",
        d: privateKey.toString("base64url"),
        // Public key components required by JWK — use dummy values; signing only needs d
        x: Buffer.alloc(32).toString("base64url"),
        y: Buffer.alloc(32).toString("base64url")
      },
      format: "jwk"
    });
  }
  const sign = createSign("SHA256");
  sign.update(hash);
  const derSig = sign.sign(keyObject);
  const { r, s } = parseDerSignature(derSig);
  return {
    r: "0x" + r.toString("hex").padStart(64, "0"),
    s: "0x" + s.toString("hex").padStart(64, "0"),
    v: 27
    // recovery id — 27 or 28; exact value requires public key recovery
  };
}
var HlSigner = class {
  privateKeyBytes;
  chainId;
  constructor(privateKey, testnet) {
    this.chainId = testnet ? TESTNET_CHAIN_ID : MAINNET_CHAIN_ID;
    const hex = privateKey.startsWith("0x") ? privateKey.slice(2) : privateKey;
    this.privateKeyBytes = Buffer.from(hex, "hex");
  }
  /**
   * Sign a Hyperliquid action payload using EIP-712.
   * Returns the signature and nonce to include in the request body.
   */
  signAction(action, nonce, vaultAddress) {
    const hash = this.hashAction(action, nonce, vaultAddress);
    const signature = secp256k1Sign(hash, this.privateKeyBytes);
    return { signature, nonce, vaultAddress };
  }
  // ── Private ────────────────────────────────────────────────────────────────
  hashAction(action, nonce, vaultAddress) {
    const domainSeparator = this.buildDomainSeparator();
    const structHash = this.buildStructHash(action, nonce, vaultAddress);
    const msg = Buffer.concat([
      Buffer.from([25, 1]),
      domainSeparator,
      structHash
    ]);
    return keccak256(msg);
  }
  buildDomainSeparator() {
    const typeHash = keccak256Str(
      "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    const encoded = Buffer.concat([
      typeHash,
      keccak256Str("Exchange"),
      keccak256Str("1"),
      padUint256(BigInt(this.chainId)),
      padAddress("0x0000000000000000000000000000000000000000")
    ]);
    return keccak256(encoded);
  }
  buildStructHash(action, nonce, vaultAddress) {
    const actionBytes = Buffer.from(JSON.stringify(action));
    const nonceBytes = Buffer.alloc(8);
    nonceBytes.writeBigUInt64BE(BigInt(nonce));
    const connectionId = keccak256(Buffer.concat([actionBytes, nonceBytes]));
    const zero = padAddress("0x0000000000000000000000000000000000000000");
    const source = vaultAddress ? padAddress(vaultAddress) : zero;
    const encoded = Buffer.concat([
      AGENT_TYPE_HASH,
      source,
      connectionId
    ]);
    return keccak256(encoded);
  }
};

// src/exchanges/hyperliquid.ts
function toHlCoin(symbol) {
  return symbol.replace(/-PERP$/, "");
}
function fromHlCoin(coin) {
  return `${coin}-PERP`;
}
function toHlOrderType(orderType) {
  switch (orderType) {
    case "ALO":
      return { limit: { tif: "Alo" } };
    case "GTC":
      return { limit: { tif: "Gtc" } };
    case "IOC":
      return { limit: { tif: "Ioc" } };
  }
}
function fromHlOrderType(rawType) {
  if (rawType.toLowerCase().includes("alo")) return "ALO";
  if (rawType.toLowerCase().includes("ioc")) return "IOC";
  return "GTC";
}
var STATIC_ASSET_INDEX = {
  BTC: 0,
  ETH: 1,
  ATOM: 2,
  MATIC: 3,
  DYDX: 4,
  SOL: 5,
  AVAX: 6,
  BNB: 7,
  APT: 8,
  ARB: 9,
  OP: 10,
  LTC: 11,
  DOGE: 12,
  CFX: 13,
  SUI: 14,
  kPEPE: 15,
  SHIB: 16,
  TRX: 17,
  ADA: 18,
  TON: 19,
  LINK: 20
};
var HyperliquidAdapter = class {
  name = "Hyperliquid";
  baseUrl;
  walletAddress;
  signer;
  assetIndex = new Map(Object.entries(STATIC_ASSET_INDEX));
  assetIndexLoaded = false;
  // Injected in tests via _fetch; production uses global fetch
  _fetch = (...args) => fetch(...args);
  constructor(config) {
    this.baseUrl = config.testnet ? "https://api.hyperliquid-testnet.xyz" : "https://api.hyperliquid.xyz";
    this.walletAddress = requireWalletAddress(config.walletAddress, "HyperliquidAdapter");
    this.signer = new HlSigner(config.privateKey, config.testnet);
  }
  // ── Public interface ───────────────────────────────────────────────────────
  async getTicker(symbol) {
    const coin = toHlCoin(symbol);
    const [midsRaw, ctxRaw] = await Promise.all([
      this.infoPost({ type: "allMids" }),
      this.infoPost({ type: "metaAndAssetCtxs" })
    ]);
    if (!(coin in midsRaw)) {
      throw new Error(`Symbol not found: ${symbol}`);
    }
    const mid = parseFloat(midsRaw[coin]);
    const spread = mid * 1e-4;
    const bid = mid - spread;
    const ask = mid + spread;
    let fundingRate = 0;
    let volume24h = 0;
    let openInterest = 0;
    if (Array.isArray(ctxRaw.assetCtxs)) {
      const typedCtx = ctxRaw;
      const idx = typedCtx.universe?.findIndex((u) => u.name === coin) ?? -1;
      if (idx >= 0 && typedCtx.assetCtxs) {
        const ctx = typedCtx.assetCtxs[idx];
        if (ctx) {
          fundingRate = parseFloat(ctx.funding);
          openInterest = parseFloat(ctx.openInterest);
          volume24h = parseFloat(ctx.dayNtlVlm);
        }
      }
    }
    return {
      symbol,
      mid,
      bid,
      ask,
      lastPrice: mid,
      volume24h,
      openInterest,
      fundingRate,
      timestamp: Date.now()
    };
  }
  async getOrderBook(symbol, depth = 20) {
    const coin = toHlCoin(symbol);
    const raw = await this.infoPost({
      type: "l2Book",
      coin
    });
    const [rawBids, rawAsks] = raw.levels;
    const bids = (rawBids ?? []).slice(0, depth).map((l) => ({
      price: parseFloat(l.px),
      size: parseFloat(l.sz)
    }));
    const asks = (rawAsks ?? []).slice(0, depth).map((l) => ({
      price: parseFloat(l.px),
      size: parseFloat(l.sz)
    }));
    return { symbol, bids, asks, timestamp: Date.now() };
  }
  async getCandles(symbol, interval, limit) {
    const coin = toHlCoin(symbol);
    const startTime = Date.now() - intervalToMs(interval) * limit;
    const raw = await this.infoPost({
      type: "candleSnapshot",
      coin,
      interval,
      startTime
    });
    return raw.slice(-limit).map((c) => ({
      timestamp: c.t,
      open: parseFloat(c.o),
      high: parseFloat(c.h),
      low: parseFloat(c.l),
      close: parseFloat(c.c),
      volume: parseFloat(c.v)
    }));
  }
  async getBalances() {
    const state = await this.getAccountState();
    const available = parseFloat(state.withdrawable);
    const total = parseFloat(state.marginSummary.accountValue);
    const unrealizedPnl = state.assetPositions.reduce(
      (acc, ap) => acc + parseFloat(ap.position.unrealizedPnl),
      0
    );
    return [
      {
        currency: "USDC",
        available,
        total,
        unrealizedPnl
      }
    ];
  }
  async getPositions() {
    const state = await this.getAccountState();
    return state.assetPositions.filter((ap) => parseFloat(ap.position.szi) !== 0).map((ap) => {
      const p = ap.position;
      const szi = parseFloat(p.szi);
      return {
        symbol: fromHlCoin(p.coin),
        side: szi >= 0 ? "LONG" : "SHORT",
        size: Math.abs(szi),
        entryPrice: parseFloat(p.entryPx),
        markPrice: parseFloat(p.positionValue) / Math.abs(szi),
        unrealizedPnl: parseFloat(p.unrealizedPnl),
        leverage: parseFloat(p.leverage.value),
        liquidationPrice: p.liquidationPx !== null ? parseFloat(p.liquidationPx) : null
      };
    });
  }
  async placeOrder(order) {
    const coin = toHlCoin(order.symbol);
    const assetIdx = await this.resolveAssetIndex(coin);
    const wire = {
      a: assetIdx,
      b: order.side === "BUY",
      p: order.price.toString(),
      s: order.size.toString(),
      r: order.reduceOnly ?? false,
      t: toHlOrderType(order.orderType),
      ...order.clientOrderId ? { c: order.clientOrderId } : {}
    };
    const action = {
      type: "order",
      orders: [wire],
      grouping: "na"
    };
    const nonce = Date.now();
    const { signature } = this.signer.signAction(action, nonce);
    const resp = await this.exchangePost({
      action,
      nonce,
      signature
    });
    if (resp.status !== "ok") {
      const errMsg = typeof resp.response === "string" ? resp.response : "Order rejected by exchange";
      throw new Error(errMsg);
    }
    const responseData = resp.response;
    const statuses = responseData.data?.statuses ?? [];
    const first = statuses[0];
    if (!first) {
      throw new Error("No order status returned from exchange");
    }
    return parseOrderStatus(first);
  }
  async cancelOrder(orderId) {
    const [oidStr, coin] = orderId.split(":");
    const oid = parseInt(oidStr, 10);
    const assetIdx = coin ? await this.resolveAssetIndex(coin) : 0;
    const action = {
      type: "cancel",
      cancels: [{ a: assetIdx, o: oid }]
    };
    const nonce = Date.now();
    const { signature } = this.signer.signAction(action, nonce);
    const resp = await this.exchangePost({
      action,
      nonce,
      signature
    });
    if (resp.status !== "ok") {
      const errMsg = typeof resp.response === "string" ? resp.response : "Cancel rejected by exchange";
      throw new Error(errMsg);
    }
  }
  async cancelAllOrders(symbol) {
    const openOrders = await this.getRawOpenOrders();
    const filtered = symbol ? openOrders.filter((o) => o.coin === toHlCoin(symbol)) : openOrders;
    if (filtered.length === 0) return;
    const cancels = await Promise.all(
      filtered.map(async (o) => ({
        a: await this.resolveAssetIndex(o.coin),
        o: o.oid
      }))
    );
    const action = {
      type: "cancel",
      cancels
    };
    const nonce = Date.now();
    const { signature } = this.signer.signAction(action, nonce);
    const resp = await this.exchangePost({
      action,
      nonce,
      signature
    });
    if (resp.status !== "ok") {
      const errMsg = typeof resp.response === "string" ? resp.response : "Cancel all rejected by exchange";
      throw new Error(errMsg);
    }
  }
  async setStopLoss(symbol, side, triggerPrice, size) {
    const coin = toHlCoin(symbol);
    const assetIdx = await this.resolveAssetIndex(coin);
    const isBuy = side !== "BUY";
    const wire = {
      a: assetIdx,
      b: isBuy,
      p: triggerPrice.toString(),
      s: size.toString(),
      r: true,
      // reduce_only
      t: {
        trigger: {
          isMarket: true,
          tpsl: "sl",
          triggerPx: triggerPrice.toString()
        }
      }
    };
    const action = {
      type: "order",
      orders: [wire],
      grouping: "na"
    };
    const nonce = Date.now();
    const { signature } = this.signer.signAction(action, nonce);
    const resp = await this.exchangePost({
      action,
      nonce,
      signature
    });
    if (resp.status !== "ok") {
      const errMsg = typeof resp.response === "string" ? resp.response : "Stop loss order rejected";
      throw new Error(errMsg);
    }
    const responseData = resp.response;
    const statuses = responseData.data?.statuses ?? [];
    const first = statuses[0];
    if (!first) {
      throw new Error("No order status returned from exchange");
    }
    return parseOrderStatus(first);
  }
  async getOpenOrders(symbol) {
    const raw = await this.getRawOpenOrders();
    const filtered = symbol ? raw.filter((o) => o.coin === toHlCoin(symbol)) : raw;
    return filtered.map((o) => ({
      orderId: String(o.oid),
      symbol: fromHlCoin(o.coin),
      side: o.side === "B" ? "BUY" : "SELL",
      price: parseFloat(o.limitPx),
      size: parseFloat(o.sz),
      filledSize: 0,
      // HL open orders don't carry partial fill info
      orderType: fromHlOrderType(o.orderType),
      timestamp: o.timestamp
    }));
  }
  async getExchangeInfo() {
    const meta = await this.infoPost({ type: "meta" });
    const supportedSymbols = meta.universe.map((u) => fromHlCoin(u.name));
    const minOrderSizes = {};
    const tickSizes = {};
    for (const asset of meta.universe) {
      const sym = fromHlCoin(asset.name);
      minOrderSizes[sym] = Math.pow(10, -asset.szDecimals);
      tickSizes[sym] = 0.01;
    }
    return {
      name: "Hyperliquid",
      testnet: this.baseUrl.includes("testnet"),
      supportedSymbols,
      minOrderSizes,
      tickSizes
    };
  }
  // ── Private helpers ────────────────────────────────────────────────────────
  async getAccountState() {
    return this.infoPost({
      type: "clearinghouseState",
      user: this.walletAddress
    });
  }
  async getRawOpenOrders() {
    return this.infoPost({
      type: "openOrders",
      user: this.walletAddress
    });
  }
  async resolveAssetIndex(coin) {
    if (this.assetIndex.has(coin)) {
      return this.assetIndex.get(coin);
    }
    if (!this.assetIndexLoaded) {
      await this.loadAssetIndex();
      if (this.assetIndex.has(coin)) {
        return this.assetIndex.get(coin);
      }
    }
    return 0;
  }
  async loadAssetIndex() {
    const meta = await this.infoPost({ type: "meta" });
    for (let i = 0; i < meta.universe.length; i++) {
      const asset = meta.universe[i];
      if (asset) this.assetIndex.set(asset.name, i);
    }
    this.assetIndexLoaded = true;
  }
  async infoPost(body) {
    const resp = await this._fetch(`${this.baseUrl}/info`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Hyperliquid info API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }
  async exchangePost(body) {
    const resp = await this._fetch(`${this.baseUrl}/exchange`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Hyperliquid exchange API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }
};
function parseOrderStatus(status) {
  if (typeof status === "string") {
    return { orderId: "0", status: "OPEN", filledSize: 0, filledPrice: 0, timestamp: Date.now() };
  }
  if ("error" in status) {
    throw new Error(`Order rejected: ${status.error}`);
  }
  if ("resting" in status) {
    return {
      orderId: String(status.resting.oid),
      status: "OPEN",
      filledSize: 0,
      filledPrice: 0,
      timestamp: Date.now()
    };
  }
  if ("filled" in status) {
    return {
      orderId: String(status.filled.oid),
      status: "FILLED",
      filledSize: parseFloat(status.filled.totalSz),
      filledPrice: parseFloat(status.filled.avgPx),
      timestamp: Date.now()
    };
  }
  return { orderId: "0", status: "OPEN", filledSize: 0, filledPrice: 0, timestamp: Date.now() };
}
function intervalToMs(interval) {
  const n = parseInt(interval);
  if (interval.endsWith("m")) return n * 6e4;
  if (interval.endsWith("h")) return n * 36e5;
  if (interval.endsWith("d")) return n * 864e5;
  return 6e4;
}
function requireWalletAddress(address, context) {
  if (!address) {
    throw new Error(`${context}: walletAddress is required. Ethereum addresses cannot be derived from private keys without elliptic curve point multiplication. Provide the address explicitly in config.`);
  }
  return address;
}

// src/exchanges/hl-websocket.ts
import { EventEmitter } from "events";
import WebSocket from "ws";
var TESTNET_WS_URL = "wss://api.hyperliquid-testnet.xyz/ws";
var MAINNET_WS_URL = "wss://api.hyperliquid.xyz/ws";
var HEARTBEAT_INTERVAL_MS = 3e4;
var BASE_RECONNECT_DELAY_MS = 1e3;
var MAX_RETRIES = 5;
var HlWebSocket = class extends EventEmitter {
  url;
  _WS;
  _ws = null;
  _connected = false;
  _intentionalDisconnect = false;
  _retryCount = 0;
  _heartbeatTimer = null;
  _reconnectTimer = null;
  _subscriptions = [];
  constructor(config) {
    super();
    this.url = config.testnet ? TESTNET_WS_URL : MAINNET_WS_URL;
    this._WS = config._WebSocket ?? WebSocket;
  }
  // ── Public API ──────────────────────────────────────────────────────────
  get isConnected() {
    return this._connected;
  }
  get subscriptions() {
    return [...this._subscriptions];
  }
  connect() {
    return new Promise((resolve, reject) => {
      this._intentionalDisconnect = false;
      this._createConnection(resolve, reject);
    });
  }
  disconnect() {
    this._intentionalDisconnect = true;
    this._stopHeartbeat();
    this._clearReconnectTimer();
    this._subscriptions = [];
    if (this._ws) {
      this._connected = false;
      this._ws.close();
      this._ws = null;
      this.emit("disconnected");
    }
  }
  subscribe(type, params) {
    if (!this._connected || !this._ws) {
      throw new Error("Not connected");
    }
    this._sendSubscription({ type, params });
    this._subscriptions.push({ type, params });
  }
  // ── Private ─────────────────────────────────────────────────────────────
  _createConnection(resolve, reject) {
    const ws = new this._WS(this.url);
    this._ws = ws;
    ws.on("open", () => {
      this._connected = true;
      this._retryCount = 0;
      this._startHeartbeat();
      this.emit("connected");
      resolve?.();
    });
    ws.on("message", (data) => {
      this._handleMessage(data);
    });
    ws.on("error", (err) => {
      if (!this._connected && reject) {
        reject(err);
        return;
      }
      this.emit("error", err);
    });
    ws.on("close", (_code, _reason) => {
      this._connected = false;
      this._stopHeartbeat();
      if (!this._intentionalDisconnect) {
        this._scheduleReconnect();
      }
    });
  }
  _handleMessage(data) {
    let text;
    if (Buffer.isBuffer(data)) {
      text = data.toString("utf-8");
    } else if (typeof data === "string") {
      text = data;
    } else {
      return;
    }
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      this.emit("error", new Error(`Failed to parse WebSocket message: ${text.slice(0, 100)}`));
      return;
    }
    const channel = parsed["channel"];
    if (typeof channel !== "string") {
      return;
    }
    const rawData = parsed["data"];
    if (!rawData) return;
    switch (channel) {
      case "allMids": {
        this.emit("allMids", rawData["mids"]);
        break;
      }
      case "l2Book": {
        this.emit("l2Book", rawData);
        break;
      }
      default: {
        this.emit(channel, rawData);
        break;
      }
    }
  }
  _startHeartbeat() {
    this._stopHeartbeat();
    this._heartbeatTimer = setInterval(() => {
      if (this._ws && this._connected) {
        this._ws.ping();
      }
    }, HEARTBEAT_INTERVAL_MS);
  }
  _stopHeartbeat() {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
  }
  _scheduleReconnect() {
    if (this._retryCount >= MAX_RETRIES) {
      this.emit("maxRetriesReached");
      return;
    }
    const delay = BASE_RECONNECT_DELAY_MS * Math.pow(2, this._retryCount);
    this._retryCount++;
    this._reconnectTimer = setTimeout(() => {
      this._reconnect();
    }, delay);
  }
  _reconnect() {
    const subs = [...this._subscriptions];
    this._createConnection(
      () => {
        for (const sub of subs) {
          this._sendSubscription(sub);
        }
        this._subscriptions = subs;
      },
      () => {
      }
    );
  }
  _sendSubscription(entry) {
    const subscription = { type: entry.type };
    if (entry.params) {
      Object.assign(subscription, entry.params);
    }
    this._ws?.send(JSON.stringify({ method: "subscribe", subscription }));
  }
  _clearReconnectTimer() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }
};

// src/exchanges/bn-signer.ts
import { createHmac } from "node:crypto";
var BnSigner = class {
  secret;
  constructor(secret) {
    this.secret = secret;
  }
  /** HMAC-SHA256 sign a params object. Params are sorted alphabetically. */
  sign(params) {
    const queryString = this.buildQueryString(params);
    return createHmac("sha256", this.secret).update(queryString).digest("hex");
  }
  /** Build query string with appended signature. */
  signQueryString(params) {
    const queryString = this.buildQueryString(params);
    const signature = createHmac("sha256", this.secret).update(queryString).digest("hex");
    return `${queryString}&signature=${signature}`;
  }
  buildQueryString(params) {
    return Object.entries(params).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  }
};

// src/exchanges/binance.ts
var RECV_WINDOW = "5000";
function toBnSymbol(symbol) {
  return symbol.replace(/-PERP$/, "") + "USDT";
}
function fromBnSymbol(bnSymbol) {
  return bnSymbol.replace(/USDT$/, "") + "-PERP";
}
function toBnTimeInForce(orderType) {
  switch (orderType) {
    case "ALO":
      return "GTX";
    case "GTC":
      return "GTC";
    case "IOC":
      return "IOC";
  }
}
function fromBnTimeInForce(tif) {
  switch (tif) {
    case "GTX":
      return "ALO";
    case "IOC":
      return "IOC";
    default:
      return "GTC";
  }
}
function fromBnStatus(bnStatus) {
  switch (bnStatus) {
    case "NEW":
      return "OPEN";
    case "FILLED":
      return "FILLED";
    case "PARTIALLY_FILLED":
      return "PARTIAL";
    case "CANCELED":
    case "REJECTED":
    case "EXPIRED":
      return "REJECTED";
    default:
      return "OPEN";
  }
}
var BinanceAdapter = class {
  name = "Binance";
  baseUrl;
  apiKey;
  signer;
  market;
  // Injected in tests via _fetch; production uses global fetch
  _fetch = (...args) => fetch(...args);
  constructor(config) {
    this.market = config.market ?? "futures";
    this.baseUrl = config.testnet ? this.market === "futures" ? "https://testnet.binancefuture.com" : "https://testnet.binance.vision" : this.market === "futures" ? "https://fapi.binance.com" : "https://api.binance.com";
    this.apiKey = config.apiKey;
    this.signer = new BnSigner(config.secretKey);
  }
  // ── Public interface ───────────────────────────────────────────────────────
  async getTicker(symbol) {
    const bnSymbol = toBnSymbol(symbol);
    const [ticker24hr, premium, bookTicker] = await Promise.all([
      this.publicGet("/fapi/v1/ticker/24hr", { symbol: bnSymbol }),
      this.publicGet("/fapi/v1/premiumIndex", { symbol: bnSymbol }),
      this.publicGet("/fapi/v1/ticker/bookTicker", { symbol: bnSymbol })
    ]);
    const bid = parseFloat(bookTicker.bidPrice);
    const ask = parseFloat(bookTicker.askPrice);
    const mid = (bid + ask) / 2;
    return {
      symbol,
      mid,
      bid,
      ask,
      lastPrice: parseFloat(ticker24hr.lastPrice),
      volume24h: parseFloat(ticker24hr.volume),
      openInterest: ticker24hr.openInterest ? parseFloat(ticker24hr.openInterest) : 0,
      fundingRate: parseFloat(premium.lastFundingRate),
      timestamp: Date.now()
    };
  }
  async getOrderBook(symbol, depth = 20) {
    const bnSymbol = toBnSymbol(symbol);
    const raw = await this.publicGet("/fapi/v1/depth", {
      symbol: bnSymbol,
      limit: String(depth)
    });
    const bids = (raw.bids ?? []).map((level) => ({
      price: parseFloat(level[0]),
      size: parseFloat(level[1])
    }));
    const asks = (raw.asks ?? []).map((level) => ({
      price: parseFloat(level[0]),
      size: parseFloat(level[1])
    }));
    return { symbol, bids, asks, timestamp: Date.now() };
  }
  async getCandles(symbol, interval, limit) {
    const bnSymbol = toBnSymbol(symbol);
    const raw = await this.publicGet("/fapi/v1/klines", {
      symbol: bnSymbol,
      interval,
      limit: String(limit)
    });
    return raw.map((k) => ({
      timestamp: k[0],
      open: parseFloat(k[1]),
      high: parseFloat(k[2]),
      low: parseFloat(k[3]),
      close: parseFloat(k[4]),
      volume: parseFloat(k[5])
    }));
  }
  async getBalances() {
    const raw = await this.signedGet("/fapi/v2/balance");
    return raw.filter((b) => parseFloat(b.balance) !== 0).map((b) => ({
      currency: b.asset,
      available: parseFloat(b.availableBalance),
      total: parseFloat(b.balance),
      unrealizedPnl: parseFloat(b.crossUnPnl)
    }));
  }
  async getPositions() {
    const raw = await this.signedGet("/fapi/v2/positionRisk");
    return raw.filter((p) => parseFloat(p.positionAmt) !== 0).map((p) => {
      const amt = parseFloat(p.positionAmt);
      const liqPrice = parseFloat(p.liquidationPrice);
      return {
        symbol: fromBnSymbol(p.symbol),
        side: amt >= 0 ? "LONG" : "SHORT",
        size: Math.abs(amt),
        entryPrice: parseFloat(p.entryPrice),
        markPrice: parseFloat(p.markPrice),
        unrealizedPnl: parseFloat(p.unRealizedProfit),
        leverage: parseFloat(p.leverage),
        liquidationPrice: liqPrice === 0 ? null : liqPrice
      };
    });
  }
  async placeOrder(order) {
    const bnSymbol = toBnSymbol(order.symbol);
    const params = {
      symbol: bnSymbol,
      side: order.side,
      type: "LIMIT",
      quantity: String(order.size),
      price: String(order.price),
      timeInForce: toBnTimeInForce(order.orderType)
    };
    if (order.reduceOnly) {
      params["reduceOnly"] = "true";
    }
    if (order.clientOrderId) {
      params["newClientOrderId"] = order.clientOrderId;
    }
    const resp = await this.signedPost("/fapi/v1/order", params);
    return {
      orderId: String(resp.orderId),
      status: fromBnStatus(resp.status),
      filledSize: parseFloat(resp.executedQty),
      filledPrice: parseFloat(resp.avgPrice),
      timestamp: resp.updateTime
    };
  }
  async cancelOrder(orderId) {
    const [oidStr, bnSymbol] = orderId.split(":");
    const params = {
      orderId: oidStr
    };
    if (bnSymbol) {
      params["symbol"] = bnSymbol;
    }
    await this.signedDelete("/fapi/v1/order", params);
  }
  async cancelAllOrders(symbol) {
    if (symbol) {
      const bnSymbol = toBnSymbol(symbol);
      await this.signedDelete("/fapi/v1/allOpenOrders", { symbol: bnSymbol });
      return;
    }
    const openOrders = await this.signedGet("/fapi/v1/openOrders");
    const uniqueSymbols = [...new Set(openOrders.map((o) => o.symbol))];
    await Promise.all(
      uniqueSymbols.map(
        (sym) => this.signedDelete("/fapi/v1/allOpenOrders", { symbol: sym })
      )
    );
  }
  async setStopLoss(symbol, side, triggerPrice, size) {
    const bnSymbol = toBnSymbol(symbol);
    const slSide = side === "BUY" ? "SELL" : "BUY";
    const params = {
      symbol: bnSymbol,
      side: slSide,
      type: "STOP_MARKET",
      quantity: String(size),
      stopPrice: String(triggerPrice),
      reduceOnly: "true"
    };
    const resp = await this.signedPost("/fapi/v1/order", params);
    return {
      orderId: String(resp.orderId),
      status: fromBnStatus(resp.status),
      filledSize: parseFloat(resp.executedQty),
      filledPrice: parseFloat(resp.avgPrice),
      timestamp: resp.updateTime
    };
  }
  async getOpenOrders(symbol) {
    const params = {};
    if (symbol) {
      params["symbol"] = toBnSymbol(symbol);
    }
    const raw = await this.signedGet("/fapi/v1/openOrders", params);
    return raw.map((o) => ({
      orderId: String(o.orderId),
      symbol: fromBnSymbol(o.symbol),
      side: o.side,
      price: parseFloat(o.price),
      size: parseFloat(o.origQty),
      filledSize: parseFloat(o.executedQty),
      orderType: fromBnTimeInForce(o.timeInForce),
      timestamp: o.time
    }));
  }
  async getExchangeInfo() {
    const raw = await this.publicGet("/fapi/v1/exchangeInfo", {});
    const supportedSymbols = [];
    const minOrderSizes = {};
    const tickSizes = {};
    for (const sym of raw.symbols) {
      if (sym.status !== "TRADING") continue;
      const engineSymbol = fromBnSymbol(sym.symbol);
      supportedSymbols.push(engineSymbol);
      const lotFilter = sym.filters.find((f) => f.filterType === "LOT_SIZE");
      if (lotFilter?.minQty) {
        minOrderSizes[engineSymbol] = parseFloat(lotFilter.minQty);
      }
      const priceFilter = sym.filters.find((f) => f.filterType === "PRICE_FILTER");
      if (priceFilter?.tickSize) {
        tickSizes[engineSymbol] = parseFloat(priceFilter.tickSize);
      }
    }
    return {
      name: "Binance",
      testnet: this.baseUrl.includes("testnet"),
      supportedSymbols,
      minOrderSizes,
      tickSizes
    };
  }
  // ── Private helpers ────────────────────────────────────────────────────────
  async publicGet(path, params) {
    const qs = Object.entries(params).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
    const url = qs ? `${this.baseUrl}${path}?${qs}` : `${this.baseUrl}${path}`;
    const resp = await this._fetch(url, {
      method: "GET",
      headers: { "X-MBX-APIKEY": this.apiKey }
    });
    if (!resp.ok) {
      const text = await resp.text();
      let errMsg = `Binance API error ${resp.status}: ${text}`;
      try {
        const err = JSON.parse(text);
        errMsg = `Binance API error ${resp.status}: [${err.code}] ${err.msg}`;
      } catch {
      }
      throw new Error(errMsg);
    }
    return resp.json();
  }
  async signedGet(path, extraParams) {
    const params = {
      ...extraParams,
      timestamp: String(Date.now()),
      recvWindow: RECV_WINDOW
    };
    const signedQs = this.signer.signQueryString(params);
    const url = `${this.baseUrl}${path}?${signedQs}`;
    const resp = await this._fetch(url, {
      method: "GET",
      headers: { "X-MBX-APIKEY": this.apiKey }
    });
    if (!resp.ok) {
      const text = await resp.text();
      let errMsg = `Binance API error ${resp.status}: ${text}`;
      try {
        const err = JSON.parse(text);
        errMsg = `Binance API error ${resp.status}: [${err.code}] ${err.msg}`;
      } catch {
      }
      throw new Error(errMsg);
    }
    return resp.json();
  }
  async signedPost(path, extraParams) {
    const params = {
      ...extraParams,
      timestamp: String(Date.now()),
      recvWindow: RECV_WINDOW
    };
    const signedQs = this.signer.signQueryString(params);
    const url = `${this.baseUrl}${path}?${signedQs}`;
    const resp = await this._fetch(url, {
      method: "POST",
      headers: { "X-MBX-APIKEY": this.apiKey }
    });
    if (!resp.ok) {
      const text = await resp.text();
      let errMsg = `Binance API error ${resp.status}: ${text}`;
      try {
        const err = JSON.parse(text);
        errMsg = `Binance API error ${resp.status}: [${err.code}] ${err.msg}`;
      } catch {
      }
      throw new Error(errMsg);
    }
    return resp.json();
  }
  async signedDelete(path, extraParams) {
    const params = {
      ...extraParams,
      timestamp: String(Date.now()),
      recvWindow: RECV_WINDOW
    };
    const signedQs = this.signer.signQueryString(params);
    const url = `${this.baseUrl}${path}?${signedQs}`;
    const resp = await this._fetch(url, {
      method: "DELETE",
      headers: { "X-MBX-APIKEY": this.apiKey }
    });
    if (!resp.ok) {
      const text = await resp.text();
      let errMsg = `Binance API error ${resp.status}: ${text}`;
      try {
        const err = JSON.parse(text);
        errMsg = `Binance API error ${resp.status}: [${err.code}] ${err.msg}`;
      } catch {
      }
      throw new Error(errMsg);
    }
    return resp.json();
  }
};

// src/exchanges/bn-websocket.ts
import { EventEmitter as EventEmitter2 } from "events";
import WebSocket2 from "ws";
var FUTURES_TESTNET_WS_URL = "wss://stream.binancefuture.com/ws";
var FUTURES_MAINNET_WS_URL = "wss://fstream.binance.com/ws";
var BASE_RECONNECT_DELAY_MS2 = 1e3;
var MAX_RETRIES2 = 5;
function detectStreamType(streamName) {
  if (streamName.includes("@ticker")) return "ticker";
  if (streamName.includes("@depth")) return "depth";
  if (streamName.includes("@kline")) return "kline";
  return null;
}
var BnWebSocket = class extends EventEmitter2 {
  url;
  _WS;
  _ws = null;
  _connected = false;
  _intentionalDisconnect = false;
  _retryCount = 0;
  _reconnectTimer = null;
  _subscriptions = [];
  constructor(config) {
    super();
    const market = config.market ?? "futures";
    if (market === "futures") {
      this.url = config.testnet ? FUTURES_TESTNET_WS_URL : FUTURES_MAINNET_WS_URL;
    } else {
      this.url = config.testnet ? "wss://testnet.binance.vision/ws" : "wss://stream.binance.com:9443/ws";
    }
    this._WS = config._WebSocket ?? WebSocket2;
  }
  // ── Public API ──────────────────────────────────────────────────────────
  get isConnected() {
    return this._connected;
  }
  get subscriptions() {
    return [...this._subscriptions];
  }
  connect() {
    return new Promise((resolve, reject) => {
      this._intentionalDisconnect = false;
      this._createConnection(resolve, reject);
    });
  }
  disconnect() {
    this._intentionalDisconnect = true;
    this._clearReconnectTimer();
    this._subscriptions = [];
    if (this._ws) {
      this._connected = false;
      this._ws.close();
      this._ws = null;
      this.emit("disconnected");
    }
  }
  subscribe(...streams) {
    if (!this._connected || !this._ws) {
      throw new Error("Not connected");
    }
    this._sendSubscription(streams);
    for (const s of streams) {
      if (!this._subscriptions.includes(s)) {
        this._subscriptions.push(s);
      }
    }
  }
  // ── Private ─────────────────────────────────────────────────────────────
  _createConnection(resolve, reject) {
    const ws = new this._WS(this.url);
    this._ws = ws;
    ws.on("open", () => {
      this._connected = true;
      this._retryCount = 0;
      this.emit("connected");
      resolve?.();
    });
    ws.on("message", (data) => {
      this._handleMessage(data);
    });
    ws.on("ping", () => {
      if (this._ws && this._connected) {
        this._ws.pong();
      }
    });
    ws.on("error", (err) => {
      if (!this._connected && reject) {
        reject(err);
        return;
      }
      this.emit("error", err);
    });
    ws.on("close", (_code, _reason) => {
      this._connected = false;
      if (!this._intentionalDisconnect) {
        this._scheduleReconnect();
      }
    });
  }
  _handleMessage(data) {
    let text;
    if (Buffer.isBuffer(data)) {
      text = data.toString("utf-8");
    } else if (typeof data === "string") {
      text = data;
    } else {
      return;
    }
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      this.emit("error", new Error(`Failed to parse WebSocket message: ${text.slice(0, 100)}`));
      return;
    }
    const stream = parsed["stream"];
    if (typeof stream !== "string") {
      return;
    }
    const rawData = parsed["data"];
    if (!rawData) return;
    const streamType = detectStreamType(stream);
    if (streamType) {
      this.emit(streamType, rawData);
    }
  }
  _scheduleReconnect() {
    if (this._retryCount >= MAX_RETRIES2) {
      this.emit("maxRetriesReached");
      return;
    }
    const delay = BASE_RECONNECT_DELAY_MS2 * Math.pow(2, this._retryCount);
    this._retryCount++;
    this._reconnectTimer = setTimeout(() => {
      this._reconnect();
    }, delay);
  }
  _reconnect() {
    const subs = [...this._subscriptions];
    this._createConnection(
      () => {
        if (subs.length > 0) {
          this._sendSubscription(subs);
        }
        this._subscriptions = subs;
      },
      () => {
      }
    );
  }
  _sendSubscription(streams) {
    const msg = {
      method: "SUBSCRIBE",
      params: streams,
      id: Date.now()
    };
    this._ws?.send(JSON.stringify(msg));
  }
  _clearReconnectTimer() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }
};

// src/exchanges/alpaca.ts
function toAlpacaTimeframe(interval) {
  switch (interval) {
    case "1m":
      return "1Min";
    case "5m":
      return "5Min";
    case "15m":
      return "15Min";
    case "1h":
      return "1Hour";
    case "1d":
      return "1Day";
    default:
      return "1Min";
  }
}
function toAlpacaTif(orderType) {
  switch (orderType) {
    case "ALO":
      return "day";
    // ALO not available — map to day limit
    case "GTC":
      return "gtc";
    case "IOC":
      return "ioc";
  }
}
function fromAlpacaTif(tif) {
  switch (tif) {
    case "gtc":
      return "GTC";
    case "ioc":
      return "IOC";
    default:
      return "GTC";
  }
}
function toOrderStatus(alpacaStatus) {
  switch (alpacaStatus) {
    case "filled":
      return "FILLED";
    case "partially_filled":
      return "PARTIAL";
    case "rejected":
    case "canceled":
    case "expired":
    case "suspended":
      return "REJECTED";
    default:
      return "OPEN";
  }
}
var AlpacaAdapter = class {
  name = "Alpaca";
  tradingUrl;
  dataUrl;
  apiKey;
  apiSecret;
  paper;
  dataFeed;
  // Injected in tests via _fetch; production uses global fetch
  _fetch = (...args) => fetch(...args);
  constructor(config) {
    this.paper = config.paper;
    this.tradingUrl = config.paper ? "https://paper-api.alpaca.markets" : "https://api.alpaca.markets";
    this.dataUrl = "https://data.alpaca.markets";
    this.apiKey = config.apiKey;
    this.apiSecret = config.apiSecret;
    this.dataFeed = config.dataFeed ?? "iex";
  }
  // ── Public interface ───────────────────────────────────────────────────────
  async getTicker(symbol) {
    const snapshot = await this.dataGet(
      `/v2/stocks/${symbol}/snapshot?feed=${this.dataFeed}`
    );
    const bid = snapshot.latestQuote.bp;
    const ask = snapshot.latestQuote.ap;
    const mid = (bid + ask) / 2;
    const lastPrice = snapshot.latestTrade.p;
    const volume24h = snapshot.dailyBar.v;
    return {
      symbol,
      mid,
      bid,
      ask,
      lastPrice,
      volume24h,
      openInterest: 0,
      fundingRate: 0,
      timestamp: Date.now()
    };
  }
  async getOrderBook(symbol, _depth) {
    const resp = await this.dataGet(
      `/v2/stocks/${symbol}/quotes/latest?feed=${this.dataFeed}`
    );
    const q = resp.quote;
    return {
      symbol,
      bids: [{ price: q.bp, size: q.bs }],
      asks: [{ price: q.ap, size: q.as }],
      timestamp: Date.now()
    };
  }
  async getCandles(symbol, interval, limit) {
    const timeframe = toAlpacaTimeframe(interval);
    const resp = await this.dataGet(
      `/v2/stocks/${symbol}/bars?timeframe=${timeframe}&limit=${limit}&feed=${this.dataFeed}`
    );
    const bars = resp.bars ?? [];
    return bars.slice(-limit).map((bar) => ({
      timestamp: new Date(bar.t).getTime(),
      open: bar.o,
      high: bar.h,
      low: bar.l,
      close: bar.c,
      volume: bar.v
    }));
  }
  async getBalances() {
    const account = await this.tradingGet("/v2/account");
    return [
      {
        currency: "USD",
        available: parseFloat(account.cash),
        total: parseFloat(account.equity),
        unrealizedPnl: parseFloat(account.unrealized_pl)
      }
    ];
  }
  async getPositions() {
    const positions = await this.tradingGet("/v2/positions");
    return positions.map((p) => {
      const qty = parseFloat(p.qty);
      return {
        symbol: p.symbol,
        side: qty >= 0 ? "LONG" : "SHORT",
        size: Math.abs(qty),
        entryPrice: parseFloat(p.avg_entry_price),
        markPrice: parseFloat(p.current_price),
        unrealizedPnl: parseFloat(p.unrealized_pl),
        leverage: 1,
        liquidationPrice: null
      };
    });
  }
  async placeOrder(order) {
    const body = {
      symbol: order.symbol,
      qty: String(order.size),
      side: order.side.toLowerCase(),
      type: "limit",
      time_in_force: toAlpacaTif(order.orderType),
      limit_price: String(order.price),
      ...order.clientOrderId ? { client_order_id: order.clientOrderId } : {}
    };
    const resp = await this.tradingPost("/v2/orders", body);
    return {
      orderId: resp.id,
      status: toOrderStatus(resp.status),
      filledSize: parseFloat(resp.filled_qty),
      filledPrice: resp.filled_avg_price ? parseFloat(resp.filled_avg_price) : 0,
      timestamp: new Date(resp.created_at).getTime()
    };
  }
  async cancelOrder(orderId) {
    await this.tradingDelete(`/v2/orders/${orderId}`);
  }
  async cancelAllOrders(symbol) {
    if (!symbol) {
      await this.tradingDelete("/v2/orders");
      return;
    }
    const openOrders = await this.tradingGet("/v2/orders?status=open");
    const matching = openOrders.filter((o) => o.symbol === symbol);
    for (const order of matching) {
      await this.tradingDelete(`/v2/orders/${order.id}`);
    }
  }
  async setStopLoss(symbol, side, triggerPrice, size) {
    const closeSide = side === "BUY" ? "sell" : "buy";
    const body = {
      symbol,
      qty: String(size),
      side: closeSide,
      type: "stop",
      time_in_force: "gtc",
      stop_price: String(triggerPrice)
    };
    const resp = await this.tradingPost("/v2/orders", body);
    return {
      orderId: resp.id,
      status: toOrderStatus(resp.status),
      filledSize: parseFloat(resp.filled_qty),
      filledPrice: resp.filled_avg_price ? parseFloat(resp.filled_avg_price) : 0,
      timestamp: new Date(resp.created_at).getTime()
    };
  }
  async getOpenOrders(symbol) {
    const rawOrders = await this.tradingGet("/v2/orders?status=open");
    const filtered = symbol ? rawOrders.filter((o) => o.symbol === symbol) : rawOrders;
    return filtered.map((o) => ({
      orderId: o.id,
      symbol: o.symbol,
      side: o.side === "buy" ? "BUY" : "SELL",
      price: parseFloat(o.limit_price),
      size: parseFloat(o.qty),
      filledSize: parseFloat(o.filled_qty),
      orderType: fromAlpacaTif(o.time_in_force),
      timestamp: new Date(o.created_at).getTime()
    }));
  }
  async getExchangeInfo() {
    const assets = await this.tradingGet("/v2/assets?status=active");
    const supportedSymbols = [];
    const minOrderSizes = {};
    const tickSizes = {};
    for (const asset of assets) {
      if (!asset.tradable) continue;
      supportedSymbols.push(asset.symbol);
      minOrderSizes[asset.symbol] = parseFloat(asset.min_order_size || "1");
      tickSizes[asset.symbol] = parseFloat(asset.price_increment || "0.01");
    }
    return {
      name: "Alpaca",
      testnet: this.paper,
      supportedSymbols,
      minOrderSizes,
      tickSizes
    };
  }
  // ── Private helpers ────────────────────────────────────────────────────────
  authHeaders() {
    return {
      "APCA-API-KEY-ID": this.apiKey,
      "APCA-API-SECRET-KEY": this.apiSecret,
      "Content-Type": "application/json"
    };
  }
  async tradingGet(path) {
    const resp = await this._fetch(`${this.tradingUrl}${path}`, {
      headers: this.authHeaders()
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Alpaca trading API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }
  async tradingPost(path, body) {
    const resp = await this._fetch(`${this.tradingUrl}${path}`, {
      method: "POST",
      headers: this.authHeaders(),
      body: JSON.stringify(body)
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Alpaca trading API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }
  async tradingDelete(path) {
    const resp = await this._fetch(`${this.tradingUrl}${path}`, {
      method: "DELETE",
      headers: this.authHeaders()
    });
    if (!resp.ok && resp.status !== 204 && resp.status !== 207) {
      const text = await resp.text();
      throw new Error(`Alpaca trading API error ${resp.status}: ${text}`);
    }
  }
  async dataGet(path) {
    const resp = await this._fetch(`${this.dataUrl}${path}`, {
      headers: this.authHeaders()
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Alpaca data API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }
};

// src/exchanges/alpaca-market-hours.ts
function getETComponents(now = /* @__PURE__ */ new Date()) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    weekday: "short"
  });
  const parts = formatter.formatToParts(now);
  const get = (type) => {
    const part = parts.find((p) => p.type === type);
    return part?.value ?? "0";
  };
  const weekdayStr = get("weekday");
  const dayMap = {
    "Sun": 0,
    "Mon": 1,
    "Tue": 2,
    "Wed": 3,
    "Thu": 4,
    "Fri": 5,
    "Sat": 6
  };
  return {
    year: parseInt(get("year"), 10),
    month: parseInt(get("month"), 10),
    day: parseInt(get("day"), 10),
    hours: parseInt(get("hour"), 10),
    minutes: parseInt(get("minute"), 10),
    dayOfWeek: dayMap[weekdayStr] ?? 0
  };
}
var PRE_MARKET_OPEN = 4 * 60;
var REGULAR_OPEN = 9 * 60 + 30;
var REGULAR_CLOSE = 16 * 60;
var POST_MARKET_CLOSE = 20 * 60;
function getCurrentSession(now) {
  const et = getETComponents(now);
  if (et.dayOfWeek === 0 || et.dayOfWeek === 6) return "CLOSED";
  const timeMinutes = et.hours * 60 + et.minutes;
  if (timeMinutes >= PRE_MARKET_OPEN && timeMinutes < REGULAR_OPEN) return "PRE_MARKET";
  if (timeMinutes >= REGULAR_OPEN && timeMinutes < REGULAR_CLOSE) return "REGULAR";
  if (timeMinutes >= REGULAR_CLOSE && timeMinutes < POST_MARKET_CLOSE) return "POST_MARKET";
  return "CLOSED";
}
function isMarketOpen(now) {
  return getCurrentSession(now) === "REGULAR";
}
function getNextMarketOpen(now) {
  const currentDate = now ?? /* @__PURE__ */ new Date();
  const et = getETComponents(currentDate);
  const timeMinutes = et.hours * 60 + et.minutes;
  let targetDay = et.day;
  let targetMonth = et.month;
  let targetYear = et.year;
  const isWeekday = et.dayOfWeek >= 1 && et.dayOfWeek <= 5;
  const isBeforeOpen = timeMinutes < REGULAR_OPEN;
  if (isWeekday && isBeforeOpen) {
  } else {
    let daysToAdd = 1;
    let nextDow = et.dayOfWeek + 1;
    if (!isWeekday) {
      if (et.dayOfWeek === 6) {
        daysToAdd = 2;
        nextDow = 1;
      } else {
        daysToAdd = 1;
        nextDow = 1;
      }
    } else {
      if (nextDow === 6) {
        daysToAdd = 3;
      } else if (nextDow === 0) {
        daysToAdd = 2;
      }
    }
    const tempDate = new Date(targetYear, targetMonth - 1, targetDay + daysToAdd);
    targetDay = tempDate.getDate();
    targetMonth = tempDate.getMonth() + 1;
    targetYear = tempDate.getFullYear();
    void nextDow;
  }
  const noonTarget = new Date(Date.UTC(targetYear, targetMonth - 1, targetDay, 17, 0, 0));
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    hour12: false
  });
  const etHourAtNoon = parseInt(formatter.format(noonTarget), 10);
  const isDST = etHourAtNoon === 13;
  const offsetHours = isDST ? 4 : 5;
  return new Date(Date.UTC(targetYear, targetMonth - 1, targetDay, 9 + offsetHours, 30, 0));
}
function canTrade(session, extendedHours) {
  if (session === "REGULAR") return true;
  if (session === "CLOSED") return false;
  return extendedHours;
}

// src/strategies/base-strategy.ts
var BaseStrategy = class {
};

// src/strategies/mm/simple-mm.ts
var SimpleMM = class extends BaseStrategy {
  name = "simple-mm";
  onTick(ctx) {
    const { ticker, positions, config } = ctx;
    const mid = ticker.mid;
    const symbol = ticker.symbol;
    const spreadBps = typeof config.params["spread_bps"] === "number" ? config.params["spread_bps"] : 10;
    const orderSize = typeof config.params["order_size"] === "number" ? config.params["order_size"] : 0.1;
    const maxPosition = typeof config.params["max_position"] === "number" ? config.params["max_position"] : 1;
    const halfSpread = mid * spreadBps / 1e4 / 2;
    const bidPrice = mid - halfSpread;
    const askPrice = mid + halfSpread;
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    const decisions = [];
    if (netPosition < maxPosition) {
      decisions.push({
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "ALO",
        confidence: 50,
        reason: `SimpleMM bid at ${bidPrice.toFixed(4)} (spread ${spreadBps}bps)`,
        stopLoss: bidPrice
      });
    }
    if (netPosition > -maxPosition) {
      decisions.push({
        action: "SELL",
        symbol,
        size: orderSize,
        orderType: "ALO",
        confidence: 50,
        reason: `SimpleMM ask at ${askPrice.toFixed(4)} (spread ${spreadBps}bps)`,
        stopLoss: askPrice
      });
    }
    if (decisions.length === 0) {
      decisions.push({
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "SimpleMM: max position reached on both sides"
      });
    }
    return decisions;
  }
};

// src/strategies/math-utils.ts
function computeSigma(candles, window) {
  const slice = window !== void 0 ? candles.slice(-window) : candles;
  if (slice.length < 2) return 1e-3;
  const logReturns = [];
  for (let i = 1; i < slice.length; i++) {
    const prev = slice[i - 1]?.close;
    const curr = slice[i]?.close;
    if (prev !== void 0 && curr !== void 0 && prev > 0 && curr > 0) {
      logReturns.push(Math.log(curr / prev));
    }
  }
  if (logReturns.length === 0) return 1e-3;
  const mean = logReturns.reduce((s, r) => s + r, 0) / logReturns.length;
  const variance = logReturns.reduce((s, r) => s + (r - mean) ** 2, 0) / logReturns.length;
  return Math.sqrt(variance);
}
function getParam(params, key, defaultValue) {
  const val = params[key];
  return typeof val === "number" ? val : defaultValue;
}

// src/strategies/mm/avellaneda-mm.ts
function classifyVol(sigma) {
  if (sigma < 1e-3) return "quiet";
  if (sigma < 5e-3) return "normal";
  if (sigma < 0.01) return "volatile";
  return "extreme";
}
function volMultiplier(regime) {
  switch (regime) {
    case "quiet":
      return 0.8;
    case "normal":
      return 1;
    case "volatile":
      return 1.5;
    case "extreme":
      return 2;
  }
}
var AvellanedaMM = class extends BaseStrategy {
  name = "avellaneda-mm";
  onTick(ctx) {
    const { ticker, positions, candles, config } = ctx;
    const mid = ticker.mid;
    const symbol = ticker.symbol;
    const gamma = typeof config.params["gamma"] === "number" ? config.params["gamma"] : 0.1;
    const orderSize = typeof config.params["order_size"] === "number" ? config.params["order_size"] : 0.1;
    const maxPosition = typeof config.params["max_position"] === "number" ? config.params["max_position"] : 1;
    const timeHorizon = typeof config.params["time_horizon"] === "number" ? config.params["time_horizon"] : 1;
    const T = timeHorizon;
    const sigma = computeSigma(candles);
    const sigma2 = sigma ** 2;
    const q = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    const reservationPrice = mid - q * gamma * sigma2 * T;
    const k = 1.5;
    const optimalSpread = gamma * sigma2 * T + 2 / gamma * Math.log(1 + gamma / k);
    const regime = classifyVol(sigma);
    let spreadMultiplier = volMultiplier(regime);
    const hasDrawdown = positions.filter((p) => p.symbol === symbol).some((p) => p.unrealizedPnl < 0);
    if (hasDrawdown) {
      spreadMultiplier *= 1.5;
    }
    const adjustedSpread = optimalSpread * spreadMultiplier;
    const bidPrice = reservationPrice - adjustedSpread / 2;
    const askPrice = reservationPrice + adjustedSpread / 2;
    const decisions = [];
    if (q < maxPosition) {
      decisions.push({
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "ALO",
        confidence: 60,
        reason: `AvellanedaMM bid=${bidPrice.toFixed(4)} rp=${reservationPrice.toFixed(4)} sigma=${sigma.toFixed(6)} regime=${regime}`,
        stopLoss: bidPrice
      });
    }
    if (q > -maxPosition) {
      decisions.push({
        action: "SELL",
        symbol,
        size: orderSize,
        orderType: "ALO",
        confidence: 60,
        reason: `AvellanedaMM ask=${askPrice.toFixed(4)} rp=${reservationPrice.toFixed(4)} sigma=${sigma.toFixed(6)} regime=${regime}`,
        stopLoss: askPrice
      });
    }
    if (decisions.length === 0) {
      decisions.push({
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "AvellanedaMM: max position reached on both sides"
      });
    }
    return decisions;
  }
};

// src/strategies/mm/engine-mm.ts
function volMultiplier2(sigma) {
  if (sigma < 1e-3) return 0.8;
  if (sigma < 5e-3) return 1;
  if (sigma < 0.01) return 1.5;
  return 2;
}
function computeMicroPrice(topBid, topAsk) {
  const totalSize = topBid.size + topAsk.size;
  if (totalSize === 0) return (topBid.price + topAsk.price) / 2;
  return (topBid.price * topAsk.size + topAsk.price * topBid.size) / totalSize;
}
function computeVwap(candles) {
  let sumPV = 0;
  let sumV = 0;
  for (const c of candles) {
    sumPV += c.close * c.volume;
    sumV += c.volume;
  }
  return sumV > 0 ? sumPV / sumV : 0;
}
function computeOfi(topBid, topAsk) {
  const total = topBid.size + topAsk.size;
  if (total === 0) return 0;
  return (topBid.size / total - 0.5) * 2;
}
function computeSma(candles, period) {
  const slice = candles.slice(-period);
  if (slice.length === 0) return 0;
  return slice.reduce((s, c) => s + c.close, 0) / slice.length;
}
var EngineMM = class extends BaseStrategy {
  name = "engine-mm";
  onTick(ctx) {
    const { ticker, positions, candles, config, orderBook } = ctx;
    const mid = ticker.mid;
    const symbol = ticker.symbol;
    const baseSpreadBps = getParam(config.params, "base_spread_bps", 10);
    const orderSize = getParam(config.params, "order_size", 0.1);
    const maxPosition = getParam(config.params, "max_position", 1);
    const wMicro = getParam(config.params, "w_micro", 0.4);
    const wVwap = getParam(config.params, "w_vwap", 0.2);
    const wOfi = getParam(config.params, "w_ofi", 0.2);
    const wMeanRev = getParam(config.params, "w_mean_rev", 0.2);
    const ofiSensitivity = getParam(config.params, "ofi_sensitivity", 0.5);
    const meanRevPeriod = getParam(config.params, "mean_rev_period", 20);
    const topBid = orderBook.bids[0];
    const topAsk = orderBook.asks[0];
    if (!topBid || !topAsk) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "EngineMM: no order book data"
      }];
    }
    const microPrice = computeMicroPrice(topBid, topAsk);
    const vwap = computeVwap(candles);
    const ofi = computeOfi(topBid, topAsk);
    const sma = computeSma(candles, meanRevPeriod);
    const microAdj = microPrice;
    const vwapAdj = vwap > 0 ? vwap : mid;
    const ofiAdj = mid + ofi * mid * 1e-3;
    const meanRevAdj = sma > 0 ? sma : mid;
    const totalWeight = wMicro + wVwap + wOfi + wMeanRev;
    const fairValue = totalWeight > 0 ? (wMicro * microAdj + wVwap * vwapAdj + wOfi * ofiAdj + wMeanRev * meanRevAdj) / totalWeight : mid;
    const sigma = computeSigma(candles);
    const volMult = volMultiplier2(sigma);
    const absOfi = Math.abs(ofi);
    const dynamicSpread = mid * baseSpreadBps / 1e4 * (1 + absOfi * ofiSensitivity) * volMult;
    const bidPrice = fairValue - dynamicSpread / 2;
    const askPrice = fairValue + dynamicSpread / 2;
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    const decisions = [];
    if (netPosition < maxPosition) {
      decisions.push({
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "ALO",
        confidence: 55,
        reason: `EngineMM bid fv=${fairValue.toFixed(2)} micro=${microPrice.toFixed(2)} ofi=${ofi.toFixed(3)} sigma=${sigma.toFixed(6)}`,
        stopLoss: bidPrice
      });
    }
    if (netPosition > -maxPosition) {
      decisions.push({
        action: "SELL",
        symbol,
        size: orderSize,
        orderType: "ALO",
        confidence: 55,
        reason: `EngineMM ask fv=${fairValue.toFixed(2)} micro=${microPrice.toFixed(2)} ofi=${ofi.toFixed(3)} sigma=${sigma.toFixed(6)}`,
        stopLoss: askPrice
      });
    }
    if (decisions.length === 0) {
      decisions.push({
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "EngineMM: max position reached on both sides"
      });
    }
    return decisions;
  }
};

// src/strategies/mm/regime-mm.ts
var REGIME_PARAMS = {
  LOW: { spreadMultiplier: 0.5, sizeMultiplier: 2, inventorySkewFactor: 0 },
  NORMAL: { spreadMultiplier: 1, sizeMultiplier: 1, inventorySkewFactor: 0.3 },
  HIGH: { spreadMultiplier: 2, sizeMultiplier: 0.5, inventorySkewFactor: 0.7 },
  EXTREME: { spreadMultiplier: 3, sizeMultiplier: 0.25, inventorySkewFactor: 1 }
};
var HYSTERESIS_TICKS = 3;
function classifyRegime(sigma) {
  if (sigma < 1e-3) return "LOW";
  if (sigma < 5e-3) return "NORMAL";
  if (sigma < 0.015) return "HIGH";
  return "EXTREME";
}
var RegimeMM = class extends BaseStrategy {
  name = "regime-mm";
  currentRegime = "NORMAL";
  pendingRegime = null;
  pendingCount = 0;
  onTick(ctx) {
    const { ticker, positions, candles, config } = ctx;
    const mid = ticker.mid;
    const symbol = ticker.symbol;
    const baseSpreadBps = getParam(config.params, "base_spread_bps", 10);
    const orderSize = getParam(config.params, "order_size", 0.1);
    const maxPosition = getParam(config.params, "max_position", 1);
    const volWindow = getParam(config.params, "vol_window", 20);
    const sigma = computeSigma(candles, volWindow);
    const rawRegime = classifyRegime(sigma);
    if (rawRegime !== this.currentRegime) {
      if (rawRegime === this.pendingRegime) {
        this.pendingCount++;
        if (this.pendingCount >= HYSTERESIS_TICKS) {
          this.currentRegime = rawRegime;
          this.pendingRegime = null;
          this.pendingCount = 0;
        }
      } else {
        this.pendingRegime = rawRegime;
        this.pendingCount = 1;
      }
    } else {
      this.pendingRegime = null;
      this.pendingCount = 0;
    }
    const regime = REGIME_PARAMS[this.currentRegime];
    const halfSpread = mid * baseSpreadBps / 1e4 / 2 * regime.spreadMultiplier;
    const adjustedSize = orderSize * regime.sizeMultiplier;
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    const skewShift = netPosition * regime.inventorySkewFactor * mid * 1e-3;
    const fairValue = mid - skewShift;
    const bidPrice = fairValue - halfSpread;
    const askPrice = fairValue + halfSpread;
    const decisions = [];
    if (netPosition < maxPosition) {
      decisions.push({
        action: "BUY",
        symbol,
        size: adjustedSize,
        orderType: "ALO",
        confidence: 55,
        reason: `RegimeMM bid regime=${this.currentRegime} sigma=${sigma.toFixed(6)} spread_mult=${regime.spreadMultiplier}`,
        stopLoss: bidPrice
      });
    }
    if (netPosition > -maxPosition) {
      decisions.push({
        action: "SELL",
        symbol,
        size: adjustedSize,
        orderType: "ALO",
        confidence: 55,
        reason: `RegimeMM ask regime=${this.currentRegime} sigma=${sigma.toFixed(6)} spread_mult=${regime.spreadMultiplier}`,
        stopLoss: askPrice
      });
    }
    if (decisions.length === 0) {
      decisions.push({
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: `RegimeMM: max position reached on both sides (regime=${this.currentRegime})`
      });
    }
    return decisions;
  }
};

// src/strategies/mm/grid-mm.ts
var GridMM = class extends BaseStrategy {
  name = "grid-mm";
  onTick(ctx) {
    const { ticker, positions, config } = ctx;
    const mid = ticker.mid;
    const symbol = ticker.symbol;
    const gridLevels = getParam(config.params, "grid_levels", 5);
    const gridSpacingBps = getParam(config.params, "grid_spacing_bps", 20);
    const sizePerLevel = getParam(config.params, "size_per_level", 0.05);
    const maxPosition = getParam(config.params, "max_position", 1);
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    const buyCapacity = maxPosition - netPosition;
    const sellCapacity = maxPosition + netPosition;
    const decisions = [];
    for (let level = 1; level <= gridLevels; level++) {
      const cumulativeSize = level * sizePerLevel;
      const confidence = Math.max(30, 65 - (level - 1) * 5);
      const bidPrice = mid * (1 - level * gridSpacingBps / 1e4);
      if (cumulativeSize <= buyCapacity + 1e-10) {
        decisions.push({
          action: "BUY",
          symbol,
          size: sizePerLevel,
          orderType: "ALO",
          confidence,
          reason: `GridMM bid L${level} at ${bidPrice.toFixed(2)} (spacing ${gridSpacingBps}bps)`,
          stopLoss: bidPrice
        });
      }
      const askPrice = mid * (1 + level * gridSpacingBps / 1e4);
      if (cumulativeSize <= sellCapacity + 1e-10) {
        decisions.push({
          action: "SELL",
          symbol,
          size: sizePerLevel,
          orderType: "ALO",
          confidence,
          reason: `GridMM ask L${level} at ${askPrice.toFixed(2)} (spacing ${gridSpacingBps}bps)`,
          stopLoss: askPrice
        });
      }
    }
    if (decisions.length === 0) {
      decisions.push({
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "GridMM: max position reached on both sides"
      });
    }
    return decisions;
  }
};

// src/strategies/mm/liquidation-mm.ts
var LiquidationMM = class extends BaseStrategy {
  name = "liquidation-mm";
  prevOI = null;
  onTick(ctx) {
    const { ticker, positions, candles, config } = ctx;
    const mid = ticker.mid;
    const symbol = ticker.symbol;
    const liqDistancePct = getParam(config.params, "liq_distance_pct", 5);
    const orderSize = getParam(config.params, "order_size", 0.1);
    const maxPosition = getParam(config.params, "max_position", 1);
    const fundingThreshold = getParam(config.params, "funding_threshold", 1e-4);
    const oiSurgeThreshold = getParam(config.params, "oi_surge_threshold", 5);
    const spreadBps = getParam(config.params, "spread_bps", 10);
    const fundingRate = ticker.fundingRate;
    const currentOI = ticker.openInterest;
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    let oiChangePct = 0;
    if (this.prevOI !== null && this.prevOI > 0) {
      oiChangePct = (currentOI - this.prevOI) / this.prevOI * 100;
    }
    this.prevOI = currentOI;
    const oiDropPct = Math.max(0, -oiChangePct);
    const sizeMultiplier = oiDropPct > oiSurgeThreshold ? 1 + (oiDropPct - oiSurgeThreshold) / 10 : 1;
    const adjustedSize = orderSize * sizeMultiplier;
    const sigma = computeSigma(candles);
    const volBuffer = sigma * mid * 0.5;
    const decisions = [];
    const absFunding = Math.abs(fundingRate);
    if (absFunding < fundingThreshold) {
      const halfSpread = mid * spreadBps / 1e4 / 2;
      const bidPrice = mid - halfSpread;
      const askPrice = mid + halfSpread;
      if (netPosition < maxPosition) {
        decisions.push({
          action: "BUY",
          symbol,
          size: adjustedSize,
          orderType: "GTC",
          confidence: 40,
          reason: `LiquidationMM neutral bid, funding=${fundingRate.toFixed(6)} (below threshold)`,
          stopLoss: bidPrice
        });
      }
      if (netPosition > -maxPosition) {
        decisions.push({
          action: "SELL",
          symbol,
          size: adjustedSize,
          orderType: "GTC",
          confidence: 40,
          reason: `LiquidationMM neutral ask, funding=${fundingRate.toFixed(6)} (below threshold)`,
          stopLoss: askPrice
        });
      }
    } else if (fundingRate > 0) {
      const liqZone = mid * (1 - liqDistancePct / 100);
      const bidPrice = liqZone + volBuffer;
      if (netPosition < maxPosition) {
        decisions.push({
          action: "BUY",
          symbol,
          size: adjustedSize,
          orderType: "GTC",
          confidence: 60,
          reason: `LiquidationMM long-squeeze bid near liqZone=${liqZone.toFixed(2)} funding=${fundingRate.toFixed(6)} oiChg=${oiChangePct.toFixed(1)}%`,
          stopLoss: bidPrice
        });
      }
      if (netPosition > -maxPosition) {
        const askPrice = mid + mid * spreadBps / 1e4;
        decisions.push({
          action: "SELL",
          symbol,
          size: adjustedSize,
          orderType: "GTC",
          confidence: 45,
          reason: `LiquidationMM hedge ask, funding=${fundingRate.toFixed(6)}`,
          stopLoss: askPrice
        });
      }
    } else {
      const liqZone = mid * (1 + liqDistancePct / 100);
      const askPrice = liqZone - volBuffer;
      if (netPosition > -maxPosition) {
        decisions.push({
          action: "SELL",
          symbol,
          size: adjustedSize,
          orderType: "GTC",
          confidence: 60,
          reason: `LiquidationMM short-squeeze ask near liqZone=${liqZone.toFixed(2)} funding=${fundingRate.toFixed(6)} oiChg=${oiChangePct.toFixed(1)}%`,
          stopLoss: askPrice
        });
      }
      if (netPosition < maxPosition) {
        const bidPrice = mid - mid * spreadBps / 1e4;
        decisions.push({
          action: "BUY",
          symbol,
          size: adjustedSize,
          orderType: "GTC",
          confidence: 45,
          reason: `LiquidationMM hedge bid, funding=${fundingRate.toFixed(6)}`,
          stopLoss: bidPrice
        });
      }
    }
    if (decisions.length === 0) {
      decisions.push({
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "LiquidationMM: max position reached on both sides"
      });
    }
    return decisions;
  }
};

// src/strategies/arb/funding-arb.ts
var FundingArb = class extends BaseStrategy {
  name = "funding-arb";
  onTick(ctx) {
    const { ticker, positions, config } = ctx;
    const symbol = ticker.symbol;
    const minSpread = typeof config.params["min_spread"] === "number" ? config.params["min_spread"] : 1e-4;
    const orderSize = typeof config.params["order_size"] === "number" ? config.params["order_size"] : 0.5;
    const maxPosition = typeof config.params["max_position"] === "number" ? config.params["max_position"] : 2;
    const peerFundingRate = config.params["peer_funding_rate"];
    if (typeof peerFundingRate !== "number") {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "FundingArb: peer_funding_rate not provided"
      }];
    }
    const primaryFunding = ticker.fundingRate;
    const spread = primaryFunding - peerFundingRate;
    if (Math.abs(spread) < minSpread) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: `FundingArb: funding spread ${(spread * 1e4).toFixed(2)}bps below threshold ${(minSpread * 1e4).toFixed(2)}bps`
      }];
    }
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    if (spread > 0) {
      if (netPosition <= -maxPosition) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: `FundingArb: max short position reached (${netPosition.toFixed(4)})`
        }];
      }
      const confidence = Math.min(90, Math.round(Math.abs(spread) / minSpread * 30));
      return [{
        action: "SELL",
        symbol,
        size: orderSize,
        orderType: "GTC",
        confidence,
        reason: `FundingArb: short primary, funding spread +${(spread * 1e4).toFixed(2)}bps (primary ${(primaryFunding * 1e4).toFixed(2)}bps > peer ${(peerFundingRate * 1e4).toFixed(2)}bps)`
      }];
    } else {
      if (netPosition >= maxPosition) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: `FundingArb: max long position reached (${netPosition.toFixed(4)})`
        }];
      }
      const confidence = Math.min(90, Math.round(Math.abs(spread) / minSpread * 30));
      return [{
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "GTC",
        confidence,
        reason: `FundingArb: long primary, funding spread ${(spread * 1e4).toFixed(2)}bps (primary ${(primaryFunding * 1e4).toFixed(2)}bps < peer ${(peerFundingRate * 1e4).toFixed(2)}bps)`
      }];
    }
  }
};

// src/strategies/arb/basis-arb.ts
var BasisArb = class extends BaseStrategy {
  name = "basis-arb";
  onTick(ctx) {
    const { ticker, positions, config } = ctx;
    const symbol = ticker.symbol;
    const perpPrice = ticker.mid;
    const minBasisBps = typeof config.params["min_basis_bps"] === "number" ? config.params["min_basis_bps"] : 20;
    const orderSize = typeof config.params["order_size"] === "number" ? config.params["order_size"] : 0.5;
    const maxPosition = typeof config.params["max_position"] === "number" ? config.params["max_position"] : 2;
    const spotPrice = config.params["spot_price"];
    if (typeof spotPrice !== "number" || spotPrice <= 0) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "BasisArb: spot_price not provided"
      }];
    }
    const basisBps = (perpPrice - spotPrice) / spotPrice * 1e4;
    if (Math.abs(basisBps) < minBasisBps) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: `BasisArb: basis ${basisBps.toFixed(2)}bps below threshold ${minBasisBps}bps`
      }];
    }
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    if (basisBps > 0) {
      if (netPosition <= -maxPosition) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: `BasisArb: max short position reached (${netPosition.toFixed(4)})`
        }];
      }
      const confidence = Math.min(90, Math.round(Math.abs(basisBps) / minBasisBps * 30));
      return [{
        action: "SELL",
        symbol,
        size: orderSize,
        orderType: "GTC",
        confidence,
        reason: `BasisArb: contango, short perp, basis +${basisBps.toFixed(2)}bps (perp=${perpPrice.toFixed(2)} > spot=${spotPrice.toFixed(2)})`
      }];
    } else {
      if (netPosition >= maxPosition) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: `BasisArb: max long position reached (${netPosition.toFixed(4)})`
        }];
      }
      const confidence = Math.min(90, Math.round(Math.abs(basisBps) / minBasisBps * 30));
      return [{
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "GTC",
        confidence,
        reason: `BasisArb: backwardation, long perp, basis ${basisBps.toFixed(2)}bps (perp=${perpPrice.toFixed(2)} < spot=${spotPrice.toFixed(2)})`
      }];
    }
  }
};

// src/strategies/signal/momentum-breakout.ts
function computeATR(candles, period) {
  if (candles.length < 2) return 0;
  const trs = [];
  for (let i = 1; i < candles.length; i++) {
    const prev = candles[i - 1];
    const curr = candles[i];
    const tr = Math.max(
      curr.high - curr.low,
      Math.abs(curr.high - prev.close),
      Math.abs(curr.low - prev.close)
    );
    trs.push(tr);
  }
  const recent = trs.slice(-period);
  return recent.reduce((s, v) => s + v, 0) / recent.length;
}
function detectBreakout(candles, lookback) {
  if (candles.length < lookback + 1) return null;
  const lastCandle = candles[candles.length - 1];
  const lookbackCandles = candles.slice(-(lookback + 1), -1);
  let highestHigh = -Infinity;
  let lowestLow = Infinity;
  for (const c of lookbackCandles) {
    if (c.high > highestHigh) highestHigh = c.high;
    if (c.low < lowestLow) lowestLow = c.low;
  }
  if (lastCandle.close > highestHigh) return "UP";
  if (lastCandle.close < lowestLow) return "DOWN";
  return null;
}
function volumeConfirmed(candles, threshold) {
  if (candles.length < 2) return false;
  const lastCandle = candles[candles.length - 1];
  const priorCandles = candles.slice(0, -1);
  const avgVolume = priorCandles.reduce((s, c) => s + c.volume, 0) / priorCandles.length;
  return lastCandle.volume > threshold * avgVolume;
}
var MomentumBreakout = class extends BaseStrategy {
  name = "momentum-breakout";
  onTick(ctx) {
    const { ticker, positions, candles, config } = ctx;
    const symbol = ticker.symbol;
    const atrPeriod = typeof config.params["atr_period"] === "number" ? config.params["atr_period"] : 14;
    const lookbackPeriod = typeof config.params["lookback_period"] === "number" ? config.params["lookback_period"] : 20;
    const volThreshold = typeof config.params["volume_threshold"] === "number" ? config.params["volume_threshold"] : 2;
    const atrMultiplier = typeof config.params["atr_multiplier"] === "number" ? config.params["atr_multiplier"] : 2;
    const orderSize = typeof config.params["order_size"] === "number" ? config.params["order_size"] : 0.1;
    const maxPosition = typeof config.params["max_position"] === "number" ? config.params["max_position"] : 1;
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    const breakout = detectBreakout(candles, lookbackPeriod);
    if (breakout === null) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "MomentumBreakout: no breakout detected"
      }];
    }
    if (!volumeConfirmed(candles, volThreshold)) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "MomentumBreakout: breakout detected but volume not confirmed"
      }];
    }
    const atr = computeATR(candles, atrPeriod);
    const entryPrice = ticker.lastPrice;
    if (breakout === "UP") {
      if (netPosition >= maxPosition) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: "MomentumBreakout: max long position reached"
        }];
      }
      return [{
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "IOC",
        confidence: 70,
        reason: `MomentumBreakout: UP breakout confirmed, ATR=${atr.toFixed(4)}`,
        stopLoss: entryPrice - atr * atrMultiplier
      }];
    }
    if (netPosition <= -maxPosition) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "MomentumBreakout: max short position reached"
      }];
    }
    return [{
      action: "SELL",
      symbol,
      size: orderSize,
      orderType: "IOC",
      confidence: 70,
      reason: `MomentumBreakout: DOWN breakout confirmed, ATR=${atr.toFixed(4)}`,
      stopLoss: entryPrice + atr * atrMultiplier
    }];
  }
};

// src/strategies/signal/mean-reversion.ts
function computeSMA(values, period) {
  if (values.length === 0) return 0;
  const slice = values.slice(-period);
  return slice.reduce((s, v) => s + v, 0) / slice.length;
}
function computeStdDev(values, mean) {
  if (values.length <= 1) return 0;
  const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}
function computeBollingerBands(candles, period, multiplier) {
  const closes = candles.map((c) => c.close);
  const recentCloses = closes.slice(-period);
  const sma = computeSMA(recentCloses, period);
  const stddev = computeStdDev(recentCloses, sma);
  const upper = sma + multiplier * stddev;
  const lower = sma - multiplier * stddev;
  return { sma, upper, lower, stddev };
}
var MeanReversion = class extends BaseStrategy {
  name = "mean-reversion";
  onTick(ctx) {
    const { ticker, positions, candles, config } = ctx;
    const symbol = ticker.symbol;
    const price = ticker.mid;
    const smaPeriod = typeof config.params["sma_period"] === "number" ? config.params["sma_period"] : 20;
    const bbMultiplier = typeof config.params["bb_multiplier"] === "number" ? config.params["bb_multiplier"] : 2;
    const orderSize = typeof config.params["order_size"] === "number" ? config.params["order_size"] : 0.1;
    const maxPosition = typeof config.params["max_position"] === "number" ? config.params["max_position"] : 1;
    const minDeviationPct = typeof config.params["min_deviation_pct"] === "number" ? config.params["min_deviation_pct"] : 0.5;
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    if (candles.length < smaPeriod) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "MeanReversion: insufficient candle data"
      }];
    }
    const bands = computeBollingerBands(candles, smaPeriod, bbMultiplier);
    if (price < bands.lower) {
      const deviationPct = (bands.lower - price) / bands.lower * 100;
      if (deviationPct < minDeviationPct) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: `MeanReversion: deviation ${deviationPct.toFixed(2)}% below min ${minDeviationPct}%`
        }];
      }
      if (netPosition >= maxPosition) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: "MeanReversion: max long position reached"
        }];
      }
      const confidence = Math.min(90, Math.round(50 + deviationPct * 10));
      return [{
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "GTC",
        confidence,
        reason: `MeanReversion: price below lower BB by ${deviationPct.toFixed(2)}%, SMA=${bands.sma.toFixed(2)}`,
        stopLoss: bands.lower - bands.stddev,
        takeProfit: bands.sma
      }];
    }
    if (price > bands.upper) {
      const deviationPct = (price - bands.upper) / bands.upper * 100;
      if (deviationPct < minDeviationPct) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: `MeanReversion: deviation ${deviationPct.toFixed(2)}% below min ${minDeviationPct}%`
        }];
      }
      if (netPosition <= -maxPosition) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: "MeanReversion: max short position reached"
        }];
      }
      const confidence = Math.min(90, Math.round(50 + deviationPct * 10));
      return [{
        action: "SELL",
        symbol,
        size: orderSize,
        orderType: "GTC",
        confidence,
        reason: `MeanReversion: price above upper BB by ${deviationPct.toFixed(2)}%, SMA=${bands.sma.toFixed(2)}`,
        stopLoss: bands.upper + bands.stddev,
        takeProfit: bands.sma
      }];
    }
    return [{
      action: "HOLD",
      symbol,
      size: 0,
      orderType: "GTC",
      confidence: 0,
      reason: `MeanReversion: price within bands [${bands.lower.toFixed(2)}, ${bands.upper.toFixed(2)}]`
    }];
  }
};

// src/strategies/signal/aggressive-taker.ts
function computeConviction(ctx) {
  const { ticker, candles } = ctx;
  const rsi = calculateRSI(candles, 14);
  let rsiScore;
  let direction;
  if (rsi < 25) {
    rsiScore = 25;
    direction = "BUY";
  } else if (rsi < 30) {
    rsiScore = 20;
    direction = "BUY";
  } else if (rsi < 40) {
    rsiScore = 10;
    direction = "BUY";
  } else if (rsi > 75) {
    rsiScore = 25;
    direction = "SELL";
  } else if (rsi > 70) {
    rsiScore = 20;
    direction = "SELL";
  } else if (rsi > 60) {
    rsiScore = 10;
    direction = "SELL";
  } else {
    rsiScore = 0;
    direction = rsi < 50 ? "BUY" : "SELL";
  }
  let volumeScore;
  if (ticker.volume24h > 5e6) volumeScore = 25;
  else if (ticker.volume24h > 2e6) volumeScore = 20;
  else if (ticker.volume24h > 1e6) volumeScore = 15;
  else if (ticker.volume24h > 5e5) volumeScore = 10;
  else volumeScore = 5;
  let oiScore;
  if (ticker.openInterest > 5e6) oiScore = 25;
  else if (ticker.openInterest > 2e6) oiScore = 20;
  else if (ticker.openInterest > 1e6) oiScore = 15;
  else if (ticker.openInterest > 5e5) oiScore = 10;
  else oiScore = 5;
  const absFunding = Math.abs(ticker.fundingRate);
  let fundingScore;
  const fundingAligned = ticker.fundingRate < 0 && direction === "BUY" || ticker.fundingRate > 0 && direction === "SELL";
  if (fundingAligned) {
    if (absFunding > 5e-3) fundingScore = 25;
    else if (absFunding > 1e-3) fundingScore = 20;
    else fundingScore = 15;
  } else {
    if (absFunding > 5e-3) fundingScore = 5;
    else fundingScore = 10;
  }
  const total = rsiScore + volumeScore + oiScore + fundingScore;
  return {
    total,
    direction,
    factors: { rsiScore, volumeScore, oiScore, fundingScore }
  };
}
var AggressiveTaker = class extends BaseStrategy {
  name = "aggressive-taker";
  onTick(ctx) {
    const { ticker, positions, config } = ctx;
    const symbol = ticker.symbol;
    const minConviction = typeof config.params["min_conviction"] === "number" ? config.params["min_conviction"] : 75;
    const orderSize = typeof config.params["order_size"] === "number" ? config.params["order_size"] : 0.1;
    const maxPosition = typeof config.params["max_position"] === "number" ? config.params["max_position"] : 0.5;
    const stopPct = typeof config.params["stop_pct"] === "number" ? config.params["stop_pct"] : 1;
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    const conviction = computeConviction(ctx);
    if (conviction.total < minConviction) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: `AggressiveTaker: conviction ${conviction.total} below threshold ${minConviction}`
      }];
    }
    if (conviction.direction === "BUY") {
      if (netPosition >= maxPosition) {
        return [{
          action: "HOLD",
          symbol,
          size: 0,
          orderType: "GTC",
          confidence: 0,
          reason: "AggressiveTaker: max long position reached"
        }];
      }
      const entryPrice2 = ticker.ask;
      return [{
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "IOC",
        confidence: conviction.total,
        reason: `AggressiveTaker: BUY conviction=${conviction.total} (rsi=${conviction.factors.rsiScore} vol=${conviction.factors.volumeScore} oi=${conviction.factors.oiScore} fund=${conviction.factors.fundingScore})`,
        stopLoss: entryPrice2 * (1 - stopPct / 100)
      }];
    }
    if (netPosition <= -maxPosition) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: "AggressiveTaker: max short position reached"
      }];
    }
    const entryPrice = ticker.bid;
    return [{
      action: "SELL",
      symbol,
      size: orderSize,
      orderType: "IOC",
      confidence: conviction.total,
      reason: `AggressiveTaker: SELL conviction=${conviction.total} (rsi=${conviction.factors.rsiScore} vol=${conviction.factors.volumeScore} oi=${conviction.factors.oiScore} fund=${conviction.factors.fundingScore})`,
      stopLoss: entryPrice * (1 + stopPct / 100)
    }];
  }
};

// src/strategies/llm-custom.ts
function formatNumber(n) {
  return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}
function formatPrice(n) {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function totalUnrealizedPnl(positions) {
  return positions.reduce((sum, p) => sum + p.unrealizedPnl, 0);
}
function primaryBalance(balances) {
  return balances[0] ?? null;
}
var LlmCustom = class extends BaseStrategy {
  name = "llm-custom";
  _fetch;
  constructor(fetchFn) {
    super();
    this._fetch = fetchFn ?? fetch;
  }
  buildMarketSnapshot(ctx) {
    const { ticker, orderBook, positions, balances } = ctx;
    const spread = ticker.ask - ticker.bid;
    const spreadPct = (spread / ticker.mid * 100).toFixed(3);
    const vol = ticker.volume24h >= 1e9 ? `$${(ticker.volume24h / 1e9).toFixed(1)}B` : ticker.volume24h >= 1e6 ? `$${(ticker.volume24h / 1e6).toFixed(1)}M` : `$${ticker.volume24h.toFixed(0)}`;
    const oi = ticker.openInterest >= 1e9 ? `$${(ticker.openInterest / 1e9).toFixed(1)}B` : ticker.openInterest >= 1e6 ? `$${(ticker.openInterest / 1e6).toFixed(1)}M` : `$${ticker.openInterest.toFixed(0)}`;
    const fundingPct = (ticker.fundingRate * 100).toFixed(3);
    const topBid = orderBook.bids[0];
    const topAsk = orderBook.asks[0];
    let snapshot = `## Market Data
`;
    snapshot += `Symbol: ${ticker.symbol} | Price: $${formatNumber(ticker.mid)} | 24h Volume: ${vol}
`;
    snapshot += `Bid: $${formatPrice(topBid?.price ?? ticker.bid)} | Ask: $${formatPrice(topAsk?.price ?? ticker.ask)} | Spread: ${spreadPct}%
`;
    snapshot += `Funding: ${fundingPct}% | OI: ${oi}
`;
    if (positions.length > 0) {
      snapshot += `
## Positions
`;
      for (const pos of positions) {
        const pnlSign = pos.unrealizedPnl >= 0 ? "+" : "";
        snapshot += `${pos.symbol} ${pos.side} ${pos.size} @ $${formatNumber(pos.entryPrice)} | PnL: ${pnlSign}$${pos.unrealizedPnl.toFixed(2)}
`;
      }
    }
    const bal = primaryBalance(balances);
    if (bal) {
      const unrealized = totalUnrealizedPnl(positions);
      const unrealizedStr = unrealized >= 0 ? `+$${unrealized.toFixed(2)}` : `-$${Math.abs(unrealized).toFixed(2)}`;
      snapshot += `
## Account
`;
      snapshot += `Equity: $${formatNumber(bal.total)} | Available: $${formatNumber(bal.available)} | Unrealized: ${unrealizedStr}
`;
    }
    return snapshot;
  }
  async callLlm(endpoint, systemPrompt, userMessage) {
    const response = await this._fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userMessage }
        ]
      })
    });
    if (!response.ok) {
      return null;
    }
    const data = await response.json();
    const content = data.choices[0]?.message?.content ?? "";
    try {
      return JSON.parse(content);
    } catch {
      const match = /```(?:json)?\s*([\s\S]*?)```/.exec(content);
      if (match?.[1]) {
        try {
          return JSON.parse(match[1]);
        } catch {
          return null;
        }
      }
      return null;
    }
  }
  validateDecision(raw, ctx) {
    const { ticker, balances, config } = ctx;
    const maxPositionPct = typeof config.params["max_position_pct"] === "number" ? config.params["max_position_pct"] : 10;
    const maxLeverage = typeof config.params["max_leverage"] === "number" ? config.params["max_leverage"] : 10;
    const action = raw.action;
    if (!["BUY", "SELL", "HOLD"].includes(action)) return null;
    if (action === "HOLD") {
      return {
        action: "HOLD",
        symbol: raw.symbol ?? ticker.symbol,
        size: 0,
        orderType: "GTC",
        confidence: raw.confidence ?? 0,
        reason: raw.reason ?? "LLM: hold"
      };
    }
    const equity = balances.reduce((sum, b) => sum + b.total, 0);
    const price = ticker.mid;
    const notionalValue = raw.size * price;
    const maxNotional = equity * (maxPositionPct / 100);
    if (notionalValue > maxNotional) {
      return null;
    }
    if (notionalValue > equity * 0.5) {
      return null;
    }
    const impliedLeverage = notionalValue / equity;
    if (impliedLeverage > maxLeverage) {
      return null;
    }
    return {
      action,
      symbol: raw.symbol ?? ticker.symbol,
      size: raw.size,
      orderType: "GTC",
      confidence: raw.confidence ?? 70,
      reason: raw.reason ?? "LLM decision",
      stopLoss: raw.stopLoss,
      takeProfit: raw.takeProfit
    };
  }
  holdDecision(ctx, reason) {
    return {
      action: "HOLD",
      symbol: ctx.ticker.symbol,
      size: 0,
      orderType: "GTC",
      confidence: 0,
      reason
    };
  }
  async onTick(ctx) {
    const { config } = ctx;
    const endpoint = typeof config.params["llm_endpoint"] === "string" ? config.params["llm_endpoint"] : "http://chat-proxy/v1/chat/completions";
    const systemPrompt = config.strategyPrompt ?? "You are a trading assistant. Respond ONLY with valid JSON matching the required schema.";
    const fullSystem = `${systemPrompt}

Respond ONLY with a JSON object in this exact format (no markdown, no explanation):
{"action":"BUY"|"SELL"|"HOLD","symbol":"<symbol>","size":<number>,"reason":"<string>","stopLoss":<number|null>,"takeProfit":<number|null>,"confidence":<0-100>}`;
    const snapshot = this.buildMarketSnapshot(ctx);
    try {
      const raw = await this.callLlm(endpoint, fullSystem, snapshot);
      if (!raw) {
        return [this.holdDecision(ctx, "LLM: no parseable response")];
      }
      const decision = this.validateDecision(raw, ctx);
      if (!decision) {
        return [this.holdDecision(ctx, "LLM: decision failed validation")];
      }
      return [decision];
    } catch {
      return [this.holdDecision(ctx, "LLM: error during inference")];
    }
  }
};

// src/strategies/prediction/prediction-mm.ts
function computeFairValue(yesPrice, noPrice, candles) {
  let fv = yesPrice;
  if (candles.length >= 2) {
    const recentCandles = candles.slice(-5);
    const firstCandle = recentCandles[0];
    const lastCandle = recentCandles[recentCandles.length - 1];
    const trendDelta = lastCandle.close - firstCandle.close;
    fv += trendDelta * 0.1;
  }
  return Math.max(0.01, Math.min(0.99, fv));
}
function computeInventorySkew(positions, symbol, maxPosition) {
  const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
  if (maxPosition === 0) return 0;
  return netPosition / maxPosition;
}
function generateQuotes(fairValue, spreadBps, skew, maxPrice, minPrice) {
  const halfSpread = fairValue * spreadBps / 1e4 / 2;
  const skewShift = halfSpread * skew;
  const yesBid = clamp2(fairValue - halfSpread - skewShift, minPrice, maxPrice);
  const yesAsk = clamp2(fairValue + halfSpread - skewShift, minPrice, maxPrice);
  const noFairValue = 1 - fairValue;
  const noBid = clamp2(noFairValue - halfSpread + skewShift, minPrice, maxPrice);
  const noAsk = clamp2(noFairValue + halfSpread + skewShift, minPrice, maxPrice);
  return {
    yesBid,
    yesAsk,
    noBid,
    noAsk,
    fairValue,
    inventorySkew: skew
  };
}
function clamp2(value, min, max) {
  return Math.max(min, Math.min(max, value));
}
var PredictionMM = class extends BaseStrategy {
  name = "prediction-mm";
  onTick(ctx) {
    const { ticker, positions, candles, config } = ctx;
    const spreadBps = typeof config.params["spread_bps"] === "number" ? config.params["spread_bps"] : 200;
    const orderSize = typeof config.params["order_size"] === "number" ? config.params["order_size"] : 10;
    const maxPosition = typeof config.params["max_position"] === "number" ? config.params["max_position"] : 100;
    const minEdge = typeof config.params["min_edge"] === "number" ? config.params["min_edge"] : 50;
    const skewFactor = typeof config.params["skew_factor"] === "number" ? config.params["skew_factor"] : 0.5;
    const symbol = ticker.symbol;
    const yesPrice = ticker.mid;
    const noPrice = 1 - yesPrice;
    const fairValue = computeFairValue(yesPrice, noPrice, candles);
    const rawSkew = computeInventorySkew(positions, symbol, maxPosition);
    const skew = rawSkew * skewFactor;
    const edgeBps = Math.abs(fairValue - yesPrice) * 1e4;
    if (edgeBps < minEdge) {
      return [{
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: `PredictionMM: edge ${edgeBps.toFixed(0)}bps < min ${minEdge}bps`
      }];
    }
    const quotes = generateQuotes(fairValue, spreadBps, skew, 0.99, 0.01);
    const netPosition = positions.filter((p) => p.symbol === symbol).reduce((sum, p) => sum + (p.side === "LONG" ? p.size : -p.size), 0);
    const confidence = Math.min(100, Math.round(edgeBps / 5));
    const decisions = [];
    const reasonBase = `PredictionMM: fv=${fairValue.toFixed(4)} skew=${skew.toFixed(4)}`;
    if (netPosition < maxPosition) {
      decisions.push({
        action: "BUY",
        symbol,
        size: orderSize,
        orderType: "ALO",
        confidence,
        reason: `${reasonBase} bid=${quotes.yesBid.toFixed(4)}`
      });
    }
    if (netPosition > -maxPosition) {
      decisions.push({
        action: "SELL",
        symbol,
        size: orderSize,
        orderType: "ALO",
        confidence,
        reason: `${reasonBase} ask=${quotes.yesAsk.toFixed(4)}`
      });
    }
    if (decisions.length === 0) {
      decisions.push({
        action: "HOLD",
        symbol,
        size: 0,
        orderType: "GTC",
        confidence: 0,
        reason: `PredictionMM: max position reached on both sides`
      });
    }
    return decisions;
  }
};

// src/strategies/registry.ts
var STRATEGIES = {
  "simple-mm": () => new SimpleMM(),
  "avellaneda-mm": () => new AvellanedaMM(),
  "engine-mm": () => new EngineMM(),
  "regime-mm": () => new RegimeMM(),
  "grid-mm": () => new GridMM(),
  "liquidation-mm": () => new LiquidationMM(),
  "funding-arb": () => new FundingArb(),
  "basis-arb": () => new BasisArb(),
  "momentum-breakout": () => new MomentumBreakout(),
  "mean-reversion": () => new MeanReversion(),
  "aggressive-taker": () => new AggressiveTaker(),
  "llm-custom": () => new LlmCustom(),
  "prediction-mm": () => new PredictionMM()
};
function createStrategy(name) {
  const factory = STRATEGIES[name];
  if (!factory) throw new Error(`Unknown strategy: ${name}`);
  return factory();
}
function listStrategies() {
  return Object.keys(STRATEGIES);
}

// src/exchanges/polymarket.ts
import { createHmac as createHmac2 } from "node:crypto";
var PolymarketAdapter = class {
  name = "Polymarket";
  apiUrl;
  chainId;
  privateKey;
  walletAddress;
  apiKey;
  apiSecret;
  apiPassphrase;
  marketCache = /* @__PURE__ */ new Map();
  // Injected in tests via _fetch; production uses global fetch
  _fetch = (...args) => fetch(...args);
  constructor(config) {
    this.apiUrl = config.apiUrl;
    this.chainId = config.chainId;
    this.privateKey = config.privateKey;
    this.walletAddress = requireWalletAddress2(config.walletAddress, "PolymarketAdapter");
    this.apiKey = config.apiKey ?? "";
    this.apiSecret = config.apiSecret ?? "";
    this.apiPassphrase = config.apiPassphrase ?? "";
  }
  // ── Public interface ───────────────────────────────────────────────────────
  async getTicker(symbol) {
    const parsed = await this.resolveSymbol(symbol);
    const priceResp = await this.publicGet(`/price?token_id=${parsed.tokenId}`);
    const price = parseFloat(priceResp.price);
    const market = await this.fetchMarket(parsed.conditionId);
    const volume24h = market.volume_num_24hr ?? 0;
    return {
      symbol,
      mid: price,
      bid: price - 0.01,
      ask: price + 0.01,
      lastPrice: price,
      volume24h,
      openInterest: 0,
      fundingRate: 0,
      timestamp: Date.now()
    };
  }
  async getOrderBook(symbol, depth = 20) {
    const parsed = await this.resolveSymbol(symbol);
    const raw = await this.publicGet(
      `/book?token_id=${parsed.tokenId}`
    );
    const bids = (raw.bids ?? []).slice(0, depth).map((l) => ({
      price: parseFloat(l.price),
      size: parseFloat(l.size)
    }));
    const asks = (raw.asks ?? []).slice(0, depth).map((l) => ({
      price: parseFloat(l.price),
      size: parseFloat(l.size)
    }));
    return { symbol, bids, asks, timestamp: Date.now() };
  }
  async getCandles(symbol, interval, limit) {
    const parsed = await this.resolveSymbol(symbol);
    const raw = await this.publicGet(
      `/prices-history?token_id=${parsed.tokenId}&interval=${interval}&fidelity=${limit * 2}`
    );
    const points = raw.history ?? [];
    return this.pointsToCandles(points, limit);
  }
  async getBalances() {
    const raw = await this.authGet("/balances");
    const usdcEntry = raw.find((b) => b.asset_type === "USDC");
    const balance = usdcEntry ? parseFloat(usdcEntry.balance) : 0;
    return [
      {
        currency: "USDC",
        available: balance,
        total: balance,
        unrealizedPnl: 0
      }
    ];
  }
  async getPositions() {
    const raw = await this.authGet("/positions");
    return raw.filter((p) => parseFloat(p.size) > 0).map((p) => {
      const size = parseFloat(p.size);
      const entryPrice = parseFloat(p.avg_price);
      const markPrice = parseFloat(p.cur_price);
      const unrealizedPnl = (markPrice - entryPrice) * size;
      const side = p.outcome.toLowerCase() === "yes" ? "YES" : "NO";
      const conditionSymbol = `${side}-${p.condition_id}`;
      return {
        symbol: conditionSymbol,
        side: "LONG",
        size,
        entryPrice,
        markPrice,
        unrealizedPnl,
        leverage: 1,
        liquidationPrice: null
      };
    });
  }
  async placeOrder(order) {
    const parsed = await this.resolveSymbol(order.symbol);
    const body = {
      token_id: parsed.tokenId,
      price: order.price.toString(),
      size: order.size.toString(),
      side: order.side,
      type: this.toPolyOrderType(order.orderType)
    };
    const resp = await this.authPost("/order", body);
    return this.mapOrderResponse(resp);
  }
  async cancelOrder(orderId) {
    await this.authDelete(`/order/${orderId}`);
  }
  async cancelAllOrders(symbol) {
    const orders = await this.getOpenOrders(symbol);
    await Promise.all(orders.map((o) => this.cancelOrder(o.orderId)));
  }
  async setStopLoss(_symbol, _side, _triggerPrice, _size) {
    throw new Error("Stop loss is not supported for prediction markets");
  }
  async getOpenOrders(symbol) {
    const raw = await this.authGet("/orders");
    let filtered = raw;
    if (symbol) {
      const parsed = this.parseSymbol(symbol);
      filtered = raw.filter((o) => o.condition_id === parsed.conditionId);
    }
    return filtered.map((o) => ({
      orderId: o.id,
      symbol: `${o.outcome.toUpperCase() === "YES" ? "YES" : "NO"}-${o.condition_id}`,
      side: o.side,
      price: parseFloat(o.price),
      size: parseFloat(o.original_size),
      filledSize: parseFloat(o.size_matched),
      orderType: this.fromPolyOrderType(o.type),
      timestamp: o.created_at
    }));
  }
  async getExchangeInfo() {
    const markets = await this.publicGet("/markets");
    const supportedSymbols = [];
    const minOrderSizes = {};
    const tickSizes = {};
    for (const market of markets) {
      if (!market.active) continue;
      const yesSymbol = `YES-${market.condition_id}`;
      const noSymbol = `NO-${market.condition_id}`;
      supportedSymbols.push(yesSymbol, noSymbol);
      const minSize = parseFloat(market.minimum_order_size);
      const tickSize = parseFloat(market.minimum_tick_size);
      minOrderSizes[yesSymbol] = minSize;
      minOrderSizes[noSymbol] = minSize;
      tickSizes[yesSymbol] = tickSize;
      tickSizes[noSymbol] = tickSize;
      this.marketCache.set(market.condition_id, market);
    }
    return {
      name: "Polymarket",
      testnet: this.apiUrl.includes("testnet"),
      supportedSymbols,
      minOrderSizes,
      tickSizes
    };
  }
  // ── Private helpers ────────────────────────────────────────────────────────
  parseSymbol(symbol) {
    const match = /^(YES|NO)-(.+)$/.exec(symbol);
    if (!match || !match[1] || !match[2]) {
      throw new Error(`Invalid Polymarket symbol: ${symbol}`);
    }
    return { conditionId: match[2], side: match[1] };
  }
  async resolveSymbol(symbol) {
    const { conditionId, side } = this.parseSymbol(symbol);
    let market = this.marketCache.get(conditionId);
    if (!market) {
      market = await this.fetchMarket(conditionId);
      this.marketCache.set(conditionId, market);
    }
    const targetOutcome = side === "YES" ? "Yes" : "No";
    const token = market.tokens.find((t) => t.outcome === targetOutcome);
    if (!token) {
      throw new Error(`Token not found for ${side} outcome in market ${conditionId}`);
    }
    return { conditionId, tokenId: token.token_id, side };
  }
  async fetchMarket(conditionId) {
    return this.publicGet(`/markets/${conditionId}`);
  }
  pointsToCandles(points, limit) {
    if (points.length === 0) return [];
    const relevantPoints = points.slice(-limit * 2);
    const candles = [];
    const chunkSize = Math.max(1, Math.floor(relevantPoints.length / limit));
    for (let i = 0; i < relevantPoints.length; i += chunkSize) {
      const chunk = relevantPoints.slice(i, i + chunkSize);
      if (chunk.length === 0) continue;
      const prices = chunk.map((p) => p.p);
      const firstPoint = chunk[0];
      const lastPoint = chunk[chunk.length - 1];
      candles.push({
        timestamp: firstPoint.t * 1e3,
        open: firstPoint.p,
        high: Math.max(...prices),
        low: Math.min(...prices),
        close: lastPoint.p,
        volume: 0
        // prediction markets don't have per-candle volume
      });
      if (candles.length >= limit) break;
    }
    return candles.slice(-limit);
  }
  mapOrderResponse(resp) {
    const status = resp.status.toUpperCase();
    const filledSize = parseFloat(resp.size);
    const filledPrice = parseFloat(resp.price);
    if (status === "MATCHED") {
      return {
        orderId: resp.id,
        status: "FILLED",
        filledSize,
        filledPrice,
        timestamp: Date.now()
      };
    }
    return {
      orderId: resp.id,
      status: "OPEN",
      filledSize: 0,
      filledPrice: 0,
      timestamp: Date.now()
    };
  }
  toPolyOrderType(orderType) {
    switch (orderType) {
      case "ALO":
        return "FOK";
      // Polymarket doesn't have ALO; use FOK closest analog
      case "GTC":
        return "GTC";
      case "IOC":
        return "FOK";
    }
  }
  fromPolyOrderType(rawType) {
    if (rawType.toUpperCase() === "FOK") return "IOC";
    if (rawType.toUpperCase() === "GTC") return "GTC";
    return "GTC";
  }
  generateApiHeaders(method, path, body) {
    const timestamp = Math.floor(Date.now() / 1e3).toString();
    const nonce = "0";
    const message = timestamp + method.toUpperCase() + path + (body ?? "");
    let signature = "";
    if (this.apiSecret) {
      const secretBytes = Buffer.from(this.apiSecret, "base64");
      signature = createHmac2("sha256", secretBytes).update(message).digest("base64");
    }
    return {
      "POLY-ADDRESS": this.walletAddress,
      "POLY-SIGNATURE": signature,
      "POLY-TIMESTAMP": timestamp,
      "POLY-NONCE": nonce,
      "POLY-API-KEY": this.apiKey,
      "POLY-PASSPHRASE": this.apiPassphrase,
      "Content-Type": "application/json"
    };
  }
  async publicGet(path) {
    const resp = await this._fetch(`${this.apiUrl}${path}`, {
      method: "GET",
      headers: { "Content-Type": "application/json" }
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Polymarket API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }
  async authGet(path) {
    const headers = this.generateApiHeaders("GET", path);
    const resp = await this._fetch(`${this.apiUrl}${path}`, {
      method: "GET",
      headers
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Polymarket API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }
  async authPost(path, body) {
    const bodyStr = JSON.stringify(body);
    const headers = this.generateApiHeaders("POST", path, bodyStr);
    const resp = await this._fetch(`${this.apiUrl}${path}`, {
      method: "POST",
      headers,
      body: bodyStr
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Polymarket API error ${resp.status}: ${text}`);
    }
    return resp.json();
  }
  async authDelete(path) {
    const headers = this.generateApiHeaders("DELETE", path);
    const resp = await this._fetch(`${this.apiUrl}${path}`, {
      method: "DELETE",
      headers
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Polymarket API error ${resp.status}: ${text}`);
    }
  }
};
function requireWalletAddress2(address, context) {
  if (!address) {
    throw new Error(`${context}: walletAddress is required. Ethereum addresses cannot be derived from private keys without elliptic curve point multiplication. Provide the address explicitly in config.`);
  }
  return address;
}

// src/exchanges/mcp-bridge.ts
var McpBridgeAdapter = class {
  name;
  caller;
  mapping;
  config;
  constructor(caller, config) {
    this.name = config.exchangeName;
    this.caller = caller;
    this.mapping = config.toolMapping;
    this.config = config;
  }
  async getTicker(symbol) {
    const raw = await this.caller.callTool(this.mapping.getTicker, { symbol });
    return this.parseTicker(raw);
  }
  async getOrderBook(symbol, depth) {
    const raw = await this.caller.callTool(this.mapping.getOrderBook, { symbol, depth });
    return this.parseOrderBook(raw);
  }
  async getCandles(_symbol, _interval, _limit) {
    throw new Error("getCandles is not supported via MCP bridge");
  }
  async getBalances() {
    const raw = await this.caller.callTool(this.mapping.getBalances, {});
    return this.parseBalances(raw);
  }
  async getPositions() {
    const raw = await this.caller.callTool(this.mapping.getPositions, {});
    return this.parsePositions(raw);
  }
  async placeOrder(order) {
    const raw = await this.caller.callTool(this.mapping.placeOrder, {
      symbol: order.symbol,
      side: order.side,
      size: order.size,
      price: order.price,
      orderType: order.orderType
    });
    return this.parseOrderResult(raw);
  }
  async cancelOrder(orderId) {
    await this.caller.callTool(this.mapping.cancelOrder, { orderId });
  }
  async cancelAllOrders(_symbol) {
    const openOrders = await this.getOpenOrders(_symbol);
    for (const order of openOrders) {
      await this.cancelOrder(order.orderId);
    }
  }
  async setStopLoss(_symbol, _side, _triggerPrice, _size) {
    throw new Error("setStopLoss is not supported via MCP bridge");
  }
  async getOpenOrders(symbol) {
    const raw = await this.caller.callTool(this.mapping.getOpenOrders, { symbol });
    return this.parseOpenOrders(raw);
  }
  async getExchangeInfo() {
    return {
      name: this.config.exchangeName,
      testnet: false,
      supportedSymbols: this.config.supportedSymbols,
      minOrderSizes: {},
      tickSizes: {}
    };
  }
  // --- Parsers ---
  parseTicker(raw) {
    const data = raw;
    return {
      symbol: String(data["symbol"] ?? ""),
      mid: Number(data["mid"] ?? 0),
      bid: Number(data["bid"] ?? 0),
      ask: Number(data["ask"] ?? 0),
      lastPrice: Number(data["lastPrice"] ?? 0),
      volume24h: Number(data["volume24h"] ?? 0),
      openInterest: Number(data["openInterest"] ?? 0),
      fundingRate: Number(data["fundingRate"] ?? 0),
      timestamp: Number(data["timestamp"] ?? Date.now())
    };
  }
  parseOrderBook(raw) {
    const data = raw;
    const parseLevels = (levels) => {
      if (!Array.isArray(levels)) return [];
      return levels.map((l) => ({
        price: Number(l["price"] ?? 0),
        size: Number(l["size"] ?? 0)
      }));
    };
    return {
      symbol: String(data["symbol"] ?? ""),
      bids: parseLevels(data["bids"]),
      asks: parseLevels(data["asks"]),
      timestamp: Number(data["timestamp"] ?? Date.now())
    };
  }
  parseOrderResult(raw) {
    const data = raw;
    return {
      orderId: String(data["orderId"] ?? ""),
      status: String(data["status"] ?? "REJECTED"),
      filledSize: Number(data["filledSize"] ?? 0),
      filledPrice: Number(data["filledPrice"] ?? 0),
      timestamp: Number(data["timestamp"] ?? Date.now())
    };
  }
  parseBalances(raw) {
    if (!Array.isArray(raw)) return [];
    return raw.map((b) => ({
      currency: String(b["currency"] ?? ""),
      available: Number(b["available"] ?? 0),
      total: Number(b["total"] ?? 0),
      unrealizedPnl: Number(b["unrealizedPnl"] ?? 0)
    }));
  }
  parsePositions(raw) {
    if (!Array.isArray(raw)) return [];
    return raw.map((p) => ({
      symbol: String(p["symbol"] ?? ""),
      side: String(p["side"] ?? "LONG"),
      size: Number(p["size"] ?? 0),
      entryPrice: Number(p["entryPrice"] ?? 0),
      markPrice: Number(p["markPrice"] ?? 0),
      unrealizedPnl: Number(p["unrealizedPnl"] ?? 0),
      leverage: Number(p["leverage"] ?? 1),
      liquidationPrice: p["liquidationPrice"] != null ? Number(p["liquidationPrice"]) : null
    }));
  }
  parseOpenOrders(raw) {
    if (!Array.isArray(raw)) return [];
    return raw.map((o) => ({
      orderId: String(o["orderId"] ?? ""),
      symbol: String(o["symbol"] ?? ""),
      side: String(o["side"] ?? "BUY"),
      price: Number(o["price"] ?? 0),
      size: Number(o["size"] ?? 0),
      filledSize: Number(o["filledSize"] ?? 0),
      orderType: String(o["orderType"] ?? "GTC"),
      timestamp: Number(o["timestamp"] ?? Date.now())
    }));
  }
};

// src/exchanges/krx-calendar.ts
var FIXED_HOLIDAYS = [
  { date: "01-01", name: "New Year", nameKo: "\uC2E0\uC815" },
  { date: "03-01", name: "Independence Movement Day", nameKo: "\uC0BC\uC77C\uC808" },
  { date: "05-05", name: "Children's Day", nameKo: "\uC5B4\uB9B0\uC774\uB0A0" },
  { date: "06-06", name: "Memorial Day", nameKo: "\uD604\uCDA9\uC77C" },
  { date: "08-15", name: "Liberation Day", nameKo: "\uAD11\uBCF5\uC808" },
  { date: "10-03", name: "National Foundation Day", nameKo: "\uAC1C\uCC9C\uC808" },
  { date: "10-09", name: "Hangul Day", nameKo: "\uD55C\uAE00\uB0A0" },
  { date: "12-25", name: "Christmas", nameKo: "\uC131\uD0C4\uC808" },
  { date: "12-31", name: "Year End (KRX)", nameKo: "\uC5F0\uB9D0 \uD734\uC7A5" }
];
var LUNAR_HOLIDAY_NAMES = {
  // 설날 (Lunar New Year) — 3-day span
  "2026-02-16": "\uC124\uB0A0",
  "2026-02-17": "\uC124\uB0A0",
  "2026-02-18": "\uC124\uB0A0",
  "2026-05-24": "\uC11D\uAC00\uD0C4\uC2E0\uC77C",
  "2026-10-04": "\uCD94\uC11D",
  "2026-10-05": "\uCD94\uC11D",
  "2026-10-06": "\uCD94\uC11D",
  "2027-02-05": "\uC124\uB0A0",
  "2027-02-06": "\uC124\uB0A0",
  "2027-02-07": "\uC124\uB0A0",
  "2027-05-13": "\uC11D\uAC00\uD0C4\uC2E0\uC77C",
  "2027-09-23": "\uCD94\uC11D",
  "2027-09-24": "\uCD94\uC11D",
  "2027-09-25": "\uCD94\uC11D"
};
var LUNAR_HOLIDAYS = {
  2026: [
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    // 설날 (Lunar New Year)
    "2026-05-24",
    // 석가탄신일 (Buddha's Birthday)
    "2026-10-04",
    "2026-10-05",
    "2026-10-06"
    // 추석 (Chuseok)
  ],
  2027: [
    "2027-02-05",
    "2027-02-06",
    "2027-02-07",
    "2027-05-13",
    "2027-09-23",
    "2027-09-24",
    "2027-09-25"
  ]
};
var KST_OFFSET_MINUTES = 540;
function getKSTComponents(now) {
  const utcMs = now.getTime();
  const kstMs = utcMs + KST_OFFSET_MINUTES * 6e4;
  const kst = new Date(kstMs);
  return {
    hours: kst.getUTCHours(),
    minutes: kst.getUTCMinutes(),
    dayOfWeek: kst.getUTCDay(),
    year: kst.getUTCFullYear(),
    month: kst.getUTCMonth() + 1,
    day: kst.getUTCDate()
  };
}
function formatMMDD(month, day) {
  return `${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}
function formatISODate(year, month, day) {
  return `${year}-${formatMMDD(month, day)}`;
}
function getCurrentKrxSession(now) {
  const date = now ?? /* @__PURE__ */ new Date();
  const kst = getKSTComponents(date);
  if (kst.dayOfWeek === 0 || kst.dayOfWeek === 6) return "CLOSED";
  if (isKrxHoliday(date)) return "CLOSED";
  const totalMinutes = kst.hours * 60 + kst.minutes;
  if (totalMinutes >= 480 && totalMinutes < 540) return "PRE_MARKET";
  if (totalMinutes >= 540 && totalMinutes < 930) return "REGULAR";
  if (totalMinutes >= 940 && totalMinutes < 1080) return "AFTER_HOURS";
  return "CLOSED";
}
function isKrxMarketOpen(now) {
  return getCurrentKrxSession(now) === "REGULAR";
}
function isKrxHoliday(date) {
  const kst = getKSTComponents(date);
  const mmdd = formatMMDD(kst.month, kst.day);
  const isoDate = formatISODate(kst.year, kst.month, kst.day);
  if (FIXED_HOLIDAYS.some((h) => h.date === mmdd)) return true;
  const yearHolidays = LUNAR_HOLIDAYS[kst.year];
  if (yearHolidays?.includes(isoDate)) return true;
  return false;
}
function getKrxHolidayName(date) {
  const kst = getKSTComponents(date);
  const mmdd = formatMMDD(kst.month, kst.day);
  const isoDate = formatISODate(kst.year, kst.month, kst.day);
  const fixed = FIXED_HOLIDAYS.find((h) => h.date === mmdd);
  if (fixed) return fixed.nameKo;
  const lunarName = LUNAR_HOLIDAY_NAMES[isoDate];
  if (lunarName) return lunarName;
  return null;
}
function getNextKrxMarketOpen(now) {
  const date = now ?? /* @__PURE__ */ new Date();
  const kst = getKSTComponents(date);
  const totalMinutes = kst.hours * 60 + kst.minutes;
  const isWeekend = kst.dayOfWeek === 0 || kst.dayOfWeek === 6;
  if (!isWeekend && !isKrxHoliday(date) && totalMinutes < 540) {
    return makeKST0900(kst.year, kst.month, kst.day);
  }
  let candidateYear = kst.year;
  let candidateMonth = kst.month;
  let candidateDay = kst.day;
  for (let i = 0; i < 30; i++) {
    const next = new Date(Date.UTC(candidateYear, candidateMonth - 1, candidateDay + 1, 0, 0, 0));
    candidateYear = next.getUTCFullYear();
    candidateMonth = next.getUTCMonth() + 1;
    candidateDay = next.getUTCDate();
    const dayOfWeek = next.getUTCDay();
    if (dayOfWeek === 0 || dayOfWeek === 6) continue;
    const candidateDate = new Date(Date.UTC(candidateYear, candidateMonth - 1, candidateDay, 3, 0, 0));
    if (isKrxHoliday(candidateDate)) continue;
    return makeKST0900(candidateYear, candidateMonth, candidateDay);
  }
  return makeKST0900(kst.year, kst.month, kst.day + 1);
}
function makeKST0900(year, month, day) {
  return new Date(Date.UTC(year, month - 1, day, 0, 0, 0));
}
function canTradeKrx(session, afterHoursAllowed) {
  if (session === "REGULAR") return true;
  if (session === "AFTER_HOURS" && afterHoursAllowed) return true;
  return false;
}

// src/exchanges/kium.ts
function parseKoreanNumber(s) {
  const cleaned = s.replace(/[,+\s]/g, "");
  return Math.abs(parseFloat(cleaned) || 0);
}
function mapPriceType(orderType) {
  if (orderType === "IOC") return "03";
  return "00";
}
function mapOrderSide(side) {
  return side === "BUY" ? 1 : 2;
}
var KiumAdapter = class {
  name = "\uD0A4\uC6C0\uC99D\uAD8C";
  accountNo;
  bridge;
  afterHours;
  constructor(config) {
    this.accountNo = config.accountNo;
    this.bridge = config.bridge;
    this.afterHours = config.afterHoursTrading ?? false;
  }
  async getTicker(symbol) {
    const rows = await this.bridge.requestTR("opt10001", { "\uC885\uBAA9\uCF54\uB4DC": symbol }, "0101");
    const row = rows[0];
    if (!row) throw new Error(`No data for symbol: ${symbol}`);
    const currentPrice = parseKoreanNumber(row["\uD604\uC7AC\uAC00"] ?? "0");
    return {
      symbol,
      mid: currentPrice,
      bid: parseKoreanNumber(row["\uB9E4\uC218\uCD5C\uC6B0\uC120\uD638\uAC00"] ?? "0"),
      ask: parseKoreanNumber(row["\uB9E4\uB3C4\uCD5C\uC6B0\uC120\uD638\uAC00"] ?? "0"),
      lastPrice: currentPrice,
      volume24h: parseKoreanNumber(row["\uAC70\uB798\uB7C9"] ?? "0"),
      openInterest: 0,
      fundingRate: 0,
      timestamp: Date.now()
    };
  }
  async getOrderBook(symbol, _depth) {
    const rows = await this.bridge.requestTR("opt10004", { "\uC885\uBAA9\uCF54\uB4DC": symbol }, "0102");
    const row = rows[0] ?? {};
    const asks = [];
    const bids = [];
    for (let i = 1; i <= 10; i++) {
      const askPrice = parseKoreanNumber(row[`\uB9E4\uB3C4\uD638\uAC00${i}`] ?? "0");
      const askSize = parseKoreanNumber(row[`\uB9E4\uB3C4\uD638\uAC00\uC218\uB7C9${i}`] ?? "0");
      const bidPrice = parseKoreanNumber(row[`\uB9E4\uC218\uD638\uAC00${i}`] ?? "0");
      const bidSize = parseKoreanNumber(row[`\uB9E4\uC218\uD638\uAC00\uC218\uB7C9${i}`] ?? "0");
      asks.push({ price: askPrice, size: askSize });
      bids.push({ price: bidPrice, size: bidSize });
    }
    return {
      symbol,
      asks,
      bids,
      timestamp: Date.now()
    };
  }
  async getCandles(symbol, _interval, _limit) {
    const rows = await this.bridge.requestTR("opt10081", { "\uC885\uBAA9\uCF54\uB4DC": symbol, "\uAE30\uC900\uC77C\uC790": "", "\uC218\uC815\uC8FC\uAC00\uAD6C\uBD84": "1" }, "0103");
    return rows.map((row) => {
      const dateStr = row["\uC77C\uC790"] ?? "";
      const year = parseInt(dateStr.slice(0, 4), 10) || 0;
      const month = parseInt(dateStr.slice(4, 6), 10) || 1;
      const day = parseInt(dateStr.slice(6, 8), 10) || 1;
      const timestamp = new Date(year, month - 1, day).getTime();
      return {
        timestamp,
        open: parseKoreanNumber(row["\uC2DC\uAC00"] ?? "0"),
        high: parseKoreanNumber(row["\uACE0\uAC00"] ?? "0"),
        low: parseKoreanNumber(row["\uC800\uAC00"] ?? "0"),
        close: parseKoreanNumber(row["\uD604\uC7AC\uAC00"] ?? "0"),
        volume: parseKoreanNumber(row["\uAC70\uB798\uB7C9"] ?? "0")
      };
    });
  }
  async getBalances() {
    const rows = await this.bridge.requestTR("opw00018", {
      "\uACC4\uC88C\uBC88\uD638": this.accountNo,
      "\uBE44\uBC00\uBC88\uD638": "",
      "\uBE44\uBC00\uBC88\uD638\uC785\uB825\uB9E4\uCCB4\uAD6C\uBD84": "00",
      "\uC870\uD68C\uAD6C\uBD84": "1"
    }, "0104");
    const row = rows[0];
    if (!row) return [];
    return [{
      currency: "KRW",
      available: parseKoreanNumber(row["\uCD1D\uD3C9\uAC00\uAE08\uC561"] ?? "0"),
      total: parseKoreanNumber(row["\uCD94\uC815\uC608\uD0C1\uC790\uC0B0"] ?? "0"),
      unrealizedPnl: parseKoreanNumber(row["\uCD1D\uD3C9\uAC00\uC190\uC775\uAE08\uC561"] ?? "0")
    }];
  }
  async getPositions() {
    const rows = await this.bridge.requestTR("opw00018", {
      "\uACC4\uC88C\uBC88\uD638": this.accountNo,
      "\uBE44\uBC00\uBC88\uD638": "",
      "\uBE44\uBC00\uBC88\uD638\uC785\uB825\uB9E4\uCCB4\uAD6C\uBD84": "00",
      "\uC870\uD68C\uAD6C\uBD84": "2"
    }, "0104");
    return rows.map((row) => ({
      symbol: (row["\uC885\uBAA9\uBC88\uD638"] ?? "").replace(/\s/g, ""),
      side: "LONG",
      // Korean stocks: no short selling for retail
      size: parseKoreanNumber(row["\uBCF4\uC720\uC218\uB7C9"] ?? "0"),
      entryPrice: parseKoreanNumber(row["\uB9E4\uC785\uAC00"] ?? "0"),
      markPrice: parseKoreanNumber(row["\uD604\uC7AC\uAC00"] ?? "0"),
      unrealizedPnl: parseKoreanNumber(row["\uD3C9\uAC00\uC190\uC775"] ?? "0"),
      leverage: 1,
      // Korean stocks: no leverage
      liquidationPrice: null
      // N/A for spot stocks
    }));
  }
  async placeOrder(order) {
    const result = await this.bridge.sendOrder({
      accountNo: this.accountNo,
      orderType: mapOrderSide(order.side),
      symbol: order.symbol,
      quantity: order.size,
      price: order.price,
      priceType: mapPriceType(order.orderType),
      originalOrderNo: void 0
    });
    return {
      orderId: result.orderId,
      status: result.status === "FILLED" ? "FILLED" : "OPEN",
      filledSize: result.status === "FILLED" ? order.size : 0,
      filledPrice: result.status === "FILLED" ? order.price : 0,
      timestamp: Date.now()
    };
  }
  async cancelOrder(orderId) {
    await this.bridge.cancelOrder(orderId, 3);
  }
  async cancelAllOrders(symbol) {
    const openOrders = await this.getOpenOrders(symbol);
    for (const order of openOrders) {
      await this.cancelOrder(order.orderId);
    }
  }
  async setStopLoss(_symbol, _side, _triggerPrice, _size) {
    throw new Error("setStopLoss is not natively supported by \uD0A4\uC6C0\uC99D\uAD8C Open API");
  }
  async getOpenOrders(_symbol) {
    const rows = await this.bridge.requestTR("opt10075", {
      "\uACC4\uC88C\uBC88\uD638": this.accountNo,
      "\uC804\uCCB4\uC885\uBAA9\uAD6C\uBD84": "0",
      "\uB9E4\uB9E4\uAD6C\uBD84": "0",
      "\uC885\uBAA9\uCF54\uB4DC": "",
      "\uCCB4\uACB0\uAD6C\uBD84": "1"
    }, "0105");
    return rows.map((row) => {
      const orderTypeStr = row["\uC8FC\uBB38\uAD6C\uBD84"] ?? "";
      const side = orderTypeStr.includes("\uB9E4\uC218") ? "BUY" : "SELL";
      const totalSize = parseKoreanNumber(row["\uC8FC\uBB38\uC218\uB7C9"] ?? "0");
      const unfilledSize = parseKoreanNumber(row["\uBBF8\uCCB4\uACB0\uC218\uB7C9"] ?? "0");
      return {
        orderId: row["\uC8FC\uBB38\uBC88\uD638"] ?? "",
        symbol: row["\uC885\uBAA9\uCF54\uB4DC"] ?? "",
        side,
        price: parseKoreanNumber(row["\uC8FC\uBB38\uAC00\uACA9"] ?? "0"),
        size: totalSize,
        filledSize: totalSize - unfilledSize,
        orderType: "GTC",
        timestamp: Date.now()
      };
    });
  }
  async getExchangeInfo() {
    return {
      name: "\uD0A4\uC6C0\uC99D\uAD8C",
      testnet: false,
      supportedSymbols: [],
      minOrderSizes: {},
      tickSizes: {}
    };
  }
};

// src/exchanges/kis-mcp.ts
var KIS_TOOL_MAPPING = {
  getTicker: "kis_get_stock_price",
  getOrderBook: "kis_get_orderbook",
  getBalances: "kis_get_account_balance",
  getPositions: "kis_get_positions",
  placeOrder: "kis_place_order",
  cancelOrder: "kis_cancel_order",
  getOpenOrders: "kis_get_open_orders"
};
var KisMcpAdapter = class {
  name = "\uD55C\uAD6D\uD22C\uC790\uC99D\uAD8C";
  bridge;
  accountNo;
  afterHours;
  nowFn;
  constructor(config, nowFn) {
    this.accountNo = config.accountNo;
    this.afterHours = config.afterHoursTrading ?? false;
    this.nowFn = nowFn ?? (() => /* @__PURE__ */ new Date());
    const bridgeConfig = {
      exchangeName: "\uD55C\uAD6D\uD22C\uC790\uC99D\uAD8C",
      toolMapping: KIS_TOOL_MAPPING,
      supportedSymbols: []
    };
    this.bridge = new McpBridgeAdapter(config.mcpCaller, bridgeConfig);
  }
  // --- Read-only methods: delegate directly to bridge ---
  async getTicker(symbol) {
    return this.bridge.getTicker(symbol);
  }
  async getOrderBook(symbol, depth) {
    return this.bridge.getOrderBook(symbol, depth);
  }
  async getCandles(symbol, interval, limit) {
    return this.bridge.getCandles(symbol, interval, limit);
  }
  async getBalances() {
    return this.bridge.getBalances();
  }
  async getPositions() {
    return this.bridge.getPositions();
  }
  async getOpenOrders(symbol) {
    return this.bridge.getOpenOrders(symbol);
  }
  async getExchangeInfo() {
    return this.bridge.getExchangeInfo();
  }
  // --- Mutation methods: KRX hours guard ---
  async placeOrder(order) {
    this.assertMarketOpen();
    return this.bridge.placeOrder(order);
  }
  async cancelOrder(orderId) {
    this.assertMarketOpen();
    return this.bridge.cancelOrder(orderId);
  }
  async cancelAllOrders(symbol) {
    this.assertMarketOpen();
    return this.bridge.cancelAllOrders(symbol);
  }
  async setStopLoss(symbol, side, triggerPrice, size) {
    this.assertMarketOpen();
    return this.bridge.setStopLoss(symbol, side, triggerPrice, size);
  }
  // --- Private helpers ---
  /** Throw if KRX market is not open for trading */
  assertMarketOpen() {
    const now = this.nowFn();
    const session = getCurrentKrxSession(now);
    if (!canTradeKrx(session, this.afterHours)) {
      const nextOpen = getNextKrxMarketOpen(now);
      throw new Error(
        `KRX market is ${session}. Next open: ${nextOpen.toISOString()}`
      );
    }
  }
};
export {
  APEX_PRESETS,
  AggressiveTaker,
  AlpacaAdapter,
  ApexOrchestrator,
  AvellanedaMM,
  BaseStrategy,
  BasisArb,
  BinanceAdapter,
  BnSigner,
  BnWebSocket,
  DEFAULT_LEVERAGE,
  EngineMM,
  FundingArb,
  GUARD_PRESETS,
  GridMM,
  Guard,
  HlSigner,
  HlWebSocket,
  HyperliquidAdapter,
  KisMcpAdapter,
  KiumAdapter,
  LiquidationMM,
  LlmCustom,
  McpBridgeAdapter,
  MeanReversion,
  MomentumBreakout,
  OrderManager,
  PolymarketAdapter,
  PredictionMM,
  Pulse,
  Radar,
  ReflectAnalyzer,
  RegimeMM,
  RiskGuardian,
  SimpleMM,
  StateStore,
  calculateEMA,
  calculateRSI,
  canTrade,
  canTradeKrx,
  computeATR,
  computeBollingerBands,
  computeFairValue,
  computeInventorySkew,
  computeSMA,
  computeStdDev,
  createDefaultConfig,
  createEmptySlot,
  createStrategy,
  generateQuotes,
  getCurrentKrxSession,
  getCurrentSession,
  getKrxHolidayName,
  getNextKrxMarketOpen,
  getNextMarketOpen,
  isKrxHoliday,
  isKrxMarketOpen,
  isMarketOpen,
  listStrategies,
  loadConfig,
  parseKoreanNumber,
  saveConfig,
  validateConfig
};
//# sourceMappingURL=index.mjs.map
