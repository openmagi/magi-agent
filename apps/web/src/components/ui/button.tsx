import type { ButtonHTMLAttributes } from "react";
import { Button as DsButton, type ButtonVariant as DsVariant } from "./_ds/Button";

// Historical API (primary/secondary/ghost/cta) preserved. Dashboard + app
// buttons route through the canonical design-system Button so the OSS
// dashboard matches cp.openmagi.ai. The landing-only `cta` variant keeps its
// larger, glowing marketing styling (canonical accent tokens).
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "cta";
  size?: "sm" | "md" | "lg";
}

const CTA_CLASS =
  "inline-flex items-center justify-center gap-2 font-semibold cursor-pointer min-h-[44px] " +
  "rounded-xl px-7 py-3.5 text-base transition duration-200 " +
  "bg-[var(--color-accent)] text-[var(--color-text-on-accent)] hover:bg-[var(--color-accent-hover)] glow-cta " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/45 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-surface-base)] " +
  "disabled:opacity-40 disabled:pointer-events-none";

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ButtonProps) {
  if (variant === "cta") {
    return <button className={`${CTA_CLASS} ${className}`.trim()} {...props} />;
  }
  return (
    <DsButton
      variant={variant as DsVariant}
      size={size}
      className={className}
      {...props}
    />
  );
}
