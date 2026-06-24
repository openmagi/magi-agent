import { forwardRef, type ButtonHTMLAttributes } from "react"
import { cn } from "./cn"

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger"
export type ButtonSize = "sm" | "md" | "lg"

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--color-accent)] text-[var(--color-text-on-accent)] " +
    "border border-[var(--color-accent-hover)] " +
    "hover:bg-[var(--color-accent-hover)] hover:border-[var(--color-accent-press)] " +
    "active:bg-[var(--color-accent-press)]",
  secondary:
    "bg-[var(--color-surface-overlay)] text-[var(--color-text-primary)] " +
    "border border-[var(--color-border-strong)] " +
    "hover:border-[var(--color-border-focus)]",
  ghost:
    "bg-transparent text-[var(--color-text-secondary)] " +
    "border border-transparent " +
    "hover:bg-[var(--color-surface-overlay)] hover:text-[var(--color-text-primary)]",
  danger:
    "bg-[var(--color-deny-bg)] text-[var(--color-deny-fg)] " +
    "border border-[var(--color-deny-fg)] " +
    "hover:bg-[var(--color-deny-bg)] hover:brightness-110",
}

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
  lg: "h-10 px-4 text-sm gap-2",
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

/** Standard button. Icons go inside as children. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { variant = "secondary", size = "md", className, type = "button", ...rest },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type}
        className={cn(
          "inline-flex items-center justify-center font-medium rounded-md",
          "cursor-pointer select-none whitespace-nowrap",
          "transition-[background-color,border-color,opacity] duration-150 ease-out",
          "disabled:cursor-not-allowed disabled:opacity-55",
          VARIANTS[variant],
          SIZES[size],
          className,
        )}
        {...rest}
      />
    )
  },
)
