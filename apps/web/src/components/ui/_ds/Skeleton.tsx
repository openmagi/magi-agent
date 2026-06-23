/* GENERATED FILE — DO NOT EDIT.
   Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh. */
import { cn } from "./cn"

export function Skeleton({
  className, ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "bg-[var(--color-surface-overlay)] rounded animate-pulse",
        className,
      )}
      {...rest}
    />
  )
}

/** KPI-card-sized skeleton. */
export function SkeletonKPI() {
  return (
    <div className="bg-[var(--color-surface-raised)] border border-[var(--color-border-subtle)] rounded-lg p-4 space-y-3">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-7 w-16" />
    </div>
  )
}
