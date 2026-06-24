import type { ReactNode } from "react"
import { Card } from "./Card"
import { Badge } from "./Badge"

export interface ErrorStateProps {
  title: ReactNode
  body?: ReactNode
  /** When set, displayed as a Badge before the title. */
  status?: string
  /** Actions (Retry, Open logs, etc.) */
  actions?: ReactNode
  severity?: "warning" | "error"
}

/** Standard inline error block. Used in lieu of toast for blocking errors. */
export function ErrorState({
  title, body, status, actions, severity = "error",
}: ErrorStateProps) {
  return (
    <Card tone={severity === "error" ? "alert" : "default"} role="alert">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {status && (
              <Badge variant={severity === "error" ? "deny" : "review"}>
                {status}
              </Badge>
            )}
            <span className="text-sm font-medium text-[var(--color-text-primary)]">
              {title}
            </span>
          </div>
          {body && (
            <div className="mt-2 text-xs text-[var(--color-text-tertiary)] leading-5">
              {body}
            </div>
          )}
        </div>
      </div>
      {actions && <div className="mt-3 flex flex-wrap gap-2">{actions}</div>}
    </Card>
  )
}
