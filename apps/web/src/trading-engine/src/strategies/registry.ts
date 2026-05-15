import type { BaseStrategy } from './base-strategy.js'
import { SimpleMM } from './mm/simple-mm.js'
import { AvellanedaMM } from './mm/avellaneda-mm.js'
import { EngineMM } from './mm/engine-mm.js'
import { RegimeMM } from './mm/regime-mm.js'
import { GridMM } from './mm/grid-mm.js'
import { LiquidationMM } from './mm/liquidation-mm.js'
import { FundingArb } from './arb/funding-arb.js'
import { BasisArb } from './arb/basis-arb.js'
import { MomentumBreakout } from './signal/momentum-breakout.js'
import { MeanReversion } from './signal/mean-reversion.js'
import { AggressiveTaker } from './signal/aggressive-taker.js'
import { LlmCustom } from './llm-custom.js'
import { PredictionMM } from './prediction/prediction-mm.js'

const STRATEGIES: Record<string, () => BaseStrategy> = {
  'simple-mm': () => new SimpleMM(),
  'avellaneda-mm': () => new AvellanedaMM(),
  'engine-mm': () => new EngineMM(),
  'regime-mm': () => new RegimeMM(),
  'grid-mm': () => new GridMM(),
  'liquidation-mm': () => new LiquidationMM(),
  'funding-arb': () => new FundingArb(),
  'basis-arb': () => new BasisArb(),
  'momentum-breakout': () => new MomentumBreakout(),
  'mean-reversion': () => new MeanReversion(),
  'aggressive-taker': () => new AggressiveTaker(),
  'llm-custom': () => new LlmCustom(),
  'prediction-mm': () => new PredictionMM(),
}

export function createStrategy(name: string): BaseStrategy {
  const factory = STRATEGIES[name]
  if (!factory) throw new Error(`Unknown strategy: ${name}`)
  return factory()
}

export function listStrategies(): string[] {
  return Object.keys(STRATEGIES)
}
