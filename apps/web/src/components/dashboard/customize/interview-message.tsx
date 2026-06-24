"use client";

/**
 * InterviewMessage — PR-F-UX6 — renders one architect interview question
 * with the right answer affordance per `expects` tag.
 *
 * Each question is one of (see InterviewQuestion in customize-api.ts):
 *   - expects=evidence_ref → chip picker over catalog.evidenceMenu (F-UX5)
 *   - expects=verifier_ref → chip picker over catalog.judgmentMenu (F-UX5)
 *   - expects=field        → chip picker over runtime-fields endpoint (F-UX2)
 *                            (the parent prefetches the runtime-fields snapshot
 *                            and passes it in via `inventory`; this component
 *                            does NOT itself hit the runtime-fields endpoint
 *                            because the tuple it needs depends on the answers
 *                            given to prior questions)
 *   - expects=tool_name    → tool catalog dropdown (F-UX3)
 *   - expects=lifecycle    → enum radio chips
 *   - expects=scope        → enum radio chips
 *   - expects=value        → text input
 *   - expects=freeform     → text input
 *
 * The component is intentionally state-free above the input: the parent owns
 * the answer string and the multi-turn history. `onAnswer` is invoked when
 * the user picks/types.
 */

import React, { useState } from "react";

import type {
  ArchitectExpects,
  InterviewQuestion,
} from "@/lib/customize-api";


// ---------------------------------------------------------------------------
// Static enum vocabularies for lifecycle / scope (no backend fetch needed).
// ---------------------------------------------------------------------------


const LIFECYCLE_OPTIONS: readonly string[] = [
  "pre_final",
  "before_tool_use",
  "after_tool_use",
  "spawn",
  "on_user_prompt_submit",
  "on_subagent_stop",
];


const SCOPE_OPTIONS: readonly string[] = [
  "always",
  "coding",
  "research",
  "delivery",
  "memory",
  "task",
];


// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------


export interface InterviewMessageProps {
  question: InterviewQuestion;
  /** Called when the user picks an inventory value or submits a freeform
   *  answer. The parent appends `{role:"user", content: answer}` to the
   *  priorTurns and re-issues compileRule with `mode:"interview"`. */
  onAnswer: (answer: string) => void;
  /** Disabled while a compile request is in flight. */
  busy?: boolean;
  /** Optional supplementary inventory hints the parent has prefetched
   *  (e.g. tool names from catalog, evidence menu, judgment menu, runtime
   *  fields). When `question.inventory` is absent the parent may pass the
   *  catalog-derived list here so the chip picker has options to render. */
  inventoryFallback?: readonly string[];
}


export function InterviewMessage({
  question,
  onAnswer,
  busy,
  inventoryFallback,
}: InterviewMessageProps): React.ReactElement {
  return (
    <section
      role="region"
      aria-label="Architect interview question"
      className="space-y-2 rounded-2xl border border-blue-200 bg-blue-50/50 p-3"
    >
      <p className="text-sm font-semibold text-blue-900">{question.question}</p>
      <AnswerAffordance
        expects={question.expects}
        inventory={question.inventory ?? inventoryFallback}
        onAnswer={onAnswer}
        busy={busy}
      />
    </section>
  );
}


function AnswerAffordance({
  expects,
  inventory,
  onAnswer,
  busy,
}: {
  expects: ArchitectExpects;
  inventory: readonly string[] | undefined;
  onAnswer: (answer: string) => void;
  busy: boolean | undefined;
}): React.ReactElement {
  // Lifecycle + scope are closed-vocab enums (no backend lookup needed).
  if (expects === "lifecycle") {
    return (
      <RadioChips
        ariaLabel="Lifecycle"
        options={LIFECYCLE_OPTIONS}
        onPick={onAnswer}
        busy={busy}
      />
    );
  }
  if (expects === "scope") {
    return (
      <RadioChips
        ariaLabel="Scope"
        options={SCOPE_OPTIONS}
        onPick={onAnswer}
        busy={busy}
      />
    );
  }

  // The remaining chip-picker tags reuse the same chip-picker primitive but
  // source their inventory from the parent (catalog snapshot).
  if (
    expects === "evidence_ref"
      || expects === "verifier_ref"
      || expects === "field"
      || expects === "tool_name"
  ) {
    if (inventory && inventory.length > 0) {
      return (
        <ChipPicker
          ariaLabel={chipAriaLabel(expects)}
          options={inventory}
          onPick={onAnswer}
          busy={busy}
        />
      );
    }
    // Fall through to a freeform input — empty catalog should not dead-end
    // the architect; the operator can still type the value verbatim.
    return (
      <FreeformInput
        placeholder={`Type a ${chipAriaLabel(expects).toLowerCase()}`}
        onAnswer={onAnswer}
        busy={busy}
      />
    );
  }

  // expects === "value" || expects === "freeform"
  return (
    <FreeformInput
      placeholder="Type your answer"
      onAnswer={onAnswer}
      busy={busy}
    />
  );
}


function chipAriaLabel(expects: ArchitectExpects): string {
  switch (expects) {
    case "evidence_ref":
      return "Evidence ref";
    case "verifier_ref":
      return "Verifier ref";
    case "field":
      return "Runtime field";
    case "tool_name":
      return "Tool name";
    default:
      return "Answer";
  }
}


function RadioChips({
  ariaLabel,
  options,
  onPick,
  busy,
}: {
  ariaLabel: string;
  options: readonly string[];
  onPick: (value: string) => void;
  busy: boolean | undefined;
}): React.ReactElement {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="flex flex-wrap gap-1.5"
    >
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          role="radio"
          aria-checked={false}
          disabled={busy}
          onClick={() => onPick(opt)}
          className="inline-flex items-center rounded-full border border-blue-300 bg-white px-2.5 py-0.5 text-[11px] font-medium text-blue-900 shadow-sm hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {opt}
        </button>
      ))}
    </div>
  );
}


function ChipPicker({
  ariaLabel,
  options,
  onPick,
  busy,
}: {
  ariaLabel: string;
  options: readonly string[];
  onPick: (value: string) => void;
  busy: boolean | undefined;
}): React.ReactElement {
  return (
    <div
      role="listbox"
      aria-label={ariaLabel}
      className="flex flex-wrap gap-1.5"
    >
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          role="option"
          aria-selected={false}
          disabled={busy}
          onClick={() => onPick(opt)}
          className="inline-flex items-center rounded-full border border-black/[0.08] bg-white px-2.5 py-0.5 font-mono text-[11px] text-foreground shadow-sm hover:bg-black/[0.03] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {opt}
        </button>
      ))}
    </div>
  );
}


function FreeformInput({
  placeholder,
  onAnswer,
  busy,
}: {
  placeholder: string;
  onAnswer: (value: string) => void;
  busy: boolean | undefined;
}): React.ReactElement {
  const [text, setText] = useState("");
  const submit = (): void => {
    const v = text.trim();
    if (!v) return;
    onAnswer(v);
    setText("");
  };
  return (
    <div className="flex items-center gap-2">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
        disabled={busy}
        placeholder={placeholder}
        aria-label="Answer"
        className="flex-1 rounded-lg border border-black/[0.10] bg-white px-2 py-1 text-xs text-foreground shadow-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
      />
      <button
        type="button"
        disabled={busy || !text.trim()}
        onClick={submit}
        className="inline-flex items-center rounded-lg bg-primary px-2.5 py-1 text-[11px] font-semibold text-white shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Send
      </button>
    </div>
  );
}
