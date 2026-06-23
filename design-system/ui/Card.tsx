import type { HTMLAttributes } from "react"
import { cn } from "./cn"

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** `interactive` adds hover lift + cursor-pointer; use when the whole card is a link. */
  interactive?: boolean
  /** `tone="alert"` swaps border to deny color; for error banners. */
  tone?: "default" | "alert" | "status"
  /** drops the default p-4. for cards that fill themselves (tables etc.) */
  noPadding?: boolean
}

const TONES = {
  default: "border-[var(--color-border-subtle)]",
  alert:   "border-[var(--color-deny-fg)] bg-[var(--color-deny-bg)]/30",
  status:  "border-[var(--color-pass-fg)] bg-[var(--color-pass-bg)]/30",
} as const

export function Card({
  interactive, tone = "default", noPadding, className, children, ...rest
}: CardProps) {
  return (
    <div
      className={cn(
        "bg-[var(--color-surface-raised)] rounded-lg border",
        noPadding ? "" : "p-4",
        TONES[tone],
        interactive &&
          "cursor-pointer transition-colors duration-150 hover:border-[var(--color-border-focus)] hover:bg-[var(--color-surface-overlay)]",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
}

export function CardHeader({
  title, subtitle, action,
}: { title: React.ReactNode; subtitle?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 mb-3">
      <div className="min-w-0 flex-1">
        <div className="font-medium text-[var(--text-md)] text-[var(--color-text-primary)] text-balance">
          {title}
        </div>
        {subtitle && (
          <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {subtitle}
          </div>
        )}
      </div>
      {action}
    </div>
  )
}
