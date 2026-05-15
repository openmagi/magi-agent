import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import type { EngineConfig, GuardPreset } from '../types.js'
import { APEX_PRESETS, GUARD_PRESETS } from '../types.js'

const CONFIG_FILENAME = 'engine.json'
const VALID_EXCHANGES = ['hyperliquid', 'binance', 'alpaca', 'polymarket', 'kium', 'kis'] as const

export function loadConfig(configDir: string): EngineConfig {
  const filePath = join(configDir, CONFIG_FILENAME)
  if (!existsSync(filePath)) {
    throw new Error(`Config not found: ${filePath}`)
  }
  const raw = readFileSync(filePath, 'utf-8')
  return JSON.parse(raw) as EngineConfig
}

export function saveConfig(configDir: string, config: EngineConfig): void {
  if (!existsSync(configDir)) {
    mkdirSync(configDir, { recursive: true })
  }
  const filePath = join(configDir, CONFIG_FILENAME)
  writeFileSync(filePath, JSON.stringify(config, null, 2), 'utf-8')
}

export function createDefaultConfig(
  exchangeName: typeof VALID_EXCHANGES[number],
  preset: 'conservative' | 'default' | 'aggressive' = 'default',
  guardPreset: GuardPreset = 'moderate'
): EngineConfig {
  const apexPreset = APEX_PRESETS[preset] ?? {}

  return {
    exchange: {
      name: exchangeName,
      testnet: true,
    },
    apex: {
      preset,
      tickIntervalMs: 60000,
      ...apexPreset,
    } as EngineConfig['apex'],
    guard: { ...GUARD_PRESETS[guardPreset] },
    strategy: {
      name: 'apex',
      symbols: [],
      params: {},
    },
    reflect: {
      autoAdjust: true,
      intervalTicks: 240,
    },
  }
}

export function validateConfig(config: EngineConfig): string[] {
  const errors: string[] = []

  // exchange.name
  if (!VALID_EXCHANGES.includes(config.exchange.name)) {
    errors.push(`exchange.name must be one of: ${VALID_EXCHANGES.join(', ')}`)
  }

  // apex.maxSlots
  if (config.apex.maxSlots < 1 || config.apex.maxSlots > 5) {
    errors.push('apex.maxSlots must be between 1 and 5')
  }

  // apex.leverage
  if (config.apex.leverage < 1 || config.apex.leverage > 100) {
    errors.push('apex.leverage must be between 1 and 100')
  }

  // apex.dailyLossLimit
  if (config.apex.dailyLossLimit <= 0) {
    errors.push('apex.dailyLossLimit must be greater than 0')
  }

  // apex.tickIntervalMs
  if (config.apex.tickIntervalMs < 1000) {
    errors.push('apex.tickIntervalMs must be at least 1000')
  }

  // guard.tiers
  if (!config.guard.tiers || config.guard.tiers.length < 1) {
    errors.push('guard.tiers must have at least 1 entry')
  }

  // strategy.name
  if (!config.strategy.name || config.strategy.name.trim() === '') {
    errors.push('strategy.name must be a non-empty string')
  }

  return errors
}
