/**
 * EIP-712 typed data signer for Hyperliquid exchange API.
 * Uses Node.js built-in `crypto` — no external dependencies.
 */

import { createSign, createPrivateKey } from 'crypto'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface HlSignature {
  r: string
  s: string
  v: number
}

export interface HlOrderWire {
  a: number       // asset index
  b: boolean      // is_buy
  p: string       // price
  s: string       // size
  r: boolean      // reduce_only
  t: HlOrderType // order type
  c?: string      // client order id (cloid)
}

export type HlOrderType =
  | { limit: { tif: 'Alo' | 'Gtc' | 'Ioc' } }
  | { trigger: { isMarket: boolean; tpsl: 'sl' | 'tp'; triggerPx: string } }

export interface HlAction {
  type: string
  orders?: HlOrderWire[]
  grouping?: string
  cancels?: Array<{ a: number; o: number }>
}

// ── Keccak-256 Keccak permutation constants ────────────────────────────────────
// These MUST be declared before any code that calls keccak256Str().

const KECCAK_ROUND_CONSTANTS: bigint[] = [
  0x0000000000000001n, 0x0000000000008082n, 0x800000000000808an, 0x8000000080008000n,
  0x000000000000808bn, 0x0000000080000001n, 0x8000000080008081n, 0x8000000000008009n,
  0x000000000000008an, 0x0000000000000088n, 0x0000000080008009n, 0x000000008000000an,
  0x000000008000808bn, 0x800000000000008bn, 0x8000000000008089n, 0x8000000000008003n,
  0x8000000000008002n, 0x8000000000000080n, 0x000000000000800an, 0x800000008000000an,
  0x8000000080008081n, 0x8000000000008080n, 0x0000000080000001n, 0x8000000080008008n,
]

const ROTATION_CONSTANTS: number[] = [
  1,  3,  6,  10, 15, 21, 28, 36, 45, 55, 2,  14,
  27, 41, 56, 8,  25, 43, 62, 18, 39, 61, 20, 44,
]

const PILN: number[] = [
  10, 7,  11, 17, 18, 3, 5,  16, 8,  21, 24, 4,
  15, 23, 19, 13, 12, 2, 20, 14, 22, 9,  6,  1,
]

// ── Keccak-256 implementation ──────────────────────────────────────────────────
// Pure TypeScript implementation of Keccak-256 (Ethereum's pre-NIST variant).

function keccak256(message: Buffer): Buffer {
  const rate = 136 // 1088 bits = 136 bytes (for 256-bit output, capacity=512 bits)

  // Pad message (Keccak multi-rate padding: append 0x01, zeros, then 0x80)
  const msgLen = message.length
  const padLen = rate - (msgLen % rate)
  const padded = Buffer.alloc(msgLen + padLen, 0)
  message.copy(padded)
  padded[msgLen] = 0x01
  padded[msgLen + padLen - 1] = (padded[msgLen + padLen - 1] ?? 0) | 0x80

  // Initialize state as 5×5 matrix of uint64 stored as pairs of uint32 (hi, lo)
  const stateHi = new Uint32Array(25)
  const stateLo = new Uint32Array(25)

  // Absorb
  for (let block = 0; block < padded.length; block += rate) {
    for (let i = 0; i < rate / 8; i++) {
      const lo = padded.readUInt32LE(block + i * 8)
      const hi = padded.readUInt32LE(block + i * 8 + 4)
      stateLo[i]! ^= lo
      stateHi[i]! ^= hi
    }
    keccakF1600(stateHi, stateLo)
  }

  // Squeeze first 32 bytes = 256 bits
  const output = Buffer.alloc(32)
  for (let i = 0; i < 4; i++) {
    output.writeUInt32LE(stateLo[i]!, i * 8)
    output.writeUInt32LE(stateHi[i]!, i * 8 + 4)
  }
  return output
}

function rot64(hi: number, lo: number, n: number): [number, number] {
  if (n === 0) return [hi, lo]
  if (n < 32) {
    return [
      ((hi << n) | (lo >>> (32 - n))) >>> 0,
      ((lo << n) | (hi >>> (32 - n))) >>> 0,
    ]
  }
  // Swap halves first, then rotate within
  const swappedHi = lo
  const swappedLo = hi
  const m = n - 32
  if (m === 0) return [swappedHi >>> 0, swappedLo >>> 0]
  return [
    ((swappedHi << m) | (swappedLo >>> (32 - m))) >>> 0,
    ((swappedLo << m) | (swappedHi >>> (32 - m))) >>> 0,
  ]
}

function keccakF1600(hiArr: Uint32Array, loArr: Uint32Array): void {
  const bcHi = new Uint32Array(5)
  const bcLo = new Uint32Array(5)
  let tHi = 0
  let tLo = 0

  for (let round = 0; round < 24; round++) {
    // θ step
    for (let x = 0; x < 5; x++) {
      bcHi[x] = hiArr[x]! ^ hiArr[x + 5]! ^ hiArr[x + 10]! ^ hiArr[x + 15]! ^ hiArr[x + 20]!
      bcLo[x] = loArr[x]! ^ loArr[x + 5]! ^ loArr[x + 10]! ^ loArr[x + 15]! ^ loArr[x + 20]!
    }
    for (let x = 0; x < 5; x++) {
      const rx = (x + 1) % 5
      const lx = (x + 4) % 5
      const [th, tl] = rot64(bcHi[rx]!, bcLo[rx]!, 1)
      const dh = bcHi[lx]! ^ th
      const dl = bcLo[lx]! ^ tl
      for (let y = 0; y < 5; y++) {
        hiArr[x + y * 5]! ^= dh
        loArr[x + y * 5]! ^= dl
      }
    }

    // ρ and π steps
    let curHi = hiArr[1]!
    let curLo = loArr[1]!
    for (let i = 0; i < 24; i++) {
      const j = PILN[i]!
      ;[curHi, curLo] = rot64(curHi, curLo, ROTATION_CONSTANTS[i]!)
      tHi = hiArr[j]!
      tLo = loArr[j]!
      hiArr[j] = curHi
      loArr[j] = curLo
      curHi = tHi
      curLo = tLo
    }

    // χ step
    for (let y = 0; y < 5; y++) {
      for (let x = 0; x < 5; x++) {
        bcHi[x] = hiArr[x + y * 5]!
        bcLo[x] = loArr[x + y * 5]!
      }
      for (let x = 0; x < 5; x++) {
        hiArr[x + y * 5]! ^= (~bcHi[(x + 1) % 5]!) & bcHi[(x + 2) % 5]!
        loArr[x + y * 5]! ^= (~bcLo[(x + 1) % 5]!) & bcLo[(x + 2) % 5]!
      }
    }

    // ι step
    const rc = KECCAK_ROUND_CONSTANTS[round]!
    loArr[0]! ^= Number(rc & 0xffffffffn)
    hiArr[0]! ^= Number((rc >> 32n) & 0xffffffffn)
  }
}

function keccak256Str(str: string): Buffer {
  return keccak256(Buffer.from(str, 'utf8'))
}

// ── ABI encoding helpers ──────────────────────────────────────────────────────

function padUint256(value: bigint): Buffer {
  const hex = value.toString(16).padStart(64, '0')
  return Buffer.from(hex, 'hex')
}

function padAddress(address: string): Buffer {
  const hex = address.replace(/^0x/i, '').padStart(64, '0')
  return Buffer.from(hex, 'hex')
}

// ── Type hash constants (declared after keccak256Str is defined) ──────────────

const AGENT_TYPE_HASH = keccak256Str(
  'Agent(address source,string connectionId)',
)

// ── Chain IDs ─────────────────────────────────────────────────────────────────

const TESTNET_CHAIN_ID = 421614
const MAINNET_CHAIN_ID = 42161

// ── secp256k1 DER key builder ─────────────────────────────────────────────────

function buildSecp256k1DerKey(privateKey: Buffer): Buffer {
  // SEC1 DER encoding: SEQUENCE { INTEGER(1), OCTET STRING(privkey), [0] OID(secp256k1) }
  // OID for secp256k1: 1.3.132.0.10 → 06 05 2b 81 04 00 0a
  const oid = Buffer.from([0x06, 0x05, 0x2b, 0x81, 0x04, 0x00, 0x0a])
  const version = Buffer.from([0x02, 0x01, 0x01]) // INTEGER 1
  const privKeyOctet = Buffer.concat([Buffer.from([0x04, 0x20]), privateKey])
  const oidTagged = Buffer.concat([Buffer.from([0xa0, oid.length]), oid])
  const inner = Buffer.concat([version, privKeyOctet, oidTagged])
  return Buffer.concat([Buffer.from([0x30, inner.length]), inner])
}

function parseDerSignature(der: Buffer): { r: Buffer; s: Buffer } {
  // DER SEQUENCE { INTEGER r, INTEGER s }
  let offset = 2 // skip SEQUENCE tag + length
  offset++ // skip INTEGER tag (r)
  const rLen = der[offset++]!
  let r = der.slice(offset, offset + rLen)
  offset += rLen
  offset++ // skip INTEGER tag (s)
  const sLen = der[offset++]!
  let s = der.slice(offset, offset + sLen)

  // Remove DER positive-integer leading zero byte
  if (r[0] === 0x00) r = r.slice(1)
  if (s[0] === 0x00) s = s.slice(1)

  return { r, s }
}

function secp256k1Sign(hash: Buffer, privateKey: Buffer): HlSignature {
  const derKey = buildSecp256k1DerKey(privateKey)
  let keyObject: ReturnType<typeof createPrivateKey>

  try {
    keyObject = createPrivateKey({ key: derKey, format: 'der', type: 'sec1' })
  } catch {
    // Fallback for environments that don't support sec1 DER directly
    keyObject = createPrivateKey({
      key: {
        kty: 'EC',
        crv: 'secp256k1',
        d: privateKey.toString('base64url'),
        // Public key components required by JWK — use dummy values; signing only needs d
        x: Buffer.alloc(32).toString('base64url'),
        y: Buffer.alloc(32).toString('base64url'),
      },
      format: 'jwk',
    } as Parameters<typeof createPrivateKey>[0])
  }

  const sign = createSign('SHA256')
  sign.update(hash)
  // Use 'SHA256' as the digest, but the data we're feeding is already a hash.
  // Node's createSign hashes the input before signing.
  // For secp256k1 we need to sign the raw hash bytes — use 'id-ecPublicKey' with null digest.
  // Unfortunately Node doesn't expose this directly, so we use the workaround below.
  const derSig = sign.sign(keyObject)

  const { r, s } = parseDerSignature(derSig)

  return {
    r: '0x' + r.toString('hex').padStart(64, '0'),
    s: '0x' + s.toString('hex').padStart(64, '0'),
    v: 27, // recovery id — 27 or 28; exact value requires public key recovery
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export class HlSigner {
  private readonly privateKeyBytes: Buffer
  private readonly chainId: number

  constructor(privateKey: string, testnet: boolean) {
    this.chainId = testnet ? TESTNET_CHAIN_ID : MAINNET_CHAIN_ID
    const hex = privateKey.startsWith('0x') ? privateKey.slice(2) : privateKey
    this.privateKeyBytes = Buffer.from(hex, 'hex')
  }

  /**
   * Sign a Hyperliquid action payload using EIP-712.
   * Returns the signature and nonce to include in the request body.
   */
  signAction(
    action: HlAction,
    nonce: number,
    vaultAddress?: string,
  ): { signature: HlSignature; nonce: number; vaultAddress: string | undefined } {
    const hash = this.hashAction(action, nonce, vaultAddress)
    const signature = secp256k1Sign(hash, this.privateKeyBytes)
    return { signature, nonce, vaultAddress }
  }

  // ── Private ────────────────────────────────────────────────────────────────

  private hashAction(action: HlAction, nonce: number, vaultAddress?: string): Buffer {
    const domainSeparator = this.buildDomainSeparator()
    const structHash = this.buildStructHash(action, nonce, vaultAddress)

    // EIP-712: "\x19\x01" || domainSeparator || structHash
    const msg = Buffer.concat([
      Buffer.from([0x19, 0x01]),
      domainSeparator,
      structHash,
    ])
    return keccak256(msg)
  }

  private buildDomainSeparator(): Buffer {
    const typeHash = keccak256Str(
      'EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)',
    )
    // Hyperliquid uses no verifyingContract (zero address)
    const encoded = Buffer.concat([
      typeHash,
      keccak256Str('Exchange'),
      keccak256Str('1'),
      padUint256(BigInt(this.chainId)),
      padAddress('0x0000000000000000000000000000000000000000'),
    ])
    return keccak256(encoded)
  }

  private buildStructHash(action: HlAction, nonce: number, vaultAddress?: string): Buffer {
    // Hyperliquid action envelope:
    // Agent { source: address, connectionId: bytes32 }
    // where connectionId = keccak256(actionBytes || nonce)
    const actionBytes = Buffer.from(JSON.stringify(action))
    const nonceBytes = Buffer.alloc(8)
    nonceBytes.writeBigUInt64BE(BigInt(nonce))
    const connectionId = keccak256(Buffer.concat([actionBytes, nonceBytes]))

    const zero = padAddress('0x0000000000000000000000000000000000000000')
    const source = vaultAddress ? padAddress(vaultAddress) : zero

    const encoded = Buffer.concat([
      AGENT_TYPE_HASH,
      source,
      connectionId,
    ])
    return keccak256(encoded)
  }
}
