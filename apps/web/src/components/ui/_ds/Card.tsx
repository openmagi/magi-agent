/* GENERATED FILE — DO NOT EDIT.
   Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh. */
import type { HTMLAttributes } from "react"
import { cn } from "./cn"
import { GlassSurface } from "./GlassSurface"

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** `interactive` adds hover lift + cursor-pointer; use when the whole card is a link. */
  interactive?: boolean
  /** `tone="alert"` swaps border to deny color; for error banners. */
  tone?: "default" | "alert" | "status"
  /** drops the default p-4. for cards that fill themselves (tables etc.) */
  noPadding?: boolean
}

// Semantic alert/status cards stay solid + colored — legibility of an error
// banner matters more than the glass effect.
const TONES = {
  alert:  "border-[var(--color-deny-fg)] bg-[var(--color-deny-bg)]/30",
  status: "border-[var(--color-pass-fg)] bg-[var(--color-pass-bg)]/30",
} as const

export function Card({
  interactive, tone = "default", noPadding, className, children, ...rest
}: CardProps) {
  if (tone !== "default") {
    return (
      <div
        className={cn(
          "rounded-lg border",
          noPadding ? "" : "p-4",
          TONES[tone],
          interactive && "cursor-pointer transition-[filter] duration-150 hover:brightness-105",
          className,
        )}
        {...rest}
      >
        {children}
      </div>
    )
  }
  // default cards are liquid glass (regular tier)
  return (
    <GlassSurface
      tier="regular"
      interactive={interactive}
      className={cn(noPadding ? "" : "p-4", className)}
      {...rest}
    >
      {children}
    </GlassSurface>
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
