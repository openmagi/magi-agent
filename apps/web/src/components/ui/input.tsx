import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

const inputBase =
  "w-full bg-white border border-black/10 rounded-xl px-4 py-3 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors duration-200";

export function Input({ label, className = "", ...props }: InputProps) {
  return (
    <label className="block">
      {label && (
        <span className="block text-sm font-medium text-secondary mb-1.5">
          {label}
        </span>
      )}
      <input className={`${inputBase} ${className}`} {...props} />
    </label>
  );
}

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
}

export function Textarea({ label, className = "", ...props }: TextareaProps) {
  return (
    <label className="block">
      {label && (
        <span className="block text-sm font-medium text-secondary mb-1.5">
          {label}
        </span>
      )}
      <textarea className={`${inputBase} resize-none ${className}`} {...props} />
    </label>
  );
}

