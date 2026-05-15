import { useState, useRef, useEffect, type ReactNode } from "react";
import {
  ChevronDown,
  type LucideIcon,
} from "lucide-react";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export type RuntimeCheckStatus =
  | "not_checked"
  | "checking"
  | "active"
  | "unavailable";

export type JsonRecord = Record<string, unknown>;

export type AppRoute =
  | "chat"
  | "overview"
  | "settings"
  | "usage"
  | "skills"
  | "converter"
  | "workspace"
  | "knowledge"
  | "memory";

export type DashboardRoute = Exclude<AppRoute, "chat">;

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

export function asArray(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is JsonRecord => !!item && typeof item === "object",
      )
    : [];
}

export function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

export function runtimeItemCount(
  snapshot: JsonRecord | null,
  key: string,
): number {
  const section = asRecord(snapshot?.[key]);
  const directCount = asNumber(section.count, Number.NaN);
  if (Number.isFinite(directCount)) return directCount;
  const loadedCount = asNumber(section.loadedCount, Number.NaN);
  if (Number.isFinite(loadedCount)) return loadedCount;
  return asArray(section.items).length;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 102.4) / 10} KB`;
  return `${Math.round(bytes / 1024 / 102.4) / 10} MB`;
}

export function runtimeStatusLabel(status: RuntimeCheckStatus): string {
  if (status === "active") return "active";
  if (status === "checking") return "checking";
  if (status === "unavailable") return "offline";
  return "not checked";
}

/* ------------------------------------------------------------------ */
/*  DashboardPageHeader                                                */
/* ------------------------------------------------------------------ */

export function DashboardPageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow && (
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            {eyebrow}
          </div>
        )}
        <h1 className="text-2xl font-bold leading-tight text-foreground">
          {title}
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-secondary">
          {description}
        </p>
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  DashboardCard                                                      */
/* ------------------------------------------------------------------ */

export function DashboardCard({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="glass rounded-2xl p-6 shadow-none">
      {(title || action) && (
        <div className="mb-4 flex min-h-9 items-center justify-between gap-3">
          {title ? (
            <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          ) : (
            <span />
          )}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  MetricTile                                                         */
/* ------------------------------------------------------------------ */

export function MetricTile({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string | number;
  icon?: LucideIcon;
}) {
  return (
    <div className="rounded-xl border border-black/[0.04] bg-black/[0.025] px-4 py-3">
      <div className="flex items-center gap-2">
        {Icon && (
          <Icon className="h-3.5 w-3.5 text-secondary/50" strokeWidth={2} />
        )}
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-secondary/70">
          {label}
        </div>
      </div>
      <div className="mt-1 text-2xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  StatusPill                                                         */
/* ------------------------------------------------------------------ */

export function StatusPill({
  status,
  children,
}: {
  status: RuntimeCheckStatus | "ok" | "muted" | "warning";
  children: ReactNode;
}) {
  const tones = {
    active: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700",
    checking: "border-amber-500/20 bg-amber-500/10 text-amber-700",
    unavailable: "border-red-500/20 bg-red-500/10 text-red-600",
    not_checked: "border-black/10 bg-gray-100 text-secondary",
    ok: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700",
    muted: "border-black/10 bg-gray-100 text-secondary",
    warning: "border-amber-500/20 bg-amber-500/10 text-amber-700",
  } satisfies Record<RuntimeCheckStatus | "ok" | "muted" | "warning", string>;
  return (
    <span
      className={`inline-flex min-h-7 items-center rounded-full border px-2.5 text-xs font-semibold ${tones[status]}`}
    >
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  EmptyState                                                         */
/* ------------------------------------------------------------------ */

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-black/[0.10] bg-gray-50/70 px-4 py-8 text-center text-sm leading-6 text-secondary">
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ButtonLike                                                         */
/* ------------------------------------------------------------------ */

export function ButtonLike({
  children,
  variant = "primary",
  disabled,
  onClick,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
  className?: string;
}) {
  const variants = {
    primary:
      "bg-primary text-white hover:bg-primary-light shadow-[0_8px_18px_rgba(124,58,237,0.18)]",
    secondary:
      "border border-black/10 bg-white text-foreground hover:border-primary/35 hover:bg-gray-50",
    ghost:
      "bg-transparent text-secondary hover:bg-black/[0.04] hover:text-foreground",
    danger:
      "border border-red-500/20 bg-red-500/10 text-red-500 hover:bg-red-500/15",
  };
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex min-h-[44px] cursor-pointer items-center justify-center rounded-xl px-5 py-2.5 text-sm font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:pointer-events-none disabled:opacity-40 ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  SettingsInput                                                      */
/* ------------------------------------------------------------------ */

export function SettingsInput({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-secondary mb-1.5">
        {label}
      </span>
      <input
        value={value}
        type={type}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="w-full bg-white border border-black/10 rounded-xl px-4 py-3 text-sm font-medium text-foreground outline-none transition-colors duration-200 placeholder:text-secondary/45 focus:border-primary/45 focus:ring-4 focus:ring-primary/10"
      />
    </label>
  );
}

/* ------------------------------------------------------------------ */
/*  SettingsDropdown                                                   */
/* ------------------------------------------------------------------ */

export function SettingsDropdown({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const selected = options.find((o) => o.value === value);

  return (
    <div ref={ref} className="relative block">
      {label && (
        <span className="block text-sm font-medium text-secondary mb-1.5">
          {label}
        </span>
      )}
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full bg-white border border-black/10 rounded-xl px-4 py-3 text-left text-foreground focus:outline-none focus:border-primary/45 focus:ring-4 focus:ring-primary/10 transition-colors duration-200 flex items-center justify-between"
      >
        <span className="truncate text-sm font-medium">{selected?.label ?? value}</span>
        <svg
          className={`w-4 h-4 text-gray-400 shrink-0 ml-2 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="absolute z-50 mt-1.5 w-full bg-white/95 backdrop-blur-xl rounded-xl shadow-lg border border-black/[0.08] py-1 max-h-60 overflow-y-auto">
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
              className={`w-full text-left px-4 py-2.5 text-sm transition-colors duration-150 flex items-center gap-2.5 ${
                opt.value === value
                  ? "text-foreground font-medium bg-black/[0.03]"
                  : "text-secondary hover:bg-black/[0.04] hover:text-foreground"
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  opt.value === value ? "bg-primary" : "bg-transparent"
                }`}
              />
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  CollapsibleCard                                                    */
/* ------------------------------------------------------------------ */

export function CollapsibleCard({
  title,
  subtitle,
  children,
  defaultOpen = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <DashboardCard title="" action={null}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="-m-6 flex min-h-[64px] w-[calc(100%+3rem)] cursor-pointer items-center justify-between rounded-2xl p-6 text-left transition-colors hover:bg-black/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
      >
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground">{title}</div>
          {subtitle && (
            <div className="mt-1 text-xs text-secondary">{subtitle}</div>
          )}
        </div>
        <ChevronDown
          className={`h-4 w-4 text-secondary transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="mt-6 border-t border-black/[0.06] pt-6">{children}</div>
      )}
    </DashboardCard>
  );
}
