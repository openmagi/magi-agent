import { describe, it, expect } from '@jest/globals'
import { BnSigner } from './bn-signer.js'

describe('BnSigner', () => {
  const signer = new BnSigner('testSecretKey123')

  describe('sign', () => {
    it('should produce a valid HMAC-SHA256 hex signature', () => {
      const params = { symbol: 'ETHUSDT', side: 'BUY', quantity: '0.1', timestamp: '1700000000000' }
      const sig = signer.sign(params)
      expect(sig).toMatch(/^[a-f0-9]{64}$/)
    })

    it('should produce deterministic signatures for same input', () => {
      const params = { symbol: 'BTCUSDT', timestamp: '1700000000000' }
      expect(signer.sign(params)).toBe(signer.sign(params))
    })

    it('should produce different signatures for different params', () => {
      const a = signer.sign({ symbol: 'ETHUSDT', timestamp: '1700000000000' })
      const b = signer.sign({ symbol: 'BTCUSDT', timestamp: '1700000000000' })
      expect(a).not.toBe(b)
    })

    it('should produce different signatures for different keys', () => {
      const other = new BnSigner('differentKey456')
      const params = { symbol: 'ETHUSDT', timestamp: '1700000000000' }
      expect(signer.sign(params)).not.toBe(other.sign(params))
    })

    it('should sort params alphabetically before signing', () => {
      const a = signer.sign({ b: '2', a: '1', timestamp: '1000' })
      const b = signer.sign({ a: '1', b: '2', timestamp: '1000' })
      expect(a).toBe(b)
    })
  })

  describe('signQueryString', () => {
    it('should append signature to query string', () => {
      const qs = signer.signQueryString({ symbol: 'ETHUSDT', timestamp: '1700000000000' })
      expect(qs).toContain('symbol=ETHUSDT')
      expect(qs).toContain('timestamp=1700000000000')
      expect(qs).toContain('&signature=')
      // Signature is last param
      expect(qs).toMatch(/&signature=[a-f0-9]{64}$/)
    })

    it('should produce sorted query string params', () => {
      const qs = signer.signQueryString({ z: '26', a: '1', m: '13' })
      const beforeSig = qs.split('&signature=')[0]!
      expect(beforeSig).toBe('a=1&m=13&z=26')
    })
  })
})
