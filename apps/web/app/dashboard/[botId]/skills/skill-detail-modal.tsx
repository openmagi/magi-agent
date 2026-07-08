"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Modal } from "@/components/ui/modal";
import {
  SkillBadge,
  skillTypeLabel,
  type SkillDirectoryItem,
} from "./skills-catalog";

interface SkillDetailModalProps {
  open: boolean;
  onClose: () => void;
  skill: SkillDirectoryItem | null;
  content: string | null;
  loading: boolean;
  error: string | null;
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/60">
        {label}
      </span>
      <span className="break-all text-sm text-foreground">{value}</span>
    </div>
  );
}

export function SkillDetailModal({
  open,
  onClose,
  skill,
  content,
  loading,
  error,
}: SkillDetailModalProps): React.JSX.Element | null {
  if (!skill) return null;

  return (
    <Modal open={open} onClose={onClose} className="max-w-3xl">
      <div className="flex flex-col gap-5 p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-foreground">{skill.name}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <SkillBadge tone={skill.scriptBacked ? "green" : "primary"}>
                {skillTypeLabel(skill)}
              </SkillBadge>
              {skill.tags.map((tag) => (
                <SkillBadge key={tag}>{tag}</SkillBadge>
              ))}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 cursor-pointer rounded-lg border border-black/[0.08] bg-white px-3 py-1.5 text-sm font-semibold text-secondary transition-colors duration-200 hover:border-primary/25 hover:text-foreground"
          >
            Close
          </button>
        </div>

        <div className="grid gap-4 rounded-xl border border-black/[0.05] bg-black/[0.02] px-4 py-3 sm:grid-cols-2">
          <MetaRow label="Path" value={skill.path || "workspace skill"} />
          <MetaRow label="Source" value={skill.source || "workspace"} />
          {skill.description ? (
            <div className="sm:col-span-2">
              <MetaRow label="Description" value={skill.description} />
            </div>
          ) : null}
        </div>

        <div>
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-secondary/60">
            SKILL.md
          </div>
          <div className="max-h-[55vh] overflow-y-auto rounded-xl border border-black/[0.06] bg-white px-4 py-3">
            {loading ? (
              <p className="text-sm text-secondary">Loading SKILL.md...</p>
            ) : error ? (
              <p className="text-sm text-red-500">{error}</p>
            ) : content ? (
              <div className="prose-chat max-w-none text-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
              </div>
            ) : (
              <p className="text-sm text-secondary">No content available.</p>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
