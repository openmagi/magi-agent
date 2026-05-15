"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { usePrivy } from "@privy-io/react-auth";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { NavBar } from "@/components/nav-bar";
import { useMessages } from "@/lib/i18n";
import { storeReferralCode } from "@/lib/referral/store-ref";
import { getOnboardingState, setOnboardingState } from "@/lib/onboarding/store";
import { trackCtaClick, trackPricingPlanClick } from "@/lib/analytics";
import { PUBLIC_BRAND } from "@/lib/brand";

const OnboardingModal = dynamic(
  () => import("@/components/onboarding/onboarding-modal").then((m) => m.OnboardingModal),
  { ssr: false },
);




type OpenSourcePlan = {
  name: string;
  price: string;
  badge: string;
  description: string;
  features: readonly string[];
  action: string;
  planId: string;
  href?: string;
};

type CloudPlan = {
  name: string;
  price: string;
  badge: string;
  description: string;
  credits: string;
  search: string;
  knowledgeBase: string;
  runtime: string;
  extras: readonly string[];
  planId: string;
};

const openSourcePlan: OpenSourcePlan = {
  name: "Open Source",
  price: "Free",
  badge: "Self-host",
  description: "Run the core runtime on your own infrastructure.",
  features: ["Open runtime", "Bring your own keys", "Local or server deploy"],
  action: "View source",
  planId: "open_source",
  href: PUBLIC_BRAND.sourceUrl,
} as const;

const openSourceInstallGuides = [
  {
    title: "Install For Humans",
    description: "Clone the source, install dependencies, and run the web app locally.",
    lines: [
      {
        text: "git clone https://github.com/openmagi/magi-agent.git",
        display: ["git clone https://github.com/openmagi/magi-agent.git"],
      },
      { text: "cd magi-agent" },
      { text: "npm install" },
      { text: "npm run dev" },
    ],
  },
  {
    title: "Install For Agents",
    description: "Give this prompt to Codex, Claude Code, or another coding agent.",
    lines: [
      { text: "Read AGENTS.md first." },
      {
        text: "Clone https://github.com/openmagi/magi-agent into ./magi-agent.",
        display: ["Clone https://github.com/openmagi/magi-agent", "into ./magi-agent."],
      },
      { text: "Install dependencies with npm install." },
      { text: "Run npm run dev and report the local URL." },
    ],
  },
] as const;

const cloudPlans: readonly CloudPlan[] = [
  {
    name: "Cloud Pro",
    price: "$14.99/mo",
    badge: "Hosted",
    description: "Managed hosting plus LLM credits billed at provider cost.",
    credits: "$5/mo LLM credits",
    search: "500 Brave searches/mo",
    knowledgeBase: "5GB Knowledge Base",
    runtime: "16GB bot storage",
    extras: ["0% LLM markup", "No API key required", "E2EE secrets"],
    planId: "pro",
  },
  {
    name: "Cloud Pro+",
    price: "$89.99/mo",
    badge: "More credits",
    description: "More at-cost inference budget and support for heavier work.",
    credits: "$80/mo LLM credits",
    search: "1,000 Brave searches/mo",
    knowledgeBase: "50GB Knowledge Base",
    runtime: "Priority support",
    extras: ["Everything in Pro", "0% LLM markup", "Expanded Knowledge Base"],
    planId: "pro_plus",
  },
  {
    name: "Cloud Max",
    price: "$399/mo",
    badge: "Dedicated",
    description: "Dedicated runtime capacity with larger at-cost LLM credits.",
    credits: "$350/mo LLM credits",
    search: "4,800 Brave searches/mo",
    knowledgeBase: "500GB Knowledge Base",
    runtime: "Dedicated node, up to 5 bots",
    extras: [
      "16GB RAM",
      "140GB dedicated storage",
      "QMD Vector Search",
      "0% LLM markup",
      "Discord integration",
    ],
    planId: "max",
  },
  {
    name: "Cloud Flex",
    price: "$1,999/mo",
    badge: "Frontier",
    description: "High-scale dedicated hosting with frontier LLM credit capacity.",
    credits: "$1,900/mo LLM credits",
    search: "25,000 Brave searches/mo",
    knowledgeBase: "2TB Knowledge Base",
    runtime: "Dedicated node, up to 10 bots",
    extras: ["Everything in Max", "0% LLM markup", "Discord integration", "Frontier hosted capacity"],
    planId: "flex",
  },
] as const;


const secondaryLinkClass =
  "inline-flex min-h-[44px] w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-black/10 bg-transparent px-7 py-3.5 text-base font-semibold text-[#111827] transition-colors duration-200 hover:border-[#0f766e]/40 hover:bg-black/[0.04] focus-visible:ring-2 focus-visible:ring-[#0f766e] focus-visible:ring-offset-2 sm:w-auto";

const invertedLinkClass =
  "inline-flex min-h-[44px] w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-white/30 bg-transparent px-7 py-3.5 text-base font-semibold text-white transition-colors duration-200 hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-[#5eead4] focus-visible:ring-offset-2 focus-visible:ring-offset-[#111827] sm:w-auto";

function CheckIcon() {
  return (
    <svg
      className="mt-0.5 h-4 w-4 shrink-0 text-[#0f766e]"
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
    >
      <path
        fillRule="evenodd"
        d="M16.704 5.29a1 1 0 0 1 .006 1.414l-7.98 8.05a1 1 0 0 1-1.425 0l-4.02-4.05a1 1 0 1 1 1.42-1.408l3.31 3.335 7.27-7.335a1 1 0 0 1 1.419-.006Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path
        fillRule="evenodd"
        d="M3 10a1 1 0 0 1 1-1h9.586l-3.293-3.293a1 1 0 1 1 1.414-1.414l5 5a1 1 0 0 1 0 1.414l-5 5a1 1 0 0 1-1.414-1.414L13.586 11H4a1 1 0 0 1-1-1Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

export function HomeClient() {
  const { ready, authenticated } = usePrivy();
  const router = useRouter();
  const t = useMessages();
  const l = t.landing;
  const isLoggedIn = ready && authenticated;
  const [modalOpen, setModalOpen] = useState(() => getOnboardingState().pendingDeploy === true);

  useEffect(() => {
    storeReferralCode();
  }, []);

  useEffect(() => {
    if (ready && authenticated && !modalOpen && getOnboardingState().pendingDeploy) {
      setModalOpen(true);
    }
  }, [ready, authenticated, modalOpen]);

  const handleDeployComplete = useCallback(() => {
    setModalOpen(false);
    router.push("/dashboard");
  }, [router]);

  const openCloud = useCallback(
    (source: string) => {
      trackCtaClick(source, isLoggedIn ? "dashboard" : "openmagi_cloud");
      if (isLoggedIn) {
        router.push("/dashboard");
        return;
      }
      setModalOpen(true);
    },
    [isLoggedIn, router],
  );

  return (
    <div className="min-h-screen bg-[#f7f7f4] text-[#111827]">
      <NavBar primaryCtaLabel={l.heroCtaCloud} />

      <main>
        {/* Hero — supervised vs unsupervised framing */}
        <section className="border-b border-black/10 bg-[linear-gradient(to_right,rgba(17,24,39,0.055)_1px,transparent_1px),linear-gradient(to_bottom,rgba(17,24,39,0.055)_1px,transparent_1px)] bg-[size:44px_44px]">
          <div className="mx-auto grid max-w-7xl gap-10 px-4 py-16 sm:px-6 sm:py-20 lg:grid-cols-[1.02fr_0.98fr] lg:px-8 lg:py-24">
            <div className="max-w-3xl">
              <div className="mb-7 flex flex-wrap gap-2">
                {l.heroBadges.map((label) => (
                  <span
                    key={label}
                    className="rounded-lg border border-black/10 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-[#374151]"
                  >
                    {label}
                  </span>
                ))}
              </div>

              <h1 className="max-w-4xl text-4xl font-black leading-[1.05] tracking-normal text-[#0b0f19] sm:text-5xl lg:text-[3.5rem]">
                {l.heroHeadline}
              </h1>

              <p className="mt-7 max-w-2xl text-lg leading-8 text-[#334155] sm:text-xl">
                {l.heroSubheadline}
              </p>

              <div className="mt-9 flex flex-col gap-3 sm:flex-row">
                <Button
                  variant="cta"
                  size="lg"
                  className="rounded-lg bg-[#0f766e] hover:bg-[#115e59]"
                  onClick={() => openCloud("hero")}
                >
                  {l.heroCtaCloud}
                  <ArrowIcon />
                </Button>
                <a
                  href={PUBLIC_BRAND.sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  onClick={() => trackCtaClick("hero", "view_source")}
                  className={secondaryLinkClass}
                >
                  {l.heroCtaSource}
                  <ArrowIcon />
                </a>
                <Link
                  href="/desktop"
                  onClick={() => trackCtaClick("hero", "desktop")}
                  className={secondaryLinkClass}
                >
                  {l.heroCtaDesktop}
                </Link>
              </div>

              <div className="mt-9 flex flex-wrap gap-2" aria-label="Supported model families">
                {l.heroModelBadges.map((model) => (
                  <span
                    key={model}
                    className="rounded-lg border border-black/10 bg-white px-3 py-2 text-sm font-medium text-[#334155]"
                  >
                    {model}
                  </span>
                ))}
              </div>
            </div>

            <div className="lg:pt-2">
              <div className="space-y-3">
                {/* Your rule */}
                <div className="rounded-xl border border-black/10 bg-white p-4 shadow-[0_2px_12px_rgba(0,0,0,0.06)]">
                  <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-[#0f766e]">{l.heroRuleLabel}</p>
                  <div className="mt-2.5 rounded-lg bg-[#0b0f19] px-4 py-3">
                    <p className="font-mono text-[11px] leading-5 text-white/40">
                      {"//"} hooks/compliance-gate.ts
                    </p>
                    <p className="font-mono text-[11px] leading-5 text-white/80">
                      <span className="text-[#c084fc]">const</span> verdict = <span className="text-[#c084fc]">await</span> ctx.callJudge({"{"}
                    </p>
                    <p className="font-mono text-[11px] leading-5 text-white/60">
                      &nbsp;&nbsp;prompt: <span className="text-[#86efac]">&quot;Does this contain unverified claims?&quot;</span>,
                    </p>
                    <p className="font-mono text-[11px] leading-5 text-white/60">
                      &nbsp;&nbsp;input: ctx.pendingResponse
                    </p>
                    <p className="font-mono text-[11px] leading-5 text-white/80">
                      {"}"})
                    </p>
                    <p className="font-mono text-[11px] leading-5 text-white/80">
                      <span className="text-[#c084fc]">if</span> (verdict.hasUnverifiedClaims)
                    </p>
                    <p className="font-mono text-[11px] leading-5 text-[#f87171]">
                      &nbsp;&nbsp;<span className="text-[#c084fc]">return</span> {"{"} action: <span className="text-[#86efac]">&quot;block&quot;</span> {"}"}
                    </p>
                  </div>
                </div>

                {/* Runtime enforces */}
                <div className="rounded-xl border border-black/10 bg-white p-4 shadow-[0_2px_12px_rgba(0,0,0,0.06)]">
                  <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-[#64748b]">{l.heroEnforceLabel}</p>

                  <div className="mt-3 space-y-2.5">
                    {/* Step 1: Agent tries to respond */}
                    <div className="rounded-lg border border-black/[0.06] bg-[#f9fafb] p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#64748b] text-[9px] font-black text-white">1</span>
                        <p className="text-[11px] font-semibold text-[#334155]">{l.heroStep1}</p>
                      </div>
                    </div>

                    {/* Step 2: Hook blocks */}
                    <div className="rounded-lg border border-[#c2410c]/20 bg-[#fef2f2] p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#c2410c] text-[9px] font-black text-white">2</span>
                        <p className="text-[11px] font-semibold text-[#c2410c]">{l.heroStep2}</p>
                        <span className="ml-auto rounded bg-[#fecaca] px-1.5 py-0.5 text-[9px] font-bold text-[#c2410c]">{l.heroStep2Badge}</span>
                      </div>
                    </div>

                    {/* Step 3: Agent reads and retries */}
                    <div className="rounded-lg border border-black/[0.06] bg-[#f9fafb] p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#64748b] text-[9px] font-black text-white">3</span>
                        <p className="text-[11px] font-semibold text-[#334155]">{l.heroStep3}</p>
                      </div>
                    </div>

                    {/* Step 4: Hook passes */}
                    <div className="rounded-lg border border-[#0f766e]/20 bg-[#f0fdfa] p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#0f766e] text-[9px] font-black text-white">4</span>
                        <p className="text-[11px] font-semibold text-[#0f766e]">{l.heroStep4}</p>
                        <span className="ml-auto rounded bg-[#ccfbf1] px-1.5 py-0.5 text-[9px] font-bold text-[#0f766e]">{l.heroStep4Badge}</span>
                      </div>
                    </div>

                    {/* Step 5: Delivered */}
                    <div className="rounded-lg border border-black/[0.06] bg-white p-3">
                      <div className="flex items-center gap-2">
                        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#111827] text-[9px] font-black text-white">&check;</span>
                        <p className="text-[11px] font-semibold text-[#0b0f19]">{l.heroStep5}</p>
                      </div>
                    </div>
                  </div>
                </div>

                <p className="text-center text-[11px] text-[#94a3b8]">
                  {l.heroFooter}
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Why — the 1% problem */}
        <section className="border-b border-black/10 bg-white">
          <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
            <div className="grid gap-10 lg:grid-cols-[1fr_1fr] lg:items-start">
              <div>
                <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#c2410c]">
                  {l.problemLabel}
                </p>
                <h2 className="mt-3 text-3xl font-black tracking-normal text-[#0b0f19] sm:text-4xl">
                  {l.problemTitle}
                </h2>
                <p className="mt-5 max-w-xl text-base leading-7 text-[#475569]">
                  {l.problemDesc1}
                </p>
                <p className="mt-4 max-w-xl text-sm leading-6 text-[#64748b]">
                  {l.problemDesc2}
                </p>
              </div>

              <div className="rounded-lg border border-black/10 bg-[#f9fafb] p-5 sm:p-6">
                <p className="text-xs font-bold uppercase tracking-[0.18em] text-[#64748b]">
                  {l.problemRuleLabel}
                </p>
                <div className="mt-5 space-y-3">
                  {l.problemSteps.map((s) => (
                    <div
                      key={s.step}
                      className={`flex items-start gap-3 rounded-lg border p-3.5 ${
                        s.status === "fail"
                          ? "border-[#c2410c]/30 bg-[#fef2f2]"
                          : s.status === "silent"
                            ? "border-[#f59e0b]/30 bg-[#fffbeb]"
                            : "border-black/10 bg-white"
                      }`}
                    >
                      <span
                        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-black ${
                          s.status === "fail"
                            ? "bg-[#c2410c] text-white"
                            : s.status === "silent"
                              ? "bg-[#f59e0b] text-white"
                              : "bg-[#0f766e] text-white"
                        }`}
                      >
                        {s.status === "fail" ? "!" : s.status === "silent" ? "?" : "\u2713"}
                      </span>
                      <div className="min-w-0">
                        <p className="text-sm font-bold text-[#0b0f19]">
                          Step {s.step}: {s.label}
                        </p>
                        <p
                          className={`mt-1 text-xs leading-5 ${
                            s.status === "fail"
                              ? "font-semibold text-[#c2410c]"
                              : s.status === "silent"
                                ? "font-semibold text-[#92400e]"
                                : "text-[#475569]"
                          }`}
                        >
                          {s.detail}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="mt-4 text-xs font-semibold leading-5 text-[#64748b]">
                  {l.problemFooter}
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Solution — programmable rules remove the human bottleneck */}
        <section className="border-b border-black/10 bg-[#f7f7f4]">
          <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
            <div className="grid gap-8 lg:grid-cols-[0.8fr_1.2fr] lg:items-start">
              <div>
                <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#0f766e]">
                  {l.solutionLabel}
                </p>
                <h2 className="mt-3 text-3xl font-black tracking-normal text-[#0b0f19] sm:text-4xl">
                  {l.solutionHeadline}
                </h2>
                <p className="mt-4 max-w-lg text-base leading-7 text-[#475569]">
                  {l.solutionDesc1}
                </p>
                <p className="mt-4 max-w-lg text-sm leading-6 text-[#475569]">
                  {l.solutionDesc2}
                </p>
              </div>

              <div className="overflow-hidden rounded-lg border border-black/10 bg-white shadow-sm">
                <div className="grid grid-cols-[1fr_1fr_1fr] border-b border-black/10 bg-[#f8fafc] text-xs font-bold uppercase tracking-[0.14em] text-[#64748b]">
                  <div className="px-4 py-3" />
                  <div className="border-l border-black/10 px-4 py-3">
                    <span className="block">{l.solutionColPrompt}</span>
                    <span className="mt-0.5 block text-[10px] tracking-[0.1em] font-semibold text-[#94a3b8]">{l.solutionColPromptSub}</span>
                  </div>
                  <div className="border-l-2 border-l-[#0f766e] bg-[#ecfdf5] px-4 py-3 text-[#0f766e]">
                    <span className="block">{l.solutionColProgrammable}</span>
                    <span className="mt-0.5 block text-[10px] tracking-[0.1em] font-semibold text-[#0f766e]/70">{l.solutionColProgrammableSub}</span>
                  </div>
                </div>
                <div className="divide-y divide-black/10">
                  {l.solutionRows.map((row) => (
                    <div key={row.label} className="grid grid-cols-[1fr_1fr_1fr]">
                      <div className="px-4 py-3.5">
                        <p className="text-xs font-black text-[#0b0f19]">{row.label}</p>
                      </div>
                      <div className="border-l border-black/10 px-4 py-3.5">
                        <p className="text-xs leading-5 text-[#475569]">{row.prompt}</p>
                      </div>
                      <div className="border-l-2 border-l-[#0f766e] bg-[#f0fdfa] px-4 py-3.5">
                        <p className="text-xs font-semibold leading-5 text-[#0f766e]">{row.programmable}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* 4 control layers */}
        <section id="features" className="border-b border-black/10 bg-white">
          <div className="mx-auto max-w-[88rem] px-4 py-16 sm:px-6 lg:px-8">
            <div className="max-w-2xl">
              <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#0f766e]">
                {l.controlLabel}
              </p>
              <h2 className="mt-3 text-3xl font-black tracking-normal text-[#0b0f19] sm:text-4xl">
                {l.controlHeadline}
              </h2>
              <p className="mt-4 max-w-xl text-base leading-7 text-[#475569]">
                {l.controlDesc}
              </p>
            </div>

            <div className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              {l.controlLayers.map((layer) => (
                <article
                  key={layer.label}
                  className="rounded-lg border border-black/10 bg-[#f9fafb] p-5 shadow-sm"
                >
                  <div className="flex items-center gap-2.5">
                    <span
                      className="flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-black text-white"
                      style={{ backgroundColor: layer.color }}
                    >
                      {layer.layer}
                    </span>
                    <span
                      className="text-xs font-bold uppercase tracking-[0.16em]"
                      style={{ color: layer.color }}
                    >
                      {layer.label}
                    </span>
                  </div>
                  <h3 className="mt-4 text-lg font-bold text-[#0b0f19]">{layer.title}</h3>
                  <p className="mt-3 text-sm leading-6 text-[#475569]">{layer.description}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* Open source + Cloud — moved after "why" and "how" */}
        <section id="open-source" className="border-b border-black/10">
          <div className="mx-auto grid max-w-7xl gap-8 px-4 py-16 sm:px-6 lg:grid-cols-2 lg:px-8">
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#0f766e]">
                {l.openSourceLabel}
              </p>
              <h2 className="mt-3 text-3xl font-black tracking-normal text-[#0b0f19] sm:text-4xl">
                {l.openSourceHeadline}
              </h2>
              <p className="mt-4 max-w-xl text-base leading-7 text-[#475569]">
                {l.openSourceDesc}
              </p>
              <ul className="mt-8 space-y-3">
                {l.openSourcePoints.map((point) => (
                  <li key={point} className="flex gap-3 text-sm font-medium text-[#1f2937]">
                    <CheckIcon />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#2563eb]">
                {l.cloudLabel}
              </p>
              <h2 className="mt-3 text-3xl font-black tracking-normal text-[#0b0f19] sm:text-4xl">
                {l.cloudHeadline}
              </h2>
              <p className="mt-4 max-w-xl text-base leading-7 text-[#475569]">
                {l.cloudDesc}
              </p>
              {"cloudMarkupCallout" in l && (
                <div className="mt-5 rounded-lg border border-[#0f766e]/20 bg-[#f0fdfa] px-4 py-3">
                  <p className="text-sm font-bold text-[#0f766e]">
                    {(l as Record<string, unknown>).cloudMarkupCallout as string}
                  </p>
                </div>
              )}
              <ul className="mt-8 space-y-3">
                {l.cloudPoints.map((point) => (
                  <li key={point} className="flex gap-3 text-sm font-medium text-[#1f2937]">
                    <CheckIcon />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>




        {/* Pricing */}
        <section id="pricing" className="border-b border-black/10 bg-white">
          <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
            <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-end">
              <div>
                <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#2563eb]">
                  {l.pricingLabel}
                </p>
                <h2 className="mt-3 text-3xl font-black tracking-normal text-[#0b0f19] sm:text-4xl">
                  {l.pricingHeadline}
                </h2>
              </div>
              <p className="max-w-xl text-sm leading-6 text-[#475569]">
                {l.pricingDesc}
              </p>
            </div>

            <div className="mt-10 rounded-lg border border-black/10 bg-[#f9fafb] p-5 sm:p-6">
              <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-3">
                    <h3 className="text-2xl font-black text-[#0b0f19]">
                      {openSourcePlan.name}
                    </h3>
                    <span className="rounded-lg border border-black/10 bg-white px-2.5 py-1 text-xs font-bold uppercase tracking-[0.14em] text-[#334155]">
                      {openSourcePlan.badge}
                    </span>
                  </div>
                  <p className="mt-3 max-w-2xl text-sm leading-6 text-[#475569]">
                    {openSourcePlan.description}
                  </p>
                  <div className="mt-5 flex flex-wrap items-center gap-3">
                    <p className="text-3xl font-black text-[#0b0f19]">{openSourcePlan.price}</p>
                    <ul className="flex flex-wrap gap-2">
                      {openSourcePlan.features.map((feature) => (
                        <li
                          key={feature}
                          className="rounded-lg border border-black/10 bg-white px-3 py-2 text-sm font-medium text-[#334155]"
                        >
                          {feature}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="mt-6 grid gap-3 sm:grid-cols-2" aria-label="Open source install guides">
                    {openSourceInstallGuides.map((guide) => (
                      <article
                        key={guide.title}
                        className="rounded-lg border border-black/10 bg-white p-4"
                      >
                        <h4 className="text-sm font-black text-[#0b0f19]">{guide.title}</h4>
                        <p className="mt-1 text-xs leading-5 text-[#64748b]">
                          {guide.description}
                        </p>
                        <pre className="mt-3 overflow-x-auto rounded-lg bg-[#0b0f19] p-3 text-[10px] leading-5 text-[#d1fae5] sm:text-[11px] xl:text-xs">
                          <code>
                            {guide.lines.map((line) => (
                              <span key={line.text} className="block whitespace-pre">
                                {"display" in line ? (
                                  <>
                                    <span className="sr-only">{line.text}</span>
                                    {line.display.map((displayLine) => (
                                      <span key={displayLine} aria-hidden="true" className="block">
                                        {displayLine}
                                      </span>
                                    ))}
                                  </>
                                ) : (
                                  line.text
                                )}
                              </span>
                            ))}
                          </code>
                        </pre>
                      </article>
                    ))}
                  </div>
                </div>
                <a
                  href={openSourcePlan.href}
                  target="_blank"
                  rel="noreferrer"
                  onClick={() => trackPricingPlanClick(openSourcePlan.planId)}
                  className="inline-flex min-h-[44px] w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-[#111827] bg-transparent px-7 py-3.5 text-base font-semibold text-[#111827] transition-colors duration-200 hover:border-[#0f766e]/40 hover:bg-black/[0.04] focus-visible:ring-2 focus-visible:ring-[#0f766e] focus-visible:ring-offset-2 sm:w-auto"
                >
                  {openSourcePlan.action}
                  <ArrowIcon />
                </a>
              </div>
            </div>

            <div className="mt-8">
              <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
                <div>
                  <p className="text-lg font-black text-[#0b0f19]">{l.pricingCloudLabel}</p>
                  <p className="mt-1 max-w-3xl text-sm leading-6 text-[#475569]">
                    {l.pricingCloudDesc}
                  </p>
                </div>
                <span className="w-fit rounded-lg border border-black/10 bg-[#f9fafb] px-3 py-2 text-xs font-bold uppercase tracking-[0.14em] text-[#334155]">
                  {l.pricingManagedBadge}
                </span>
              </div>

              <div className="mt-5 space-y-4" aria-label="Cloud plan tiers">
                {cloudPlans.map((plan) => (
                  <article
                    key={plan.name}
                    className="rounded-lg border border-black/10 bg-[#f9fafb] p-5 sm:p-6"
                  >
                    <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.25fr)_auto] lg:items-center">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-xl font-black text-[#0b0f19]">{plan.name}</h3>
                          <span className="rounded-lg border border-black/10 bg-white px-2.5 py-1 text-xs font-bold uppercase tracking-[0.14em] text-[#334155]">
                            {plan.badge}
                          </span>
                        </div>
                        <p className="mt-2 text-3xl font-black tabular-nums text-[#0b0f19]">
                          {plan.price}
                        </p>
                        <p className="mt-2 max-w-md text-sm leading-6 text-[#475569]">
                          {plan.description}
                        </p>
                      </div>

                      <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
                        {[
                          ["Credits", plan.credits],
                          ["Search", plan.search],
                          ["Knowledge Base", plan.knowledgeBase],
                          ["Runtime", plan.runtime],
                        ].map(([label, value]) => (
                          <div key={label} className="min-w-0 border-l border-black/10 pl-4">
                            <dt className="text-xs font-bold uppercase tracking-[0.14em] text-[#64748b]">
                              {label}
                            </dt>
                            <dd className="mt-1 break-words text-sm font-semibold text-[#1f2937]">
                              {value}
                            </dd>
                          </div>
                        ))}
                      </dl>

                      <Button
                        variant="cta"
                        size="lg"
                        className="w-full rounded-lg bg-[#0f766e] hover:bg-[#115e59] lg:w-auto"
                        onClick={() => {
                          trackPricingPlanClick(plan.planId);
                          openCloud("pricing");
                        }}
                      >
                        {l.pricingStartCloud}
                        <ArrowIcon />
                      </Button>
                    </div>

                    <div className="mt-5 flex flex-wrap gap-2 border-t border-black/10 pt-4">
                      {plan.extras.map((extra) => (
                        <span
                          key={extra}
                          className="rounded-lg bg-[#eef2f7] px-2.5 py-1 text-xs font-semibold text-[#334155]"
                        >
                          {extra}
                        </span>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section className="border-b border-black/10">
          <div className="mx-auto max-w-4xl px-4 py-16 sm:px-6 lg:px-8">
            <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#0f766e]">{l.faqLabel}</p>
            <h2 className="mt-3 text-3xl font-black tracking-normal text-[#0b0f19] sm:text-4xl">
              {l.faqHeadline}
            </h2>
            <div className="mt-8 divide-y divide-black/10 rounded-lg border border-black/10 bg-white">
              {l.faqItems.map((item) => (
                <details key={item.question} className="group">
                  <summary className="flex min-h-[64px] cursor-pointer items-center justify-between gap-4 px-5 py-4 text-left text-base font-bold text-[#111827] transition-colors hover:bg-[#f9fafb]">
                    <span>{item.question}</span>
                    <svg
                      className="h-4 w-4 shrink-0 text-[#64748b] transition-transform group-open:rotate-180"
                      viewBox="0 0 20 20"
                      fill="currentColor"
                      aria-hidden="true"
                    >
                      <path
                        fillRule="evenodd"
                        d="M5.23 7.21a.75.75 0 0 1 1.06.02L10 11.168l3.71-3.938a.75.75 0 1 1 1.08 1.04l-4.25 4.5a.75.75 0 0 1-1.08 0l-4.25-4.5a.75.75 0 0 1 .02-1.06Z"
                        clipRule="evenodd"
                      />
                    </svg>
                  </summary>
                  <p className="px-5 pb-5 text-sm leading-6 text-[#475569]">{item.answer}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* Bottom CTA */}
        <section className="bg-[#111827] text-white">
          <div className="mx-auto flex max-w-7xl flex-col gap-8 px-4 py-14 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#5eead4]">
                openmagi.ai
              </p>
              <h2 className="mt-3 max-w-2xl text-3xl font-black tracking-normal sm:text-4xl">
                {l.bottomCtaHeadline}
              </h2>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row">
              <Button
                variant="cta"
                size="lg"
                className="rounded-lg bg-[#14b8a6] hover:bg-[#0f766e]"
                onClick={() => openCloud("bottom")}
              >
                {l.heroCtaCloud}
              </Button>
              <a
                href={PUBLIC_BRAND.sourceUrl}
                target="_blank"
                rel="noreferrer"
                onClick={() => trackCtaClick("bottom", "view_source")}
                className={invertedLinkClass}
              >
                {l.heroCtaSource}
              </a>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-black/10 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-5 px-4 py-8 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <Logo />
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-[#64748b]">
            <Link href="/terms" className="hover:text-[#111827]">
              {t.landing.footerTerms}
            </Link>
            <Link href="/privacy" className="hover:text-[#111827]">
              {t.landing.footerPrivacy}
            </Link>
            <a href={`mailto:${PUBLIC_BRAND.supportEmail}`} className="hover:text-[#111827]">
              {PUBLIC_BRAND.supportEmail}
            </a>
            <span>{new Date().getFullYear()} {PUBLIC_BRAND.name}</span>
          </div>
        </div>
      </footer>

      <OnboardingModal
        open={modalOpen}
        onClose={() => {
          setModalOpen(false);
          setOnboardingState({ pendingDeploy: false });
        }}
        onDeployComplete={handleDeployComplete}
      />
    </div>
  );
}
