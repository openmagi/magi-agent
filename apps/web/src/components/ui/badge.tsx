interface BadgeProps {
  children: React.ReactNode;
  variant?: "default" | "success" | "warning" | "error" | "gradient";
  className?: string;
}

const variants = {
  default: "bg-black/[0.04] text-secondary border-black/[0.08]",
  success: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  warning: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  error: "bg-red-500/10 text-red-400 border-red-500/20",
  gradient:
    "bg-gradient-to-r from-primary/10 to-cta/10 text-primary-light border-primary/20",
};

export function Badge({
  children,
  variant = "default",
  className = "",
}: BadgeProps) {
  return (
    <span
      className={`
        inline-flex items-center px-2.5 py-0.5 text-xs font-medium rounded-full border
        ${variants[variant]}
        ${className}
      `}
    >
      {children}
    </span>
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
