import { mkdtempSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadConfig, saveConfig, createDefaultConfig, validateConfig } from './config.js'
import type { EngineConfig } from '../types.js'
import { APEX_PRESETS, GUARD_PRESETS } from '../types.js'

describe('Config Manager', () => {
  let tempDir: string

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), 'config-test-'))
  })

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true })
  })

  // --- createDefaultConfig ---

  describe('createDefaultConfig', () => {
    it('should produce valid config with correct default preset values', () => {
      const config = createDefaultConfig('hyperliquid')
      expect(config.exchange.name).toBe('hyperliquid')
      expect(config.exchange.testnet).toBe(true)
      expect(config.apex.preset).toBe('default')
      expect(config.apex.maxSlots).toBe(3)
      expect(config.apex.leverage).toBe(10)
      expect(config.apex.radarThreshold).toBe(170)
      expect(config.apex.dailyLossLimit).toBe(500)
      expect(config.apex.tickIntervalMs).toBe(60000)
      expect(config.guard).toEqual(GUARD_PRESETS['moderate'])
      expect(config.strategy.name).toBe('apex')
      expect(config.strategy.symbols).toEqual([])
      expect(config.strategy.params).toEqual({})
      expect(config.reflect.autoAdjust).toBe(true)
      expect(config.reflect.intervalTicks).toBe(240)

      const errors = validateConfig(config)
      expect(errors).toEqual([])
    })

    it('should use conservative preset with maxSlots=2, leverage=5', () => {
      const config = createDefaultConfig('binance', 'conservative')
      expect(config.apex.preset).toBe('conservative')
      expect(config.apex.maxSlots).toBe(2)
      expect(config.apex.leverage).toBe(5)
      expect(config.apex.radarThreshold).toBe(190)
      expect(config.apex.dailyLossLimit).toBe(250)
    })

    it('should use aggressive preset with maxSlots=3, leverage=15', () => {
      const config = createDefaultConfig('alpaca', 'aggressive')
      expect(config.apex.preset).toBe('aggressive')
      expect(config.apex.maxSlots).toBe(3)
      expect(config.apex.leverage).toBe(15)
      expect(config.apex.radarThreshold).toBe(150)
      expect(config.apex.dailyLossLimit).toBe(1000)
    })
  })

  // --- validateConfig ---

  describe('validateConfig', () => {
    it('should return empty array for valid config', () => {
      const config = createDefaultConfig('hyperliquid')
      const errors = validateConfig(config)
      expect(errors).toEqual([])
    })

    it('should catch invalid maxSlots (0)', () => {
      const config = createDefaultConfig('hyperliquid')
      config.apex.maxSlots = 0
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('maxSlots'))).toBe(true)
    })

    it('should catch invalid maxSlots (6)', () => {
      const config = createDefaultConfig('hyperliquid')
      config.apex.maxSlots = 6
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('maxSlots'))).toBe(true)
    })

    it('should catch invalid leverage (0)', () => {
      const config = createDefaultConfig('hyperliquid')
      config.apex.leverage = 0
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('leverage'))).toBe(true)
    })

    it('should catch invalid leverage (101)', () => {
      const config = createDefaultConfig('hyperliquid')
      config.apex.leverage = 101
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('leverage'))).toBe(true)
    })

    it('should catch empty strategy name', () => {
      const config = createDefaultConfig('hyperliquid')
      config.strategy.name = ''
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('strategy'))).toBe(true)
    })

    it('should catch invalid exchange name', () => {
      const config = createDefaultConfig('hyperliquid')
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ;(config.exchange as any).name = 'kraken'
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('exchange'))).toBe(true)
    })

    it('should catch invalid dailyLossLimit (0)', () => {
      const config = createDefaultConfig('hyperliquid')
      config.apex.dailyLossLimit = 0
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('dailyLossLimit'))).toBe(true)
    })

    it('should catch invalid tickIntervalMs (999)', () => {
      const config = createDefaultConfig('hyperliquid')
      config.apex.tickIntervalMs = 999
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('tickIntervalMs'))).toBe(true)
    })

    it('should catch empty guard tiers', () => {
      const config = createDefaultConfig('hyperliquid')
      config.guard.tiers = []
      const errors = validateConfig(config)
      expect(errors.length).toBeGreaterThan(0)
      expect(errors.some((e: string) => e.includes('tiers'))).toBe(true)
    })
  })

  // --- saveConfig / loadConfig ---

  describe('saveConfig and loadConfig', () => {
    it('should round-trip preserve config', () => {
      const config = createDefaultConfig('hyperliquid', 'aggressive', 'tight')
      saveConfig(tempDir, config)
      const loaded = loadConfig(tempDir)
      expect(loaded).toEqual(config)
    })

    it('should throw when file does not exist', () => {
      const nonExistent = join(tempDir, 'missing')
      expect(() => loadConfig(nonExistent)).toThrow('Config not found')
    })

    it('should create directory if needed', () => {
      const nestedDir = join(tempDir, 'deep', 'nested', 'dir')
      expect(existsSync(nestedDir)).toBe(false)
      const config = createDefaultConfig('binance')
      saveConfig(nestedDir, config)
      expect(existsSync(nestedDir)).toBe(true)
      const loaded = loadConfig(nestedDir)
      expect(loaded).toEqual(config)
    })
  })
})
