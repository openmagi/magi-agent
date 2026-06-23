/* GENERATED FILE — DO NOT EDIT.
   Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh. */
import type { ReactNode } from "react"

export interface PageHeaderProps {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}

/** Standard page header: h1, optional one-paragraph blurb, optional action slot. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)] m-0 text-balance">
            {title}
          </h1>
          {description && (
            <p className="mt-2 text-sm text-[var(--color-text-tertiary)] max-w-3xl text-pretty">
              {description}
            </p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </div>
  )
}
