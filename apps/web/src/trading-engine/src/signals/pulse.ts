import type { PulseSignal, PulseSignalType, Ticker } from '../types.js'

interface MarketSnapshot {
  symbol: string
  openInterest: number
  volume24h: number
  fundingRate: number
  lastPrice: number
  timestamp: number
}

interface ScanResult {
  signals: PulseSignal[]
  timestamp: number
}

const MAX_WINDOW = 5

const CONFIDENCE: Record<PulseSignalType, number> = {
  FIRST_JUMP: 100,
  CONTRIB_EXPLOSION: 95,
  IMMEDIATE_MOVER: 80,
  VOLUME_SURGE: 70,
  NEW_ENTRY_DEEP: 65,
  OI_BREAKOUT: 60,
  DEEP_CLIMBER: 55,
  FUNDING_FLIP: 50,
}

// Priority order: higher index = higher tier (only emit highest)
const TIER_ORDER: PulseSignalType[] = [
  'FUNDING_FLIP',
  'DEEP_CLIMBER',
  'OI_BREAKOUT',
  'NEW_ENTRY_DEEP',
  'VOLUME_SURGE',
  'IMMEDIATE_MOVER',
  'CONTRIB_EXPLOSION',
  'FIRST_JUMP',
]

const DEFAULT_SECTORS: Record<string, string[]> = {
  L1: ['BTC', 'ETH', 'SOL'],
  DEFI: ['UNI', 'AAVE', 'MKR'],
  MEME: ['DOGE', 'SHIB', 'PEPE'],
  L2: ['ARB', 'OP', 'MATIC'],
  AI: ['FET', 'RENDER', 'TAO'],
}

export class Pulse {
  private snapshots: Map<string, MarketSnapshot[]>
  private sectorMap: Map<string, string[]>
  private sectorSignalHistory: Map<string, number> // sector → last scan number with a signal
  private scanCount: number

  constructor() {
    this.snapshots = new Map()
    this.sectorMap = new Map()
    this.sectorSignalHistory = new Map()
    this.scanCount = 0

    // Build sector map
    for (const [sector, symbols] of Object.entries(DEFAULT_SECTORS)) {
      this.sectorMap.set(sector, symbols)
    }
  }

  scan(tickers: Ticker[]): ScanResult {
    const now = Date.now()
    this.scanCount++
    const signals: PulseSignal[] = []

    for (const ticker of tickers) {
      const current: MarketSnapshot = {
        symbol: ticker.symbol,
        openInterest: ticker.openInterest,
        volume24h: ticker.volume24h,
        fundingRate: ticker.fundingRate,
        lastPrice: ticker.lastPrice,
        timestamp: ticker.timestamp,
      }

      const history = this.snapshots.get(ticker.symbol)
      if (!history || history.length === 0) {
        // First scan for this symbol — store baseline, no signals
        this.snapshots.set(ticker.symbol, [current])
        continue
      }

      const prev = history[history.length - 1]!
      const signal = this.detectSignal(current, prev, history, ticker.timestamp)

      // Store snapshot in rolling window
      history.push(current)
      if (history.length > MAX_WINDOW) {
        history.shift()
      }

      if (signal) {
        signals.push(signal)
      }
    }

    // Check for FIRST_JUMP upgrades
    this.applyFirstJump(signals)

    // Sort by confidence descending
    signals.sort((a, b) => b.confidence - a.confidence)

    return { signals, timestamp: now }
  }

  reset(): void {
    this.snapshots.clear()
    this.sectorSignalHistory.clear()
    this.scanCount = 0
  }

  private detectSignal(
    current: MarketSnapshot,
    prev: MarketSnapshot,
    history: MarketSnapshot[],
    timestamp: number,
  ): PulseSignal | null {
    const oiChangePct = (current.openInterest - prev.openInterest) / prev.openInterest * 100
    const volumeMultiple = prev.volume24h > 0 ? current.volume24h / prev.volume24h : 0
    const fundingChange = current.fundingRate - prev.fundingRate
    const priceChangePct = (current.lastPrice - prev.lastPrice) / prev.lastPrice * 100
    const direction: 'LONG' | 'SHORT' = priceChangePct > 0 ? 'LONG' : 'SHORT'

    const data = {
      oiChangePct,
      volumeMultiple,
      fundingRate: current.fundingRate,
      priceChangePct,
    }

    // Detect all matching signal types
    const matched: PulseSignalType[] = []

    // CONTRIB_EXPLOSION: OI >= 15% AND volume >= 5x
    if (oiChangePct >= 15 && volumeMultiple >= 5) {
      matched.push('CONTRIB_EXPLOSION')
    }

    // IMMEDIATE_MOVER: OI >= 15% OR volume >= 5x
    if (oiChangePct >= 15 || volumeMultiple >= 5) {
      matched.push('IMMEDIATE_MOVER')
    }

    // NEW_ENTRY_DEEP: OI >= 8% AND volume < 2x
    if (oiChangePct >= 8 && volumeMultiple < 2) {
      matched.push('NEW_ENTRY_DEEP')
    }

    // DEEP_CLIMBER: OI >= 5% for 3+ consecutive scans
    if (oiChangePct >= 5 && this.hasConsecutiveOiClimbs(current.symbol, history, current, 3)) {
      matched.push('DEEP_CLIMBER')
    }

    // VOLUME_SURGE: volume >= 3x
    if (volumeMultiple >= 3) {
      matched.push('VOLUME_SURGE')
    }

    // OI_BREAKOUT: OI >= 8%
    if (oiChangePct >= 8) {
      matched.push('OI_BREAKOUT')
    }

    // FUNDING_FLIP: |fundingChange| >= |prevFunding| * 0.5
    if (prev.fundingRate !== 0 && Math.abs(fundingChange) >= Math.abs(prev.fundingRate) * 0.5) {
      matched.push('FUNDING_FLIP')
    }

    if (matched.length === 0) {
      return null
    }

    // Pick highest-tier signal
    const best = this.highestTier(matched)

    return {
      symbol: current.symbol,
      type: best,
      confidence: CONFIDENCE[best],
      direction,
      data,
      timestamp,
    }
  }

  private highestTier(types: PulseSignalType[]): PulseSignalType {
    let bestIdx = -1
    let bestType: PulseSignalType = types[0]!
    for (const t of types) {
      const idx = TIER_ORDER.indexOf(t)
      if (idx > bestIdx) {
        bestIdx = idx
        bestType = t
      }
    }
    return bestType
  }

  private hasConsecutiveOiClimbs(
    _symbol: string,
    history: MarketSnapshot[],
    current: MarketSnapshot,
    required: number,
  ): boolean {
    // We need `required` consecutive OI climbs of >= 5%.
    // The current scan is one climb. Check previous ones from history.
    // history includes all past snapshots (before current is pushed).
    // We need required-1 more consecutive climbs from history.

    if (history.length < required - 1) {
      return false
    }

    // Check the most recent required-1 transitions in history
    // Plus the current transition (current vs history[-1]) which we already know is >= 5%
    let consecutiveCount = 1 // current transition counts as 1

    for (let i = history.length - 1; i >= 1; i--) {
      const curr = history[i]!
      const prev = history[i - 1]!
      const changePct = (curr.openInterest - prev.openInterest) / prev.openInterest * 100
      if (changePct >= 5) {
        consecutiveCount++
        if (consecutiveCount >= required) {
          return true
        }
      } else {
        break
      }
    }

    return consecutiveCount >= required
  }

  private applyFirstJump(signals: PulseSignal[]): void {
    for (let i = 0; i < signals.length; i++) {
      const signal = signals[i]!
      // FIRST_JUMP only applies to IMMEDIATE_MOVER or higher
      const tierIdx = TIER_ORDER.indexOf(signal.type)
      const immediateMoverIdx = TIER_ORDER.indexOf('IMMEDIATE_MOVER')
      if (tierIdx < immediateMoverIdx) {
        continue
      }

      // Find which sector this symbol belongs to
      const sector = this.findSector(signal.symbol)
      if (!sector) {
        continue
      }

      // Check if no signal was detected for this sector in last 3 scans
      const lastSectorScan = this.sectorSignalHistory.get(sector)
      if (lastSectorScan === undefined || this.scanCount - lastSectorScan > 3) {
        // First jump in sector — upgrade to FIRST_JUMP
        signals[i] = {
          ...signal,
          type: 'FIRST_JUMP',
          confidence: CONFIDENCE['FIRST_JUMP'],
        }
        this.sectorSignalHistory.set(sector, this.scanCount)
      } else {
        // Record this sector signal anyway for future FIRST_JUMP checks
        this.sectorSignalHistory.set(sector, this.scanCount)
      }
    }
  }

  private findSector(symbol: string): string | null {
    const baseCoin = symbol.replace(/-(?:PERP|USD|USDT)$/, '')
    for (const [sector, symbols] of this.sectorMap) {
      if (symbols.includes(baseCoin)) {
        return sector
      }
    }
    return null
  }
}
