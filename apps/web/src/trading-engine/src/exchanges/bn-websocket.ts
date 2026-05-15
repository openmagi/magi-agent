/**
 * Binance USDM Futures WebSocket client.
 * Real-time market data via combined streams with auto-reconnect.
 */

import { EventEmitter } from 'events'
import WebSocket from 'ws'

// ── Config ──────────────────────────────────────────────────────────────────

export interface BnWebSocketConfig {
  testnet: boolean
  /** 'futures' (default) or 'spot' */
  market?: 'futures' | 'spot'
  /** Injectable WebSocket class for testing */
  _WebSocket?: typeof WebSocket
}

// ── Constants ───────────────────────────────────────────────────────────────

const FUTURES_TESTNET_WS_URL = 'wss://stream.binancefuture.com/ws'
const FUTURES_MAINNET_WS_URL = 'wss://fstream.binance.com/ws'

const BASE_RECONNECT_DELAY_MS = 1_000
const MAX_RETRIES = 5

// ── Stream type detection ───────────────────────────────────────────────────

function detectStreamType(streamName: string): string | null {
  if (streamName.includes('@ticker')) return 'ticker'
  if (streamName.includes('@depth')) return 'depth'
  if (streamName.includes('@kline')) return 'kline'
  return null
}

// ── Main class ──────────────────────────────────────────────────────────────

export class BnWebSocket extends EventEmitter {
  readonly url: string

  private readonly _WS: typeof WebSocket
  private _ws: WebSocket | null = null
  private _connected = false
  private _intentionalDisconnect = false
  private _retryCount = 0
  private _reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private _subscriptions: string[] = []

  constructor(config: BnWebSocketConfig) {
    super()
    const market = config.market ?? 'futures'
    if (market === 'futures') {
      this.url = config.testnet ? FUTURES_TESTNET_WS_URL : FUTURES_MAINNET_WS_URL
    } else {
      this.url = config.testnet
        ? 'wss://testnet.binance.vision/ws'
        : 'wss://stream.binance.com:9443/ws'
    }
    this._WS = config._WebSocket ?? WebSocket
  }

  // ── Public API ──────────────────────────────────────────────────────────

  get isConnected(): boolean {
    return this._connected
  }

  get subscriptions(): string[] {
    return [...this._subscriptions]
  }

  connect(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      this._intentionalDisconnect = false
      this._createConnection(resolve, reject)
    })
  }

  disconnect(): void {
    this._intentionalDisconnect = true
    this._clearReconnectTimer()
    this._subscriptions = []

    if (this._ws) {
      this._connected = false
      this._ws.close()
      this._ws = null
      this.emit('disconnected')
    }
  }

  subscribe(...streams: string[]): void {
    if (!this._connected || !this._ws) {
      throw new Error('Not connected')
    }

    this._sendSubscription(streams)
    for (const s of streams) {
      if (!this._subscriptions.includes(s)) {
        this._subscriptions.push(s)
      }
    }
  }

  // ── Private ─────────────────────────────────────────────────────────────

  private _createConnection(
    resolve?: (value: void) => void,
    reject?: (reason: Error) => void,
  ): void {
    const ws = new this._WS(this.url)
    this._ws = ws

    ws.on('open', () => {
      this._connected = true
      this._retryCount = 0
      this.emit('connected')
      resolve?.()
    })

    ws.on('message', (data: WebSocket.Data) => {
      this._handleMessage(data)
    })

    ws.on('ping', () => {
      if (this._ws && this._connected) {
        this._ws.pong()
      }
    })

    ws.on('error', (err: Error) => {
      if (!this._connected && reject) {
        reject(err)
        return
      }
      this.emit('error', err)
    })

    ws.on('close', (_code: number, _reason: string) => {
      this._connected = false

      if (!this._intentionalDisconnect) {
        this._scheduleReconnect()
      }
    })
  }

  private _handleMessage(data: WebSocket.Data): void {
    let text: string
    if (Buffer.isBuffer(data)) {
      text = data.toString('utf-8')
    } else if (typeof data === 'string') {
      text = data
    } else {
      return
    }

    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(text) as Record<string, unknown>
    } catch {
      this.emit('error', new Error(`Failed to parse WebSocket message: ${text.slice(0, 100)}`))
      return
    }

    // Binance combined stream format: { stream: "ethusdt@ticker", data: {...} }
    const stream = parsed['stream']
    if (typeof stream !== 'string') {
      // Subscription response or other non-stream message — ignore
      return
    }

    const rawData = parsed['data'] as Record<string, unknown> | undefined
    if (!rawData) return

    const streamType = detectStreamType(stream)
    if (streamType) {
      this.emit(streamType, rawData)
    }
  }

  private _scheduleReconnect(): void {
    if (this._retryCount >= MAX_RETRIES) {
      this.emit('maxRetriesReached')
      return
    }

    const delay = BASE_RECONNECT_DELAY_MS * Math.pow(2, this._retryCount)
    this._retryCount++

    this._reconnectTimer = setTimeout(() => {
      this._reconnect()
    }, delay)
  }

  private _reconnect(): void {
    const subs = [...this._subscriptions]

    this._createConnection(
      () => {
        // Resubscribe on successful reconnect
        if (subs.length > 0) {
          this._sendSubscription(subs)
        }
        this._subscriptions = subs
      },
      () => {
        // Error during reconnect — close handler will schedule next retry
      },
    )
  }

  private _sendSubscription(streams: string[]): void {
    const msg = {
      method: 'SUBSCRIBE',
      params: streams,
      id: Date.now(),
    }
    this._ws?.send(JSON.stringify(msg))
  }

  private _clearReconnectTimer(): void {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer)
      this._reconnectTimer = null
    }
  }
}
