import { describe, it, expect, jest, beforeEach, afterEach } from '@jest/globals'
import { EventEmitter } from 'events'

// ── Mock WebSocket ──────────────────────────────────────────────────────────

interface MockWSInstance extends EventEmitter {
  readyState: number
  send: jest.Mock
  close: jest.Mock
  ping: jest.Mock
  terminate: jest.Mock
}

// Track all constructed mock WS instances
let mockInstances: MockWSInstance[] = []

class MockWebSocket extends EventEmitter {
  static CONNECTING = 0 as const
  static OPEN = 1 as const
  static CLOSING = 2 as const
  static CLOSED = 3 as const

  readyState: number
  send: jest.Mock
  close: jest.Mock
  ping: jest.Mock
  terminate: jest.Mock

  constructor(_url: string) {
    super()
    this.readyState = MockWebSocket.OPEN
    this.send = jest.fn()
    this.close = jest.fn()
    this.ping = jest.fn()
    this.terminate = jest.fn()
    mockInstances.push(this as unknown as MockWSInstance)

    // Auto-open on next tick to simulate real behavior
    setTimeout(() => this.emit('open'), 0)
  }
}

// ── Import ──────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/consistent-type-imports
let HlWebSocket: typeof import('./hl-websocket.js').HlWebSocket

beforeEach(async () => {
  jest.useFakeTimers()
  mockInstances = []
  const mod = await import('./hl-websocket.js')
  HlWebSocket = mod.HlWebSocket
})

afterEach(() => {
  jest.useRealTimers()
})

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeWs(opts?: { testnet?: boolean }): InstanceType<typeof HlWebSocket> {
  return new HlWebSocket({
    testnet: opts?.testnet ?? true,
    _WebSocket: MockWebSocket as unknown as typeof import('ws').default,
  })
}

function latestMock(): MockWSInstance {
  const inst = mockInstances[mockInstances.length - 1]
  if (!inst) throw new Error('No mock WS instance created')
  return inst
}

/**
 * Connect and wait for the open event.
 * Uses advanceTimersByTime(1) to fire the setTimeout(0) mock open,
 * without triggering infinite loops from the heartbeat setInterval.
 */
async function connectWs(ws: InstanceType<typeof HlWebSocket>): Promise<void> {
  const p = ws.connect()
  jest.advanceTimersByTime(1)
  await p
}

// ── Constructor & URL ───────────────────────────────────────────────────────

describe('HlWebSocket constructor', () => {
  it('uses testnet URL when testnet is true', () => {
    const ws = makeWs({ testnet: true })
    expect(ws.url).toBe('wss://api.hyperliquid-testnet.xyz/ws')
  })

  it('uses mainnet URL when testnet is false', () => {
    const ws = makeWs({ testnet: false })
    expect(ws.url).toBe('wss://api.hyperliquid.xyz/ws')
  })
})

// ── connect() ───────────────────────────────────────────────────────────────

describe('HlWebSocket.connect', () => {
  it('creates a WebSocket connection', async () => {
    const ws = makeWs()
    await connectWs(ws)
    expect(mockInstances).toHaveLength(1)
  })

  it('resolves the connect promise when ws fires open', async () => {
    const ws = makeWs()
    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await expect(p).resolves.toBeUndefined()
  })

  it('emits connected event on open', async () => {
    const ws = makeWs()
    const connectedCb = jest.fn()
    ws.on('connected', connectedCb)
    await connectWs(ws)
    expect(connectedCb).toHaveBeenCalledTimes(1)
  })

  it('rejects if connection fails', async () => {
    const FailingMockWebSocket = class extends EventEmitter {
      static CONNECTING = 0 as const
      static OPEN = 1 as const
      static CLOSING = 2 as const
      static CLOSED = 3 as const
      readyState = 0
      send = jest.fn()
      close = jest.fn()
      ping = jest.fn()
      terminate = jest.fn()
      constructor(_url: string) {
        super()
        mockInstances.push(this as unknown as MockWSInstance)
        setTimeout(() => this.emit('error', new Error('Connection refused')), 0)
      }
    }

    const ws = new HlWebSocket({
      testnet: true,
      _WebSocket: FailingMockWebSocket as unknown as typeof import('ws').default,
    })
    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await expect(p).rejects.toThrow('Connection refused')
  })
})

// ── subscribe() ─────────────────────────────────────────────────────────────

describe('HlWebSocket.subscribe', () => {
  it('sends allMids subscription message', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('allMids')

    const mock = latestMock()
    expect(mock.send).toHaveBeenCalledWith(
      JSON.stringify({
        method: 'subscribe',
        subscription: { type: 'allMids' },
      }),
    )
  })

  it('sends l2Book subscription with coin param', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('l2Book', { coin: 'ETH' })

    const mock = latestMock()
    expect(mock.send).toHaveBeenCalledWith(
      JSON.stringify({
        method: 'subscribe',
        subscription: { type: 'l2Book', coin: 'ETH' },
      }),
    )
  })

  it('sends l2Book subscription with nSigFigs param', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('l2Book', { coin: 'BTC', nSigFigs: 5 })

    const mock = latestMock()
    expect(mock.send).toHaveBeenCalledWith(
      JSON.stringify({
        method: 'subscribe',
        subscription: { type: 'l2Book', coin: 'BTC', nSigFigs: 5 },
      }),
    )
  })

  it('throws if not connected', () => {
    const ws = makeWs()
    expect(() => ws.subscribe('allMids')).toThrow('Not connected')
  })

  it('tracks active subscriptions for resubscribe on reconnect', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('allMids')
    ws.subscribe('l2Book', { coin: 'ETH' })

    expect(ws.subscriptions).toHaveLength(2)
  })
})

// ── Event emission: ticker ──────────────────────────────────────────────────

describe('HlWebSocket ticker events', () => {
  it('emits allMids event when allMids data arrives', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('allMids')

    const tickerCb = jest.fn<(t: Record<string, string>) => void>()
    ws.on('allMids', tickerCb)

    const mock = latestMock()
    const allMidsMsg = {
      channel: 'allMids',
      data: {
        mids: { ETH: '3450.5', BTC: '67000.0' },
      },
    }
    mock.emit('message', JSON.stringify(allMidsMsg))

    expect(tickerCb).toHaveBeenCalledTimes(1)
    expect(tickerCb).toHaveBeenCalledWith({
      ETH: '3450.5',
      BTC: '67000.0',
    })
  })
})

// ── Event emission: orderBook ───────────────────────────────────────────────

describe('HlWebSocket orderBook events', () => {
  it('emits l2Book event when l2Book data arrives', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('l2Book', { coin: 'ETH' })

    const bookCb = jest.fn<(book: { coin: string; levels: unknown[][] }) => void>()
    ws.on('l2Book', bookCb)

    const mock = latestMock()
    const l2BookMsg = {
      channel: 'l2Book',
      data: {
        coin: 'ETH',
        levels: [
          [{ px: '3449.0', sz: '1.5', n: 3 }],
          [{ px: '3451.0', sz: '1.0', n: 1 }],
        ],
        time: 1700000000000,
      },
    }
    mock.emit('message', JSON.stringify(l2BookMsg))

    expect(bookCb).toHaveBeenCalledTimes(1)
    const callArg = bookCb.mock.calls[0]![0]
    expect(callArg.coin).toBe('ETH')
    expect(callArg.levels).toHaveLength(2)
  })
})

// ── Error handling ──────────────────────────────────────────────────────────

describe('HlWebSocket error handling', () => {
  it('emits error event on ws error', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const errorCb = jest.fn<(e: Error) => void>()
    ws.on('error', errorCb)

    const mock = latestMock()
    mock.emit('error', new Error('WebSocket error'))

    expect(errorCb).toHaveBeenCalledTimes(1)
    expect(errorCb.mock.calls[0]![0].message).toBe('WebSocket error')
  })

  it('emits error on malformed JSON message', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const errorCb = jest.fn<(e: Error) => void>()
    ws.on('error', errorCb)

    const mock = latestMock()
    mock.emit('message', 'not valid json {{{')

    expect(errorCb).toHaveBeenCalledTimes(1)
  })
})

// ── Heartbeat ───────────────────────────────────────────────────────────────

describe('HlWebSocket heartbeat', () => {
  it('sends ping every 30 seconds', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const mock = latestMock()

    // Advance 30s
    jest.advanceTimersByTime(30_000)
    expect(mock.ping).toHaveBeenCalledTimes(1)

    // Advance another 30s
    jest.advanceTimersByTime(30_000)
    expect(mock.ping).toHaveBeenCalledTimes(2)
  })

  it('stops heartbeat on disconnect', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const mock = latestMock()

    ws.disconnect()

    jest.advanceTimersByTime(60_000)
    expect(mock.ping).toHaveBeenCalledTimes(0)
  })
})

// ── disconnect() ────────────────────────────────────────────────────────────

describe('HlWebSocket.disconnect', () => {
  it('closes the WebSocket connection', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const mock = latestMock()
    ws.disconnect()
    expect(mock.close).toHaveBeenCalledTimes(1)
  })

  it('emits disconnected event', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const disconnectCb = jest.fn()
    ws.on('disconnected', disconnectCb)

    ws.disconnect()
    expect(disconnectCb).toHaveBeenCalledTimes(1)
  })

  it('clears subscriptions on disconnect', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('allMids')
    ws.disconnect()

    expect(ws.subscriptions).toHaveLength(0)
  })

  it('is safe to call multiple times', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.disconnect()
    ws.disconnect()
    // Should not throw
  })
})

// ── Auto-reconnect ──────────────────────────────────────────────────────────

describe('HlWebSocket auto-reconnect', () => {
  it('reconnects on unexpected close', async () => {
    const ws = makeWs()
    await connectWs(ws)

    // Simulate unexpected close
    const mock = latestMock()
    mock.emit('close', 1006, 'Abnormal closure')

    // First retry: 1000ms backoff
    jest.advanceTimersByTime(1000)
    // The reconnect setTimeout fires at 1000ms, creating a new WS instance
    // Then the new instance auto-opens at setTimeout(0)
    jest.advanceTimersByTime(1)

    expect(mockInstances.length).toBeGreaterThanOrEqual(2)
  })

  it('does not reconnect on intentional disconnect', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.disconnect()

    // The close event should not trigger reconnect after intentional disconnect
    jest.advanceTimersByTime(60_000)

    // Only the initial WS instance
    expect(mockInstances).toHaveLength(1)
  })

  it('uses exponential backoff (1s, 2s, 4s, 8s, 16s)', async () => {
    let instanceCount = 0

    const ControlledMockWebSocket = class extends EventEmitter {
      static CONNECTING = 0 as const
      static OPEN = 1 as const
      static CLOSING = 2 as const
      static CLOSED = 3 as const
      readyState = 0
      send = jest.fn()
      close = jest.fn()
      ping = jest.fn()
      terminate = jest.fn()
      constructor(_url: string) {
        super()
        instanceCount++
        mockInstances.push(this as unknown as MockWSInstance)
        if (instanceCount === 1) {
          setTimeout(() => {
            this.readyState = 1
            this.emit('open')
          }, 0)
        } else {
          // Fail immediately (no open, just error + close)
          setTimeout(() => {
            this.emit('error', new Error('Connection failed'))
          }, 0)
        }
      }
    }

    const ws = new HlWebSocket({
      testnet: true,
      _WebSocket: ControlledMockWebSocket as unknown as typeof import('ws').default,
    })

    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await p

    // Trigger first close
    latestMock().emit('close', 1006, 'Abnormal closure')

    // Retry 1: after 1s
    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1) // fire setTimeout(0) for error
    expect(instanceCount).toBe(2)

    // Close event from failed reconnect
    latestMock().emit('close', 1006, 'Failed')

    // Retry 2: after 2s
    jest.advanceTimersByTime(2000)
    jest.advanceTimersByTime(1)
    expect(instanceCount).toBe(3)

    // Close event from failed reconnect
    latestMock().emit('close', 1006, 'Failed')

    // Retry 3: after 4s
    jest.advanceTimersByTime(4000)
    jest.advanceTimersByTime(1)
    expect(instanceCount).toBe(4)
  })

  it('stops reconnecting after max retries (5)', async () => {
    let instanceCount = 0

    const FailMockWebSocket = class extends EventEmitter {
      static CONNECTING = 0 as const
      static OPEN = 1 as const
      static CLOSING = 2 as const
      static CLOSED = 3 as const
      readyState = 0
      send = jest.fn()
      close = jest.fn()
      ping = jest.fn()
      terminate = jest.fn()
      constructor(_url: string) {
        super()
        instanceCount++
        mockInstances.push(this as unknown as MockWSInstance)
        if (instanceCount === 1) {
          setTimeout(() => {
            this.readyState = 1
            this.emit('open')
          }, 0)
        } else {
          setTimeout(() => this.emit('error', new Error('fail')), 0)
        }
      }
    }

    const ws = new HlWebSocket({
      testnet: true,
      _WebSocket: FailMockWebSocket as unknown as typeof import('ws').default,
    })

    const maxRetryCb = jest.fn()
    ws.on('maxRetriesReached', maxRetryCb)

    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await p

    // Trigger first close
    latestMock().emit('close', 1006, 'Abnormal')

    // Run through 5 reconnect attempts
    for (let i = 0; i < 5; i++) {
      const delay = Math.pow(2, i) * 1000 // 1s, 2s, 4s, 8s, 16s
      jest.advanceTimersByTime(delay)
      jest.advanceTimersByTime(1) // fire error setTimeout
      if (i < 4) {
        latestMock().emit('close', 1006, 'Failed')
      }
    }

    // After 5 retries, the 5th failed instance fires close
    latestMock().emit('close', 1006, 'Failed')

    // No more retries should happen
    jest.advanceTimersByTime(100_000)

    // 1 initial + 5 retries = 6 total
    expect(instanceCount).toBe(6)
    expect(maxRetryCb).toHaveBeenCalledTimes(1)
  })

  it('resubscribes after successful reconnect', async () => {
    let instanceCount = 0

    const ReconnectMockWebSocket = class extends EventEmitter {
      static CONNECTING = 0 as const
      static OPEN = 1 as const
      static CLOSING = 2 as const
      static CLOSED = 3 as const
      readyState = 1
      send = jest.fn()
      close = jest.fn()
      ping = jest.fn()
      terminate = jest.fn()
      constructor(_url: string) {
        super()
        instanceCount++
        mockInstances.push(this as unknown as MockWSInstance)
        setTimeout(() => {
          this.readyState = 1
          this.emit('open')
        }, 0)
      }
    }

    const ws = new HlWebSocket({
      testnet: true,
      _WebSocket: ReconnectMockWebSocket as unknown as typeof import('ws').default,
    })

    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await p

    // Subscribe to allMids and l2Book
    ws.subscribe('allMids')
    ws.subscribe('l2Book', { coin: 'ETH' })

    // Simulate unexpected close
    const firstMock = latestMock()
    firstMock.emit('close', 1006, 'Abnormal closure')

    // Wait for reconnect (1s backoff) + open event (setTimeout 0)
    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1)

    // The new WS instance should have received resubscription messages
    const newMock = latestMock()
    expect(newMock).not.toBe(firstMock)
    expect(newMock.send).toHaveBeenCalledTimes(2)

    const calls = newMock.send.mock.calls
    const msg1 = JSON.parse(calls[0]![0] as string) as Record<string, unknown>
    const msg2 = JSON.parse(calls[1]![0] as string) as Record<string, unknown>

    expect(msg1).toEqual({
      method: 'subscribe',
      subscription: { type: 'allMids' },
    })
    expect(msg2).toEqual({
      method: 'subscribe',
      subscription: { type: 'l2Book', coin: 'ETH' },
    })
  })

  it('resets retry count after successful reconnect', async () => {
    let instanceCount = 0

    const ReconnectMockWebSocket = class extends EventEmitter {
      static CONNECTING = 0 as const
      static OPEN = 1 as const
      static CLOSING = 2 as const
      static CLOSED = 3 as const
      readyState = 1
      send = jest.fn()
      close = jest.fn()
      ping = jest.fn()
      terminate = jest.fn()
      constructor(_url: string) {
        super()
        instanceCount++
        mockInstances.push(this as unknown as MockWSInstance)
        setTimeout(() => {
          this.readyState = 1
          this.emit('open')
        }, 0)
      }
    }

    const ws = new HlWebSocket({
      testnet: true,
      _WebSocket: ReconnectMockWebSocket as unknown as typeof import('ws').default,
    })

    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await p

    // Simulate close, reconnect, close again
    latestMock().emit('close', 1006, 'Abnormal')
    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1)
    expect(instanceCount).toBe(2)

    // Another close — retry count should have been reset, so backoff starts at 1s again
    latestMock().emit('close', 1006, 'Abnormal')
    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1)
    expect(instanceCount).toBe(3)
  })
})

// ── Message parsing edge cases ──────────────────────────────────────────────

describe('HlWebSocket message parsing', () => {
  it('ignores messages without a channel field', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const allMidsCb = jest.fn()
    ws.on('allMids', allMidsCb)

    const mock = latestMock()
    mock.emit('message', JSON.stringify({ type: 'subscriptionResponse', data: {} }))

    expect(allMidsCb).not.toHaveBeenCalled()
  })

  it('handles Buffer messages', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const allMidsCb = jest.fn()
    ws.on('allMids', allMidsCb)

    ws.subscribe('allMids')

    const mock = latestMock()
    const msg = {
      channel: 'allMids',
      data: { mids: { ETH: '3000.0' } },
    }
    mock.emit('message', Buffer.from(JSON.stringify(msg)))

    expect(allMidsCb).toHaveBeenCalledTimes(1)
  })
})

// ── isConnected ─────────────────────────────────────────────────────────────

describe('HlWebSocket.isConnected', () => {
  it('returns false before connect', () => {
    const ws = makeWs()
    expect(ws.isConnected).toBe(false)
  })

  it('returns true after connect', async () => {
    const ws = makeWs()
    await connectWs(ws)
    expect(ws.isConnected).toBe(true)
  })

  it('returns false after disconnect', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.disconnect()
    expect(ws.isConnected).toBe(false)
  })
})
