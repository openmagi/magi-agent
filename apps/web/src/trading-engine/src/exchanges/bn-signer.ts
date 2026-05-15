/**
 * Binance HMAC-SHA256 request signer.
 * Signs query string parameters for authenticated Binance API requests.
 */

import { createHmac } from 'node:crypto'

export class BnSigner {
  private readonly secret: string

  constructor(secret: string) {
    this.secret = secret
  }

  /** HMAC-SHA256 sign a params object. Params are sorted alphabetically. */
  sign(params: Record<string, string>): string {
    const queryString = this.buildQueryString(params)
    return createHmac('sha256', this.secret).update(queryString).digest('hex')
  }

  /** Build query string with appended signature. */
  signQueryString(params: Record<string, string>): string {
    const queryString = this.buildQueryString(params)
    const signature = createHmac('sha256', this.secret).update(queryString).digest('hex')
    return `${queryString}&signature=${signature}`
  }

  private buildQueryString(params: Record<string, string>): string {
    return Object.entries(params)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&')
  }
}
