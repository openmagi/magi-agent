/**
 * Hyperliquid WebSocket client.
 * Real-time market data via WebSocket with auto-reconnect and heartbeat.
 */

import { EventEmitter } from 'events'
import WebSocket from 'ws'

// ── Config ──────────────────────────────────────────────────────────────────

export interface HlWebSocketConfig {
  testnet: boolean
  /** Injectable WebSocket class for testing */
  _WebSocket?: typeof WebSocket
}

// ── Subscription types ──────────────────────────────────────────────────────

export interface SubscriptionEntry {
  type: string
  params?: Record<string, string | number>
}

// ── Constants ───────────────────────────────────────────────────────────────

const TESTNET_WS_URL = 'wss://api.hyperliquid-testnet.xyz/ws'
const MAINNET_WS_URL = 'wss://api.hyperliquid.xyz/ws'

const HEARTBEAT_INTERVAL_MS = 30_000
const BASE_RECONNECT_DELAY_MS = 1_000
const MAX_RETRIES = 5

// ── Main class ──────────────────────────────────────────────────────────────

export class HlWebSocket extends EventEmitter {
  readonly url: string

  private readonly _WS: typeof WebSocket
  private _ws: WebSocket | null = null
  private _connected = false
  private _intentionalDisconnect = false
  private _retryCount = 0
  private _heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private _reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private _subscriptions: SubscriptionEntry[] = []

  constructor(config: HlWebSocketConfig) {
    super()
    this.url = config.testnet ? TESTNET_WS_URL : MAINNET_WS_URL
    this._WS = config._WebSocket ?? WebSocket
  }

  // ── Public API ──────────────────────────────────────────────────────────

  get isConnected(): boolean {
    return this._connected
  }

  get subscriptions(): SubscriptionEntry[] {
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
    this._stopHeartbeat()
    this._clearReconnectTimer()
    this._subscriptions = []

    if (this._ws) {
      this._connected = false
      this._ws.close()
      this._ws = null
      this.emit('disconnected')
    }
  }

  subscribe(type: string, params?: Record<string, string | number>): void {
    if (!this._connected || !this._ws) {
      throw new Error('Not connected')
    }

    this._sendSubscription({ type, params })
    this._subscriptions.push({ type, params })
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
      this._startHeartbeat()
      this.emit('connected')
      resolve?.()
    })

    ws.on('message', (data: WebSocket.Data) => {
      this._handleMessage(data)
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
      this._stopHeartbeat()

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

    const channel = parsed['channel']
    if (typeof channel !== 'string') {
      // Not a subscription message (e.g., subscriptionResponse) — ignore
      return
    }

    const rawData = parsed['data'] as Record<string, unknown> | undefined
    if (!rawData) return

    switch (channel) {
      case 'allMids': {
        this.emit('allMids', rawData['mids'])
        break
      }
      case 'l2Book': {
        this.emit('l2Book', rawData)
        break
      }
      default: {
        this.emit(channel, rawData)
        break
      }
    }
  }

  private _startHeartbeat(): void {
    this._stopHeartbeat()
    this._heartbeatTimer = setInterval(() => {
      if (this._ws && this._connected) {
        this._ws.ping()
      }
    }, HEARTBEAT_INTERVAL_MS)
  }

  private _stopHeartbeat(): void {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer)
      this._heartbeatTimer = null
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
        for (const sub of subs) {
          this._sendSubscription(sub)
        }
        this._subscriptions = subs
      },
      () => {
        // Error during reconnect — close handler will schedule next retry
      },
    )
  }

  private _sendSubscription(entry: SubscriptionEntry): void {
    const subscription: Record<string, string | number> = { type: entry.type }
    if (entry.params) {
      Object.assign(subscription, entry.params)
    }
    this._ws?.send(JSON.stringify({ method: 'subscribe', subscription }))
  }

  private _clearReconnectTimer(): void {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer)
      this._reconnectTimer = null
    }
  }
}
