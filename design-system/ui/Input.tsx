import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes, useId } from "react"
import { cn } from "./cn"

const FIELD_BASE =
  "block w-full bg-[var(--color-surface-input)] " +
  "border border-[var(--color-border-strong)] " +
  "rounded-md text-sm text-[var(--color-text-primary)] " +
  "placeholder:text-[var(--color-text-tertiary)] " +
  "transition-colors duration-150 " +
  "focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40 " +
  "disabled:opacity-55 disabled:cursor-not-allowed"

interface FieldShellProps {
  id: string
  label?: string
  helper?: string
  error?: string
  required?: boolean
  children: React.ReactNode
}

function FieldShell({ id, label, helper, error, required, children }: FieldShellProps) {
  const helperId = helper ? `${id}-helper` : undefined
  const errorId = error ? `${id}-error` : undefined
  return (
    <div className="space-y-1">
      {label && (
        <label
          htmlFor={id}
          className="block text-xs font-medium text-[var(--color-text-secondary)]"
        >
          {label}
          {required && (
            <span aria-hidden="true" className="ml-1 text-[var(--color-deny-fg)]">*</span>
          )}
        </label>
      )}
      {children}
      {helper && !error && (
        <p id={helperId} className="text-xs text-[var(--color-text-tertiary)]">
          {helper}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-[var(--color-deny-fg)]">
          {error}
        </p>
      )}
    </div>
  )
}

// ── <Input> for text/email/number/url/password/search ──
export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  helper?: string
  error?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  function Input(
    { label, helper, error, id, className, required, type = "text", ...rest },
    ref,
  ) {
    const generatedId = useId()
    const inputId = id ?? generatedId
    const describedBy = [
      helper && !error ? `${inputId}-helper` : undefined,
      error ? `${inputId}-error` : undefined,
      rest["aria-describedby"],
    ].filter(Boolean).join(" ") || undefined
    return (
      <FieldShell id={inputId} label={label} helper={helper} error={error} required={required}>
        <input
          ref={ref}
          id={inputId}
          type={type}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn(FIELD_BASE, "h-9 px-3", className)}
          {...rest}
        />
      </FieldShell>
    )
  },
)

// ── <Textarea> ──
export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  helper?: string
  error?: string
  monospace?: boolean
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea(
    { label, helper, error, id, className, required, monospace, ...rest },
    ref,
  ) {
    const generatedId = useId()
    const inputId = id ?? generatedId
    const describedBy = [
      helper && !error ? `${inputId}-helper` : undefined,
      error ? `${inputId}-error` : undefined,
      rest["aria-describedby"],
    ].filter(Boolean).join(" ") || undefined
    return (
      <FieldShell id={inputId} label={label} helper={helper} error={error} required={required}>
        <textarea
          ref={ref}
          id={inputId}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn(
            FIELD_BASE,
            "py-2 px-3 leading-5",
            monospace && "font-mono",
            className,
          )}
          {...rest}
        />
      </FieldShell>
    )
  },
)
