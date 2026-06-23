import { Badge as DsBadge, type BadgeVariant as DsVariant } from "./_ds/Badge";

// Historical variant names; standard ones route through the canonical Badge
// (status palette shared with cp). Landing-only `gradient` keeps its styling.
interface BadgeProps {
  children: React.ReactNode;
  variant?: "default" | "success" | "warning" | "error" | "gradient";
  className?: string;
}

const TO_DS: Record<Exclude<BadgeProps["variant"], "gradient" | undefined>, DsVariant> = {
  default: "default",
  success: "ok",
  warning: "review",
  error: "deny",
};

const GRADIENT_CLASS =
  "inline-flex items-center px-2.5 py-0.5 text-xs font-medium rounded-full border " +
  "bg-gradient-to-r from-[var(--color-accent)]/10 to-[var(--color-accent)]/10 " +
  "text-[var(--color-accent-light)] border-[var(--color-accent)]/20";

export function Badge({
  children,
  variant = "default",
  className = "",
}: BadgeProps) {
  if (variant === "gradient") {
    return <span className={`${GRADIENT_CLASS} ${className}`.trim()}>{children}</span>;
  }
  return (
    <DsBadge variant={TO_DS[variant]} className={className}>
      {children}
    </DsBadge>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { variant: BadgeProps["variant"]; label: string }> = {
    active: { variant: "success", label: "Active" },
    pending_telegram: { variant: "warning", label: "Waiting for Telegram" },
    provisioning: { variant: "warning", label: "Provisioning" },
    stopped: { variant: "warning", label: "Trial Ended" },
    error: { variant: "error", label: "Error" },
    deleted: { variant: "default", label: "Deleted" },
  };

  const { variant, label } = config[status] ?? { variant: "default" as const, label: status };

  return <Badge variant={variant}>{label}</Badge>;
}
