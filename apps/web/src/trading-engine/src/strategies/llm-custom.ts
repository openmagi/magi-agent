import type { TickContext, StrategyDecision, Position, Balance } from '../types.js'
import { BaseStrategy } from './base-strategy.js'

interface LlmRawDecision {
  action: string
  symbol: string
  size: number
  reason?: string
  stopLoss?: number
  takeProfit?: number
  confidence?: number
}

interface ChatCompletionResponse {
  choices: Array<{
    message: {
      content: string
    }
  }>
}

type FetchFn = typeof fetch

function formatNumber(n: number): string {
  return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

function formatPrice(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function totalUnrealizedPnl(positions: Position[]): number {
  return positions.reduce((sum, p) => sum + p.unrealizedPnl, 0)
}

function primaryBalance(balances: Balance[]): Balance | null {
  return balances[0] ?? null
}

export class LlmCustom extends BaseStrategy {
  readonly name = 'llm-custom'

  private readonly _fetch: FetchFn

  constructor(fetchFn?: FetchFn) {
    super()
    this._fetch = fetchFn ?? fetch
  }

  buildMarketSnapshot(ctx: TickContext): string {
    const { ticker, orderBook, positions, balances } = ctx

    const spread = ticker.ask - ticker.bid
    const spreadPct = ((spread / ticker.mid) * 100).toFixed(3)

    const vol = ticker.volume24h >= 1_000_000_000
      ? `$${(ticker.volume24h / 1_000_000_000).toFixed(1)}B`
      : ticker.volume24h >= 1_000_000
        ? `$${(ticker.volume24h / 1_000_000).toFixed(1)}M`
        : `$${ticker.volume24h.toFixed(0)}`

    const oi = ticker.openInterest >= 1_000_000_000
      ? `$${(ticker.openInterest / 1_000_000_000).toFixed(1)}B`
      : ticker.openInterest >= 1_000_000
        ? `$${(ticker.openInterest / 1_000_000).toFixed(1)}M`
        : `$${ticker.openInterest.toFixed(0)}`

    const fundingPct = (ticker.fundingRate * 100).toFixed(3)

    const topBid = orderBook.bids[0]
    const topAsk = orderBook.asks[0]

    let snapshot = `## Market Data\n`
    snapshot += `Symbol: ${ticker.symbol} | Price: $${formatNumber(ticker.mid)} | 24h Volume: ${vol}\n`
    snapshot += `Bid: $${formatPrice(topBid?.price ?? ticker.bid)} | Ask: $${formatPrice(topAsk?.price ?? ticker.ask)} | Spread: ${spreadPct}%\n`
    snapshot += `Funding: ${fundingPct}% | OI: ${oi}\n`

    if (positions.length > 0) {
      snapshot += `\n## Positions\n`
      for (const pos of positions) {
        const pnlSign = pos.unrealizedPnl >= 0 ? '+' : ''
        snapshot += `${pos.symbol} ${pos.side} ${pos.size} @ $${formatNumber(pos.entryPrice)} | PnL: ${pnlSign}$${pos.unrealizedPnl.toFixed(2)}\n`
      }
    }

    const bal = primaryBalance(balances)
    if (bal) {
      const unrealized = totalUnrealizedPnl(positions)
      const unrealizedStr = unrealized >= 0 ? `+$${unrealized.toFixed(2)}` : `-$${Math.abs(unrealized).toFixed(2)}`
      snapshot += `\n## Account\n`
      snapshot += `Equity: $${formatNumber(bal.total)} | Available: $${formatNumber(bal.available)} | Unrealized: ${unrealizedStr}\n`
    }

    return snapshot
  }

  private async callLlm(
    endpoint: string,
    systemPrompt: string,
    userMessage: string,
  ): Promise<LlmRawDecision | null> {
    const response = await this._fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userMessage },
        ],
      }),
    })

    if (!response.ok) {
      return null
    }

    const data = await response.json() as ChatCompletionResponse
    const content = data.choices[0]?.message?.content ?? ''

    try {
      return JSON.parse(content) as LlmRawDecision
    } catch {
      // Try extracting JSON from markdown code block
      const match = /```(?:json)?\s*([\s\S]*?)```/.exec(content)
      if (match?.[1]) {
        try {
          return JSON.parse(match[1]) as LlmRawDecision
        } catch {
          return null
        }
      }
      return null
    }
  }

  private validateDecision(raw: LlmRawDecision, ctx: TickContext): StrategyDecision | null {
    const { ticker, balances, config } = ctx

    const maxPositionPct = typeof config.params['max_position_pct'] === 'number'
      ? config.params['max_position_pct']
      : 10
    const maxLeverage = typeof config.params['max_leverage'] === 'number'
      ? config.params['max_leverage']
      : 10

    const action = raw.action as StrategyDecision['action']
    if (!['BUY', 'SELL', 'HOLD'].includes(action)) return null

    if (action === 'HOLD') {
      return {
        action: 'HOLD',
        symbol: raw.symbol ?? ticker.symbol,
        size: 0,
        orderType: 'GTC',
        confidence: raw.confidence ?? 0,
        reason: raw.reason ?? 'LLM: hold',
      }
    }

    const equity = balances.reduce((sum, b) => sum + b.total, 0)
    const price = ticker.mid
    const notionalValue = raw.size * price

    // Max position check: size * price ≤ maxPositionPct% of equity
    const maxNotional = equity * (maxPositionPct / 100)
    if (notionalValue > maxNotional) {
      return null
    }

    // All-in check: reject if notional > 50% of equity
    if (notionalValue > equity * 0.5) {
      return null
    }

    // Leverage cap check (simple: notional / equity)
    const impliedLeverage = notionalValue / equity
    if (impliedLeverage > maxLeverage) {
      return null
    }

    return {
      action,
      symbol: raw.symbol ?? ticker.symbol,
      size: raw.size,
      orderType: 'GTC',
      confidence: raw.confidence ?? 70,
      reason: raw.reason ?? 'LLM decision',
      stopLoss: raw.stopLoss,
      takeProfit: raw.takeProfit,
    }
  }

  private holdDecision(ctx: TickContext, reason: string): StrategyDecision {
    return {
      action: 'HOLD',
      symbol: ctx.ticker.symbol,
      size: 0,
      orderType: 'GTC',
      confidence: 0,
      reason,
    }
  }

  async onTick(ctx: TickContext): Promise<StrategyDecision[]> {
    const { config } = ctx
    const endpoint = typeof config.params['llm_endpoint'] === 'string'
      ? config.params['llm_endpoint']
      : 'http://chat-proxy/v1/chat/completions'

    const systemPrompt = config.strategyPrompt
      ?? 'You are a trading assistant. Respond ONLY with valid JSON matching the required schema.'

    const fullSystem = `${systemPrompt}

Respond ONLY with a JSON object in this exact format (no markdown, no explanation):
{"action":"BUY"|"SELL"|"HOLD","symbol":"<symbol>","size":<number>,"reason":"<string>","stopLoss":<number|null>,"takeProfit":<number|null>,"confidence":<0-100>}`

    const snapshot = this.buildMarketSnapshot(ctx)

    try {
      const raw = await this.callLlm(endpoint, fullSystem, snapshot)
      if (!raw) {
        return [this.holdDecision(ctx, 'LLM: no parseable response')]
      }

      const decision = this.validateDecision(raw, ctx)
      if (!decision) {
        return [this.holdDecision(ctx, 'LLM: decision failed validation')]
      }

      return [decision]
    } catch {
      return [this.holdDecision(ctx, 'LLM: error during inference')]
    }
  }
}
