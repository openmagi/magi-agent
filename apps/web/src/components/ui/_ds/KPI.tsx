/* GENERATED FILE — DO NOT EDIT.
   Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh. */
import type { ReactNode } from "react"
import { Card } from "./Card"
import { cn } from "./cn"

export interface KPIProps {
  label: ReactNode
  value: ReactNode
  /** Optional status/delta beside the value (e.g. <Badge>OK</Badge>). */
  trailing?: ReactNode
  /** Optional one-line footnote under the value. */
  footnote?: ReactNode
  /** When the value is rendered as text rather than a Badge. */
  tone?: "default" | "alert" | "status"
  className?: string
}

/** Dashboard KPI tile. Value is rendered with tabular-nums (inherited). */
export function KPI({
  label, value, trailing, footnote, tone = "default", className,
}: KPIProps) {
  return (
    <Card tone={tone} className={cn("flex flex-col gap-2", className)}>
      <div className="text-xs text-[var(--color-text-tertiary)]">{label}</div>
      <div className="flex items-baseline gap-3 flex-wrap">
        <div className="text-[28px] leading-8 font-semibold text-[var(--color-text-primary)]">
          {value}
        </div>
        {trailing && <div className="flex items-center gap-2">{trailing}</div>}
      </div>
      {footnote && (
        <div className="text-xs text-[var(--color-text-tertiary)]">{footnote}</div>
      )}
    </Card>
  )
}
