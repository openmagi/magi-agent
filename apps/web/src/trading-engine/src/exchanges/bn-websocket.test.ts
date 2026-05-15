import { describe, it, expect, jest, beforeEach, afterEach } from '@jest/globals'
import { EventEmitter } from 'events'

// ── Mock WebSocket ──────────────────────────────────────────────────────────

interface MockWSInstance extends EventEmitter {
  readyState: number
  send: jest.Mock
  close: jest.Mock
  ping: jest.Mock
  pong: jest.Mock
  terminate: jest.Mock
}

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
  pong: jest.Mock
  terminate: jest.Mock
  url: string

  constructor(url: string) {
    super()
    this.url = url
    this.readyState = MockWebSocket.OPEN
    this.send = jest.fn()
    this.close = jest.fn()
    this.ping = jest.fn()
    this.pong = jest.fn()
    this.terminate = jest.fn()
    mockInstances.push(this as unknown as MockWSInstance)

    // Auto-open on next tick to simulate real behavior
    setTimeout(() => this.emit('open'), 0)
  }
}

// ── Import ──────────────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/consistent-type-imports
let BnWebSocket: typeof import('./bn-websocket.js').BnWebSocket

beforeEach(async () => {
  jest.useFakeTimers()
  mockInstances = []
  const mod = await import('./bn-websocket.js')
  BnWebSocket = mod.BnWebSocket
})

afterEach(() => {
  jest.useRealTimers()
})

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeWs(opts?: { testnet?: boolean }): InstanceType<typeof BnWebSocket> {
  return new BnWebSocket({
    testnet: opts?.testnet ?? true,
    _WebSocket: MockWebSocket as unknown as typeof import('ws').default,
  })
}

function latestMock(): MockWSInstance {
  const inst = mockInstances[mockInstances.length - 1]
  if (!inst) throw new Error('No mock WS instance created')
  return inst
}

async function connectWs(ws: InstanceType<typeof BnWebSocket>): Promise<void> {
  const p = ws.connect()
  jest.advanceTimersByTime(1)
  await p
}

// ── Constructor & URL ───────────────────────────────────────────────────────

describe('BnWebSocket constructor', () => {
  it('uses testnet URL when testnet is true', () => {
    const ws = makeWs({ testnet: true })
    expect(ws.url).toContain('stream.binancefuture.com')
  })

  it('uses mainnet URL when testnet is false', () => {
    const ws = makeWs({ testnet: false })
    expect(ws.url).toContain('fstream.binance.com')
  })
})

// ── connect() ───────────────────────────────────────────────────────────────

describe('BnWebSocket.connect', () => {
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
      pong = jest.fn()
      terminate = jest.fn()
      constructor(_url: string) {
        super()
        mockInstances.push(this as unknown as MockWSInstance)
        setTimeout(() => this.emit('error', new Error('Connection refused')), 0)
      }
    }

    const ws = new BnWebSocket({
      testnet: true,
      _WebSocket: FailingMockWebSocket as unknown as typeof import('ws').default,
    })
    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await expect(p).rejects.toThrow('Connection refused')
  })
})

// ── subscribe() ─────────────────────────────────────────────────────────────

describe('BnWebSocket.subscribe', () => {
  it('subscribes to ticker stream', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('ethusdt@ticker')

    const mock = latestMock()
    const sent = JSON.parse(mock.send.mock.calls[0]![0] as string) as Record<string, unknown>
    expect(sent['method']).toBe('SUBSCRIBE')
    expect(sent['params']).toEqual(['ethusdt@ticker'])
  })

  it('subscribes to depth stream', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('ethusdt@depth20@100ms')

    const mock = latestMock()
    const sent = JSON.parse(mock.send.mock.calls[0]![0] as string) as Record<string, unknown>
    expect(sent['params']).toEqual(['ethusdt@depth20@100ms'])
  })

  it('subscribes to kline stream', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('ethusdt@kline_1m')

    const mock = latestMock()
    const sent = JSON.parse(mock.send.mock.calls[0]![0] as string) as Record<string, unknown>
    expect(sent['params']).toEqual(['ethusdt@kline_1m'])
  })

  it('subscribes to multiple streams at once', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('ethusdt@ticker', 'ethusdt@depth20@100ms')

    const mock = latestMock()
    const sent = JSON.parse(mock.send.mock.calls[0]![0] as string) as Record<string, unknown>
    expect(sent['params']).toEqual(['ethusdt@ticker', 'ethusdt@depth20@100ms'])
  })

  it('throws if not connected', () => {
    const ws = makeWs()
    expect(() => ws.subscribe('ethusdt@ticker')).toThrow('Not connected')
  })

  it('tracks active subscriptions', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('ethusdt@ticker')
    ws.subscribe('ethusdt@depth20@100ms')

    expect(ws.subscriptions).toHaveLength(2)
    expect(ws.subscriptions).toContain('ethusdt@ticker')
    expect(ws.subscriptions).toContain('ethusdt@depth20@100ms')
  })
})

// ── Message parsing: ticker ──────────────────────────────────────────────────

describe('BnWebSocket ticker events', () => {
  it('emits ticker event when @ticker message arrives', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('ethusdt@ticker')

    const tickerCb = jest.fn()
    ws.on('ticker', tickerCb)

    const mock = latestMock()
    const tickerMsg = {
      stream: 'ethusdt@ticker',
      data: {
        e: '24hrTicker',
        s: 'ETHUSDT',
        c: '3450.50',
        o: '3400.00',
        h: '3500.00',
        l: '3350.00',
        v: '125000.5',
        q: '430000000.00',
      },
    }
    mock.emit('message', JSON.stringify(tickerMsg))

    expect(tickerCb).toHaveBeenCalledTimes(1)
    expect(tickerCb.mock.calls[0]![0]).toEqual(tickerMsg.data)
  })
})

// ── Message parsing: depth ───────────────────────────────────────────────────

describe('BnWebSocket depth events', () => {
  it('emits depth event when @depth message arrives', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('ethusdt@depth20@100ms')

    const depthCb = jest.fn()
    ws.on('depth', depthCb)

    const mock = latestMock()
    const depthMsg = {
      stream: 'ethusdt@depth20@100ms',
      data: {
        e: 'depthUpdate',
        s: 'ETHUSDT',
        b: [['3449.00', '1.500']],
        a: [['3451.00', '1.000']],
      },
    }
    mock.emit('message', JSON.stringify(depthMsg))

    expect(depthCb).toHaveBeenCalledTimes(1)
    expect(depthCb.mock.calls[0]![0]).toEqual(depthMsg.data)
  })
})

// ── Message parsing: kline ───────────────────────────────────────────────────

describe('BnWebSocket kline events', () => {
  it('emits kline event when @kline message arrives', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.subscribe('ethusdt@kline_1m')

    const klineCb = jest.fn()
    ws.on('kline', klineCb)

    const mock = latestMock()
    const klineMsg = {
      stream: 'ethusdt@kline_1m',
      data: {
        e: 'kline',
        s: 'ETHUSDT',
        k: {
          t: 1700000000000,
          o: '3400.0',
          h: '3500.0',
          l: '3350.0',
          c: '3450.0',
          v: '1000.0',
        },
      },
    }
    mock.emit('message', JSON.stringify(klineMsg))

    expect(klineCb).toHaveBeenCalledTimes(1)
    expect(klineCb.mock.calls[0]![0]).toEqual(klineMsg.data)
  })
})

// ── Message parsing: unknown streams ─────────────────────────────────────────

describe('BnWebSocket unknown streams', () => {
  it('ignores unknown stream types', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const tickerCb = jest.fn()
    const depthCb = jest.fn()
    const klineCb = jest.fn()
    ws.on('ticker', tickerCb)
    ws.on('depth', depthCb)
    ws.on('kline', klineCb)

    const mock = latestMock()
    mock.emit('message', JSON.stringify({
      stream: 'ethusdt@unknownstream',
      data: { foo: 'bar' },
    }))

    expect(tickerCb).not.toHaveBeenCalled()
    expect(depthCb).not.toHaveBeenCalled()
    expect(klineCb).not.toHaveBeenCalled()
  })
})

// ── Error handling ──────────────────────────────────────────────────────────

describe('BnWebSocket error handling', () => {
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

describe('BnWebSocket heartbeat', () => {
  it('responds to server ping with pong', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const mock = latestMock()
    mock.emit('ping')

    expect(mock.pong).toHaveBeenCalledTimes(1)
  })

  it('stops heartbeat on disconnect', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const mock = latestMock()
    ws.disconnect()

    mock.emit('ping')
    // After disconnect, pong should not respond
    // (pong was not called during connection since no ping was sent)
    expect(mock.pong).toHaveBeenCalledTimes(0)
  })
})

// ── disconnect() ────────────────────────────────────────────────────────────

describe('BnWebSocket.disconnect', () => {
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

    ws.subscribe('ethusdt@ticker')
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

describe('BnWebSocket auto-reconnect', () => {
  it('reconnects on unexpected close', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const mock = latestMock()
    mock.emit('close', 1006, 'Abnormal closure')

    // First retry: 1000ms backoff
    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1)

    expect(mockInstances.length).toBeGreaterThanOrEqual(2)
  })

  it('does not reconnect on intentional disconnect', async () => {
    const ws = makeWs()
    await connectWs(ws)

    ws.disconnect()

    jest.advanceTimersByTime(60_000)

    expect(mockInstances).toHaveLength(1)
  })

  it('uses exponential backoff (1s, 2s, 4s)', async () => {
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
      pong = jest.fn()
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
          setTimeout(() => {
            this.emit('error', new Error('Connection failed'))
          }, 0)
        }
      }
    }

    const ws = new BnWebSocket({
      testnet: true,
      _WebSocket: ControlledMockWebSocket as unknown as typeof import('ws').default,
    })

    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await p

    latestMock().emit('close', 1006, 'Abnormal closure')

    // Retry 1: after 1s
    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1)
    expect(instanceCount).toBe(2)

    latestMock().emit('close', 1006, 'Failed')

    // Retry 2: after 2s
    jest.advanceTimersByTime(2000)
    jest.advanceTimersByTime(1)
    expect(instanceCount).toBe(3)

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
      pong = jest.fn()
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

    const ws = new BnWebSocket({
      testnet: true,
      _WebSocket: FailMockWebSocket as unknown as typeof import('ws').default,
    })

    const maxRetryCb = jest.fn()
    ws.on('maxRetriesReached', maxRetryCb)

    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await p

    latestMock().emit('close', 1006, 'Abnormal')

    for (let i = 0; i < 5; i++) {
      const delay = Math.pow(2, i) * 1000
      jest.advanceTimersByTime(delay)
      jest.advanceTimersByTime(1)
      if (i < 4) {
        latestMock().emit('close', 1006, 'Failed')
      }
    }

    latestMock().emit('close', 1006, 'Failed')

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
      pong = jest.fn()
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

    const ws = new BnWebSocket({
      testnet: true,
      _WebSocket: ReconnectMockWebSocket as unknown as typeof import('ws').default,
    })

    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await p

    ws.subscribe('ethusdt@ticker')
    ws.subscribe('ethusdt@depth20@100ms')

    const firstMock = latestMock()
    firstMock.emit('close', 1006, 'Abnormal closure')

    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1)

    const newMock = latestMock()
    expect(newMock).not.toBe(firstMock)

    // The new instance should have re-subscribed with all previous streams
    // It sends one SUBSCRIBE message with all streams
    expect(newMock.send).toHaveBeenCalledTimes(1)
    const sent = JSON.parse(newMock.send.mock.calls[0]![0] as string) as Record<string, unknown>
    expect(sent['method']).toBe('SUBSCRIBE')
    expect(sent['params']).toEqual(['ethusdt@ticker', 'ethusdt@depth20@100ms'])
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
      pong = jest.fn()
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

    const ws = new BnWebSocket({
      testnet: true,
      _WebSocket: ReconnectMockWebSocket as unknown as typeof import('ws').default,
    })

    const p = ws.connect()
    jest.advanceTimersByTime(1)
    await p

    latestMock().emit('close', 1006, 'Abnormal')
    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1)
    expect(instanceCount).toBe(2)

    // Another close — retry count should reset, so backoff starts at 1s again
    latestMock().emit('close', 1006, 'Abnormal')
    jest.advanceTimersByTime(1000)
    jest.advanceTimersByTime(1)
    expect(instanceCount).toBe(3)
  })
})

// ── Message parsing edge cases ──────────────────────────────────────────────

describe('BnWebSocket message parsing', () => {
  it('ignores messages without a stream field', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const tickerCb = jest.fn()
    ws.on('ticker', tickerCb)

    const mock = latestMock()
    mock.emit('message', JSON.stringify({ result: null, id: 1 }))

    expect(tickerCb).not.toHaveBeenCalled()
  })

  it('handles Buffer messages', async () => {
    const ws = makeWs()
    await connectWs(ws)

    const tickerCb = jest.fn()
    ws.on('ticker', tickerCb)

    ws.subscribe('ethusdt@ticker')

    const mock = latestMock()
    const msg = {
      stream: 'ethusdt@ticker',
      data: { e: '24hrTicker', s: 'ETHUSDT', c: '3000.0' },
    }
    mock.emit('message', Buffer.from(JSON.stringify(msg)))

    expect(tickerCb).toHaveBeenCalledTimes(1)
  })
})

// ── isConnected ─────────────────────────────────────────────────────────────

describe('BnWebSocket.isConnected', () => {
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
