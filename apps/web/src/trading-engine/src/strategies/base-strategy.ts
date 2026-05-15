import type { TickContext, StrategyDecision } from '../types.js'

export abstract class BaseStrategy {
  abstract readonly name: string
  abstract onTick(ctx: TickContext): StrategyDecision[] | Promise<StrategyDecision[]>
}
