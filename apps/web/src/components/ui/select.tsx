"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

export interface SelectOption {
  value: string;
  label: ReactNode;
  description?: string;
  disabled?: boolean;
}

type SelectMenuPlacement = "bottom" | "top";

interface SelectProps {
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  label?: string;
  placeholder?: string;
  disabled?: boolean;
  menuPlacement?: SelectMenuPlacement;
  /** Pre-opens the menu. Used for server-rendered snapshots and tests. */
  defaultOpen?: boolean;
  /** Merged onto the trigger button so callers can tune size/shape per usage. */
  className?: string;
  /** Accessible name when no visible `label` is rendered. */
  "aria-label"?: string;
}

const triggerBase =
  "flex w-full min-w-0 cursor-pointer items-center justify-between gap-2 rounded-xl border border-black/10 bg-white px-4 py-3 text-sm font-medium text-foreground outline-none transition-colors duration-200 hover:border-black/20 focus:border-primary/45 focus:ring-4 focus:ring-primary/10 disabled:cursor-not-allowed disabled:opacity-60";

export function getSelectMenuPositionClass(placement: SelectMenuPlacement): string {
  return placement === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5";
}

export function Select({
  options,
  value,
  onChange,
  label,
  placeholder = "Select…",
  disabled = false,
  menuPlacement = "bottom",
  defaultOpen = false,
  className = "",
  "aria-label": ariaLabel,
}: SelectProps) {
  const [open, setOpen] = useState(defaultOpen);
  const selectedIndex = options.findIndex((option) => option.value === value);
  const [activeIndex, setActiveIndex] = useState(selectedIndex >= 0 ? selectedIndex : 0);
  const ref = useRef<HTMLDivElement>(null);
  const baseId = useId();

  useEffect(() => {
    if (!open) return;
    function onClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined;

  function commit(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
  }

  function moveActive(delta: number) {
    if (options.length === 0) return;
    let next = activeIndex;
    for (let i = 0; i < options.length; i += 1) {
      next = (next + delta + options.length) % options.length;
      if (!options[next]?.disabled) break;
    }
    setActiveIndex(next);
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) {
          setOpen(true);
          setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
        } else {
          moveActive(1);
        }
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!open) {
          setOpen(true);
          setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
        } else {
          moveActive(-1);
        }
        break;
      case "Home":
        if (open) {
          event.preventDefault();
          setActiveIndex(0);
        }
        break;
      case "End":
        if (open) {
          event.preventDefault();
          setActiveIndex(options.length - 1);
        }
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        if (open) commit(activeIndex);
        else setOpen(true);
        break;
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        }
        break;
      case "Tab":
        setOpen(false);
        break;
      default:
        break;
    }
  }

  const listboxId = `${baseId}-listbox`;
  const labelId = `${baseId}-label`;
  const activeOptionId = open ? `${baseId}-option-${activeIndex}` : undefined;

  const trigger = (
    <div ref={ref} className="relative">
      <button
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={activeOptionId}
        aria-labelledby={label ? labelId : undefined}
        aria-label={label ? undefined : ariaLabel}
        disabled={disabled}
        onClick={() => {
          setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
          setOpen((previous) => !previous);
        }}
        onKeyDown={onKeyDown}
        className={`${triggerBase} ${className}`}
      >
        <span className={`truncate ${selected ? "" : "text-gray-400"}`}>
          {selected ? selected.label : placeholder}
        </span>
        <svg
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden="true"
          className={`h-3.5 w-3.5 shrink-0 text-secondary transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        >
          <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <ul
          id={listboxId}
          role="listbox"
          className={`absolute left-0 z-50 min-w-full max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-black/10 bg-white/95 py-1 shadow-lg backdrop-blur-xl max-h-[60dvh] ${getSelectMenuPositionClass(menuPlacement)}`}
        >
          {options.map((option, index) => {
            const isSelected = option.value === value;
            const isActive = index === activeIndex;
            return (
              <li key={option.value} role="presentation">
                <button
                  type="button"
                  id={`${baseId}-option-${index}`}
                  role="option"
                  aria-selected={isSelected}
                  disabled={option.disabled}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => commit(index)}
                  className={`flex w-full cursor-pointer items-start gap-2 px-4 py-2.5 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                    isSelected
                      ? "font-semibold text-primary"
                      : "text-foreground/85"
                  } ${isActive ? "bg-primary/[0.06]" : "hover:bg-black/[0.04]"}`}
                >
                  <svg
                    viewBox="0 0 16 16"
                    fill="none"
                    aria-hidden="true"
                    className={`mt-0.5 h-4 w-4 shrink-0 text-primary transition-opacity ${isSelected ? "opacity-100" : "opacity-0"}`}
                  >
                    <path d="M3.5 8.5l3 3 6-6.5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{option.label}</span>
                    {option.description && (
                      <span className="mt-0.5 block truncate text-xs font-normal text-secondary">
                        {option.description}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );

  if (!label) return trigger;

  return (
    <div className="block">
      <span id={labelId} className="mb-1.5 block text-sm font-medium text-secondary">
        {label}
      </span>
      {trigger}
    </div>
  );
}
