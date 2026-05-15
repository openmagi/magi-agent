import { describe, it, expect } from '@jest/globals'
import { createStrategy, listStrategies } from './registry.js'
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
import { BaseStrategy } from './base-strategy.js'

describe('Strategy Registry', () => {
  describe('createStrategy', () => {
    it('returns a SimpleMM instance for "simple-mm"', () => {
      const s = createStrategy('simple-mm')
      expect(s).toBeInstanceOf(SimpleMM)
      expect(s.name).toBe('simple-mm')
    })

    it('returns an AvellanedaMM instance for "avellaneda-mm"', () => {
      const s = createStrategy('avellaneda-mm')
      expect(s).toBeInstanceOf(AvellanedaMM)
      expect(s.name).toBe('avellaneda-mm')
    })

    it('returns an EngineMM instance for "engine-mm"', () => {
      const s = createStrategy('engine-mm')
      expect(s).toBeInstanceOf(EngineMM)
    })

    it('returns a RegimeMM instance for "regime-mm"', () => {
      const s = createStrategy('regime-mm')
      expect(s).toBeInstanceOf(RegimeMM)
    })

    it('returns a GridMM instance for "grid-mm"', () => {
      const s = createStrategy('grid-mm')
      expect(s).toBeInstanceOf(GridMM)
    })

    it('returns a LiquidationMM instance for "liquidation-mm"', () => {
      const s = createStrategy('liquidation-mm')
      expect(s).toBeInstanceOf(LiquidationMM)
    })

    it('returns a FundingArb instance for "funding-arb"', () => {
      const s = createStrategy('funding-arb')
      expect(s).toBeInstanceOf(FundingArb)
    })

    it('returns a BasisArb instance for "basis-arb"', () => {
      const s = createStrategy('basis-arb')
      expect(s).toBeInstanceOf(BasisArb)
    })

    it('returns a MomentumBreakout instance for "momentum-breakout"', () => {
      const s = createStrategy('momentum-breakout')
      expect(s).toBeInstanceOf(MomentumBreakout)
    })

    it('returns a MeanReversion instance for "mean-reversion"', () => {
      const s = createStrategy('mean-reversion')
      expect(s).toBeInstanceOf(MeanReversion)
    })

    it('returns an AggressiveTaker instance for "aggressive-taker"', () => {
      const s = createStrategy('aggressive-taker')
      expect(s).toBeInstanceOf(AggressiveTaker)
    })

    it('returns an LlmCustom instance for "llm-custom"', () => {
      const s = createStrategy('llm-custom')
      expect(s).toBeInstanceOf(LlmCustom)
    })

    it('returns a PredictionMM instance for "prediction-mm"', () => {
      const s = createStrategy('prediction-mm')
      expect(s).toBeInstanceOf(PredictionMM)
      expect(s.name).toBe('prediction-mm')
    })

    it('throws for unknown strategy name', () => {
      expect(() => createStrategy('does-not-exist')).toThrow('Unknown strategy: does-not-exist')
    })

    it('creates a fresh instance on each call', () => {
      const a = createStrategy('simple-mm')
      const b = createStrategy('simple-mm')
      expect(a).not.toBe(b)
    })
  })

  describe('listStrategies', () => {
    it('returns all 13 strategy names', () => {
      const names = listStrategies()
      expect(names).toHaveLength(13)
    })

    it('includes every expected strategy name', () => {
      const names = listStrategies()
      const expected = [
        'simple-mm',
        'avellaneda-mm',
        'engine-mm',
        'regime-mm',
        'grid-mm',
        'liquidation-mm',
        'funding-arb',
        'basis-arb',
        'momentum-breakout',
        'mean-reversion',
        'aggressive-taker',
        'llm-custom',
        'prediction-mm',
      ]
      for (const name of expected) {
        expect(names).toContain(name)
      }
    })

    it('every listed strategy can be created successfully', () => {
      const names = listStrategies()
      for (const name of names) {
        const s = createStrategy(name)
        expect(s).toBeInstanceOf(BaseStrategy)
        expect(s.name).toBe(name)
      }
    })
  })
})
