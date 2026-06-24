/* GENERATED FILE — DO NOT EDIT.
   Source: magi-agent/design-system. Regenerate via scripts/sync-design-system.sh. */
import { cn } from "./cn"

/** One fixed, full-viewport gradient-mesh layer that sits behind everything so
 * the glass surfaces have something to refract and tint. Mount once at an app's
 * root layout. `intensity="vivid"` (landing) lifts the blob opacity; `"subtle"`
 * (dashboards) keeps it quiet so it never competes with data. The drift
 * animation is frozen by the global prefers-reduced-motion rule. */
export interface MeshBackdropProps {
  intensity?: "subtle" | "vivid"
  className?: string
}

export function MeshBackdrop({ intensity = "subtle", className }: MeshBackdropProps) {
  const a = intensity === "vivid" ? 0.38 : 0.24
  const b = intensity === "vivid" ? 0.30 : 0.18
  return (
    <div
      aria-hidden="true"
      className={cn("fixed inset-0 -z-10 overflow-hidden pointer-events-none", className)}
      style={{ background: "var(--color-surface-base)" }}
    >
      <div
        className="absolute inset-[-20%]"
        style={{
          animation: "ds-mesh-drift 28s ease-in-out infinite",
          background: [
            `radial-gradient(40% 38% at 18% 22%, color-mix(in srgb, var(--color-accent) ${a * 100}%, transparent), transparent 70%)`,
            `radial-gradient(42% 40% at 82% 14%, color-mix(in srgb, var(--color-brand, #0f766e) ${b * 100}%, transparent), transparent 70%)`,
            `radial-gradient(46% 44% at 70% 84%, color-mix(in srgb, var(--color-accent) ${b * 100}%, transparent), transparent 72%)`,
            `radial-gradient(38% 36% at 28% 78%, color-mix(in srgb, var(--color-brand, #0f766e) ${a * 0.7 * 100}%, transparent), transparent 72%)`,
          ].join(", "),
        }}
      />
    </div>
  )
}
