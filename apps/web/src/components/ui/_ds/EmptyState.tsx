/* GENERATED FILE — DO NOT EDIT.
   Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh. */
import type { ReactNode } from "react"
import { Card } from "./Card"

export interface EmptyStateProps {
  /** Optional decorative SVG (rendered above the heading) */
  icon?: ReactNode
  title: ReactNode
  body?: ReactNode
  /** Primary call to action. */
  action?: ReactNode
}

export function EmptyState({ icon, title, body, action }: EmptyStateProps) {
  return (
    <Card className="text-center py-10">
      {icon && (
        <div aria-hidden="true" className="mx-auto mb-4 text-[var(--color-text-tertiary)]">
          {icon}
        </div>
      )}
      <h2 className="text-sm font-semibold text-[var(--color-text-primary)] m-0">
        {title}
      </h2>
      {body && (
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)] max-w-md mx-auto">
          {body}
        </p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </Card>
  )
}
