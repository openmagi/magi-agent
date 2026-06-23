/* GENERATED FILE — DO NOT EDIT.
   Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh. */
"use client"

import { useState } from "react"
import { Button, type ButtonProps } from "./Button"

export interface CopyButtonProps extends Omit<ButtonProps, "onClick" | "children"> {
  value: string
  label?: string
  copiedLabel?: string
}

/** Reusable copy-to-clipboard button. */
export function CopyButton({
  value, label = "Copy", copiedLabel = "Copied", ...rest
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        } catch {
          // Older browsers: fall back to document.execCommand
          const ta = document.createElement("textarea")
          ta.value = value
          ta.setAttribute("readonly", "")
          ta.style.position = "absolute"
          ta.style.left = "-9999px"
          document.body.appendChild(ta)
          ta.select()
          try { document.execCommand("copy") } finally { document.body.removeChild(ta) }
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }
      }}
      aria-live="polite"
      {...rest}
    >
      <svg
        width="14" height="14" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        aria-hidden="true"
      >
        {copied ? (
          <polyline points="20 6 9 17 4 12" />
        ) : (
          <>
            <rect x="9" y="9" width="13" height="13" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </>
        )}
      </svg>
      {copied ? copiedLabel : label}
    </Button>
  )
}
