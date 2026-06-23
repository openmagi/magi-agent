"use client";

/**
 * NlRuleGuide — surfaces the Guided wizard's mental model next to the
 * NL textarea so users know what axes a valid policy needs, what
 * archetypes are wired today, what isn't, and what an example phrasing
 * looks like for each shape.
 *
 * Same axes as the AuthorWizard (PR-E5):
 *   1. WHEN — lifecycle event + scope
 *   2. CONDITION — what triggers the action (or "(no condition)" for
 *      unconditional fires at after-tool)
 *   3. WHAT — archetype (block / ask / audit / strip)
 *
 * The user can click an EXAMPLE chip to pre-fill the parent NL textarea
 * — fastest path from "what can I write?" to "compile a real policy".
 */

import { ChevronRight } from "lucide-react";
import React, { useState } from "react";


export interface NlRuleGuideProps {
  /** Called when the user clicks a sample phrasing. Parent stuffs the
   *  text into the NL textarea so the user can edit before compiling. */
  onPickExample: (text: string) => void;
}


interface ExampleChip {
  archetype: "block" | "ask" | "audit" | "strip";
  label: string;
  text: string;
}


const EXAMPLES: ReadonlyArray<ExampleChip> = [
  {
    archetype: "block",
    label: "Block answer on missing tests",
    text: "On coding turns, block the final answer when tests have not been run this turn.",
  },
  {
    archetype: "block",
    label: "Deny shell_exec",
    text: "Before any tool call, deny the tool named shell_exec.",
  },
  {
    archetype: "ask",
    label: "Require approval on missing fact-grounding",
    text: "On research turns, require human approval when fact-grounding does not pass.",
  },
  {
    archetype: "audit",
    label: "Audit AWS key leaks",
    text: "After fetch_url returns, audit-log the turn when the result matches the regex AKIA[0-9A-Z]{16}.",
  },
  {
    archetype: "audit",
    label: "Audit weak citations",
    text: "On research turns, audit when an LLM critic judges that the answer cites at least one source is false.",
  },
  {
    archetype: "strip",
    label: "Strip secrets from tool output",
    text: "After any tool returns, strip the result when it contains the literal string AWS_SECRET.",
  },
];


export function NlRuleGuide({
  onPickExample,
}: NlRuleGuideProps): React.ReactElement {
  const [open, setOpen] = useState(true);
  return (
    <section
      aria-label="NL authoring guide"
      className="rounded-xl border border-black/[0.08] bg-white text-xs"
    >
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left"
      >
        <ChevronRight
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-secondary transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <span className="flex-1 text-sm font-semibold text-foreground">
          What can I write?
        </span>
        <span className="text-[10px] uppercase tracking-wider text-secondary/70">
          Authoring guide
        </span>
      </button>

      {open ? (
        <div className="space-y-4 border-t border-black/[0.04] px-4 py-3">
          <p className="leading-relaxed text-secondary">
            A policy is composed of three pieces — try to mention all three so
            the compiler doesn't have to ask:
          </p>

          <Axis
            tag="WHEN"
            title="lifecycle event + scope"
            description="Where in the agent's run does this fire, and on which kind of turn?"
            yes={[
              '"before any tool call"',
              '"after fetch_url returns"',
              '"before the final answer commits"',
              '"on coding turns" / "on research turns" / "every turn"',
            ]}
            no={[
              '"when the agent declares done" (Stop) — file-hook only',
              '"on user prompt submit" — file-hook only',
            ]}
          />

          <Axis
            tag="CONDITION"
            title="what triggers the action"
            description="Pick one. The compiler picks the right backend primitive. Pre-tool URL matchers only fire for tools that perform an HTTP fetch — name-match works for any tool."
            yes={[
              'tool name match: "the tool is shell_exec"',
              'fetch domain (network tools only): "the fetch goes to example.com"',
              'evidence ref: "tests were run" / "fact-grounding passes"',
              'SHACL shape: "exitCode is 0 on every TestRun record"',
              'LLM criterion: "the answer cites at least one source"',
              'regex / literal: "the result matches AKIA[0-9A-Z]{16}"',
              'no condition (after-tool only): "audit every tool return"',
            ]}
          />

          <Axis
            tag="WHAT"
            title="action archetype"
            description="What should the policy do when the trigger fires?"
            yes={[
              '"block / deny / refuse"',
              '"require approval / ask the user"',
              '"audit / record / just log"',
              '"strip / override the result" (after-tool only)',
            ]}
          />

          <div>
            <p className="font-semibold uppercase tracking-[0.12em] text-secondary/70 text-[10px]">
              Try one of these
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex.label}
                  type="button"
                  onClick={() => onPickExample(ex.text)}
                  className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors ${ARCHETYPE_TONE[ex.archetype]} hover:opacity-80`}
                  title={ex.text}
                >
                  {ex.label}
                </button>
              ))}
            </div>
          </div>

          <p className="rounded-lg bg-amber-50/60 px-3 py-2 text-[11px] leading-relaxed text-amber-900">
            <strong>If your phrasing is ambiguous</strong> (no lifecycle, no
            scope, missing condition), the compiler returns clarifying
            questions instead of a draft. Adding even one of the three
            pieces above usually unblocks it.
          </p>
        </div>
      ) : null}
    </section>
  );
}


const ARCHETYPE_TONE: Record<ExampleChip["archetype"], string> = {
  block: "bg-red-500/10 text-red-700",
  ask: "bg-amber-500/10 text-amber-800",
  audit: "bg-blue-500/10 text-blue-700",
  strip: "bg-violet-500/10 text-violet-700",
};


function Axis({
  tag,
  title,
  description,
  yes,
  no,
}: {
  tag: string;
  title: string;
  description: string;
  yes: string[];
  no?: string[];
}): React.ReactElement {
  return (
    <div className="rounded-lg border border-black/[0.04] bg-gray-50/50 px-3 py-2.5">
      <div className="flex items-baseline gap-2">
        <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-primary">
          {tag}
        </span>
        <span className="text-xs font-semibold text-foreground">{title}</span>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-secondary">
        {description}
      </p>
      <ul className="mt-2 space-y-0.5 text-[11px] leading-relaxed">
        {yes.map((line) => (
          <li key={line} className="flex items-start gap-2">
            <span aria-hidden="true" className="select-none text-emerald-600">
              ✓
            </span>
            <span className="text-foreground">{line}</span>
          </li>
        ))}
        {no?.map((line) => (
          <li key={line} className="flex items-start gap-2">
            <span aria-hidden="true" className="select-none text-secondary/60">
              ✗
            </span>
            <span className="text-secondary/80">{line}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
