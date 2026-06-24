import { forwardRef, type SelectHTMLAttributes, useId } from "react"
import { cn } from "./cn"

export interface SelectOption {
  value: string
  label: string
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> {
  label?: string
  helper?: string
  error?: string
  options: SelectOption[]
}

/** Native <select> with consistent token-driven styling. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  function Select(
    { label, helper, error, options, id, className, required, ...rest },
    ref,
  ) {
    const generatedId = useId()
    const sid = id ?? generatedId
    const describedBy = [
      helper && !error ? `${sid}-helper` : undefined,
      error ? `${sid}-error` : undefined,
      rest["aria-describedby"],
    ].filter(Boolean).join(" ") || undefined
    return (
      <div className="space-y-1">
        {label && (
          <label
            htmlFor={sid}
            className="block text-xs font-medium text-[var(--color-text-secondary)]"
          >
            {label}
            {required && (
              <span aria-hidden="true" className="ml-1 text-[var(--color-deny-fg)]">*</span>
            )}
          </label>
        )}
        <select
          ref={ref}
          id={sid}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          // explicit bg + color so Windows dark mode renders correctly
          style={{
            backgroundColor: "var(--color-surface-input)",
            color: "var(--color-text-primary)",
          }}
          className={cn(
            "block w-full h-9 px-3 text-sm rounded-md",
            "border border-[var(--color-border-strong)]",
            "transition-colors duration-150",
            "focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40",
            "disabled:opacity-55 disabled:cursor-not-allowed",
            className,
          )}
          {...rest}
        >
          {options.map(o => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {helper && !error && (
          <p id={`${sid}-helper`} className="text-xs text-[var(--color-text-tertiary)]">
            {helper}
          </p>
        )}
        {error && (
          <p id={`${sid}-error`} role="alert" className="text-xs text-[var(--color-deny-fg)]">
            {error}
          </p>
        )}
      </div>
    )
  },
)
