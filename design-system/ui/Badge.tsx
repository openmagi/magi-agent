import type { HTMLAttributes } from "react"
import { cn } from "./cn"

export type BadgeVariant =
  | "default" | "ok" | "review" | "deny" | "info" | "muted"

const VARIANTS: Record<BadgeVariant, string> = {
  default: "bg-[var(--color-surface-overlay)] text-[var(--color-text-secondary)]",
  ok:      "bg-[var(--color-pass-bg)]   text-[var(--color-pass-fg)]",
  review:  "bg-[var(--color-review-bg)] text-[var(--color-review-fg)]",
  deny:    "bg-[var(--color-deny-bg)]   text-[var(--color-deny-fg)]",
  info:    "bg-[var(--color-info-bg)]   text-[var(--color-info-fg)]",
  muted:   "bg-transparent border border-[var(--color-border-subtle)] text-[var(--color-text-tertiary)]",
}

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant
}

export function Badge({
  variant = "default", className, children, ...rest
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-1.5 py-0.5",
        "rounded text-xs leading-4 font-medium",
        VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  )
}
