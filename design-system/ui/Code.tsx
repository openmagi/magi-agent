import type { HTMLAttributes } from "react"
import { cn } from "./cn"

export interface CodeProps extends HTMLAttributes<HTMLElement> {
  inline?: boolean
}

/** Inline code. Always opts out of auto-translation. */
export function Code({ className, children, ...rest }: CodeProps) {
  return (
    <code
      translate="no"
      className={cn(
        "font-mono text-xs px-1 py-0.5 rounded",
        "bg-[var(--color-surface-input)] text-[var(--color-text-secondary)]",
        "border border-[var(--color-border-subtle)]",
        className,
      )}
      {...rest}
    >
      {children}
    </code>
  )
}

export interface CodeBlockProps extends HTMLAttributes<HTMLPreElement> {
  /** Max height (CSS value); above which the block scrolls. */
  maxHeight?: string
}

/** Block code/JSON. Keyboard-scrollable; opts out of translation. */
export function CodeBlock({
  className, maxHeight = "60vh", children, ...rest
}: CodeBlockProps) {
  return (
    <pre
      translate="no"
      tabIndex={0}
      style={{ maxHeight }}
      className={cn(
        "font-mono text-xs leading-5 overflow-auto p-3 rounded-md",
        "bg-[var(--color-surface-input)] text-[var(--color-text-secondary)]",
        "border border-[var(--color-border-subtle)]",
        "focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]",
        className,
      )}
      {...rest}
    >
      {children}
    </pre>
  )
}
