/* GENERATED FILE — DO NOT EDIT.
   Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh. */
"use client"

import { useTransition, type MouseEvent } from "react"

export interface SwitchProps {
  /** Current state. */
  checked: boolean
  /** Async action invoked on click. Receives the desired new state. */
  onToggle: (next: boolean) => Promise<void>
  /** aria-label for screen readers. Required. */
  labelOn: string
  labelOff: string
  /** Disable the switch (e.g. always-on items). */
  disabled?: boolean
}

/**
 * Toggle switch. Track 24×44, thumb 20×20 absolutely positioned with inline
 * style transform. Optimistic flip via useTransition; the action runs and is
 * expected to revalidate the surrounding surface.
 *
 * Why absolute + inline transform: earlier Tailwind translate-x-* attempts had
 * the thumb rendering mid-track in some inline-flex layouts. Pinning the thumb
 * absolutely makes the math unambiguous.
 */
export function Switch({
  checked, onToggle, labelOn, labelOff, disabled,
}: SwitchProps) {
  const [pending, startTransition] = useTransition()
  const rendered = pending ? !checked : checked
  return (
    <button
      type="button"
      role="switch"
      aria-checked={rendered}
      aria-label={rendered ? labelOn : labelOff}
      aria-busy={pending || undefined}
      disabled={disabled || pending}
      onClick={(e: MouseEvent<HTMLButtonElement>) => {
        e.stopPropagation()
        e.preventDefault()
        startTransition(async () => { await onToggle(!checked) })
      }}
      className={`relative inline-block h-6 w-11 shrink-0 rounded-full transition-colors duration-200 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/45 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${
        rendered ? "bg-[var(--color-accent)]" : "bg-gray-300"
      }`}
    >
      <span
        aria-hidden="true"
        style={{
          transform: `translateX(${rendered ? "20px" : "0"})`,
          boxShadow: "0 1px 2px rgba(15,23,42,0.18)",
        }}
        className="absolute top-0.5 left-0.5 inline-block h-5 w-5 rounded-full bg-white transition-transform duration-200 ease-out"
      />
    </button>
  )
}
