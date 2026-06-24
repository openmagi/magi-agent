import { forwardRef, type HTMLAttributes } from "react"
import { cn } from "./cn"

export type GlassTier = "clear" | "regular" | "thick"

/** Liquid-glass material. The tier IS the opacity, so dense content can be
 * "glass" and still readable: chrome uses `clear`, panels/cards `regular`,
 * body/tables `thick` (near-opaque). Backdrop blur + saturate gives the frost;
 * a top specular rim + depth shadow give the floating-glass read. Solid
 * fallbacks for reduce-transparency / no-backdrop-filter live in tokens.css. */
const TIER_BG: Record<GlassTier, string> = {
  clear:   "bg-[var(--glass-clear-bg)]",
  regular: "bg-[var(--glass-regular-bg)]",
  thick:   "bg-[var(--glass-thick-bg)]",
}

export interface GlassSurfaceProps extends HTMLAttributes<HTMLDivElement> {
  tier?: GlassTier
  /** spring hover lift + faint accent rim; use for clickable surfaces. */
  interactive?: boolean
}

export const GlassSurface = forwardRef<HTMLDivElement, GlassSurfaceProps>(
  function GlassSurface(
    { tier = "regular", interactive, className, style, children, ...rest },
    ref,
  ) {
    return (
      <div
        ref={ref}
        style={{
          backdropFilter: "blur(var(--glass-blur)) saturate(var(--glass-saturate))",
          WebkitBackdropFilter: "blur(var(--glass-blur)) saturate(var(--glass-saturate))",
          boxShadow: "var(--glass-rim), var(--glass-edge)",
          borderRadius: "var(--glass-radius)",
          ...style,
        }}
        className={cn(
          "relative border border-white/40",
          TIER_BG[tier],
          interactive &&
            "transition-[transform,border-color] duration-200 ease-out cursor-pointer " +
            "hover:scale-[1.02] active:scale-100 hover:border-[var(--glass-tint)]/30",
          className,
        )}
        {...rest}
      >
        {children}
      </div>
    )
  },
)
