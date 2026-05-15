"use client";

import Image from "next/image";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/ui/glass-card";
import { NavBar } from "@/components/nav-bar";
import { useI18n } from "@/lib/i18n";

type ProductIntroCopy = {
  badge: string;
  title: string;
  subtitle: string;
  primaryCta: string;
  secondaryCta: string;
  problemTitle: string;
  problemBody: string;
  repeatedWork: readonly { before: string; after: string }[];
  exampleBadge: string;
  exampleTitle: string;
  examplePrompt: string;
  exampleReadsTitle: string;
  exampleReads: readonly string[];
  exampleReturnsTitle: string;
  exampleReturns: readonly string[];
  exampleSavesTitle: string;
  exampleSaves: readonly string[];
  teamsTitle: string;
  teamsSubtitle: string;
  teams: readonly { name: string; work: string; result: string }[];
  rolloutTitle: string;
  rolloutSubtitle: string;
  rollout: readonly { label: string; title: string; body: string }[];
  closeTitle: string;
  closeBody: string;
  closeCta: string;
};

const COPY: Record<"en" | "ko", ProductIntroCopy> = {
  en: {
    badge: "Product intro",
    title: "Stop re-explaining the work your team already did.",
    subtitle:
      "Open Magi reads the documents, conversations, decisions, and follow-ups around a workflow, then produces drafts, analysis, reports, and next actions from that state.",
    primaryCta: "Try the product",
    secondaryCta: "See use cases",
    problemTitle: "Most teams do not need another blank chat box.",
    problemBody:
      "They need an agent that starts from the current state of the client, matter, project, account, or operation. The value is not one answer. It is the repeated context recovery that disappears.",
    repeatedWork: [
      {
        before: "Ask around for the latest file, decision, and owner.",
        after: "The agent retrieves the current matter or project context.",
      },
      {
        before: "Paste background into a generic AI chat.",
        after: "The agent starts with relevant documents, prior chats, decisions, and open follow-ups.",
      },
      {
        before: "Move the answer into a memo, spreadsheet, email, or deck by hand.",
        after: "The agent returns a concrete output in the format the workflow needs.",
      },
      {
        before: "Repeat the same reconstruction next week.",
        after: "The source trail, reasoning, and next actions are saved for the next task.",
      },
    ],
    exampleBadge: "Example ask",
    exampleTitle: "A normal request should produce usable work.",
    examplePrompt:
      "Prepare me for the Acme review. Start from the last open objection, include the latest files, and draft the follow-up email.",
    exampleReadsTitle: "Reads",
    exampleReads: ["Account notes", "Previous chats", "Shared files", "Open decisions"],
    exampleReturnsTitle: "Returns",
    exampleReturns: ["Briefing summary", "Risk and objection list", "Draft follow-up email"],
    exampleSavesTitle: "Saves",
    exampleSaves: ["What changed", "Why it matters", "Owner and next deadline"],
    teamsTitle: "Start where context recovery hurts most.",
    teamsSubtitle:
      "The first deployment should be narrow enough to trust, but valuable enough that the team feels the saved time immediately.",
    teams: [
      {
        name: "Finance and research",
        work: "Portfolio monitoring, thesis updates, filing review, investor briefings.",
        result: "The agent remembers why a position exists and what changed.",
      },
      {
        name: "Legal, tax, and accounting",
        work: "Matter prep, workpaper context, regulation review, client history.",
        result: "The next filing, memo, or client note starts from the whole file.",
      },
      {
        name: "Operators",
        work: "POS review, margin movement, supplier issues, weekly action plans.",
        result: "The agent connects numbers to the decisions made last week.",
      },
      {
        name: "Executives and assistants",
        work: "Meeting prep, open decisions, follow-up drafts, board deck context.",
        result: "The team stops rebuilding the briefing before every meeting.",
      },
    ],
    rolloutTitle: "Deployment is intentionally small at first.",
    rolloutSubtitle:
      "Open Magi works best when the first use case has a clear owner, recurring context, and an output the team already needs.",
    rollout: [
      {
        label: "01",
        title: "Pick one recurring workflow",
        body: "Choose a workflow where people repeatedly explain the same background before real work begins.",
      },
      {
        label: "02",
        title: "Connect the work context",
        body: "Add the documents, notes, chats, and tool outputs that decide the quality of the answer.",
      },
      {
        label: "03",
        title: "Let the agent produce and preserve",
        body: "Use the agent for a real output, then keep the reasoning and next actions available for the next turn.",
      },
    ],
    closeTitle: "The best first workflow is the one your team explains twice a week.",
    closeBody:
      "Turn that workflow into a private agent that remembers the context, creates the output, and leaves the next task in a better state.",
    closeCta: "Launch Open Magi",
  },
  ko: {
    badge: "제품 소개",
    title: "같은 설명을 반복하지 않고, 바로 업무를 이어갑니다.",
    subtitle:
      "Open Magi는 문서, 대화, 결정, 남은 일을 읽고 초안, 분석, 보고서, 후속 메일을 만듭니다. 작업이 끝나면 근거와 다음 액션도 함께 남겨 다음 일이 이어지게 합니다.",
    primaryCta: "데모 보기",
    secondaryCta: "활용 사례 보기",
    problemTitle: "빈 채팅창이 하나 더 필요한 게 아닙니다.",
    problemBody:
      "실제 업무는 이미 진행 중인 상태에서 시작합니다. 누가 무엇을 결정했는지, 어떤 자료를 봐야 하는지, 다음에 무엇을 해야 하는지가 함께 있어야 합니다. Open Magi의 가치는 답변 하나가 아니라, 매번 맥락을 다시 맞추던 시간을 줄이는 데 있습니다.",
    repeatedWork: [
      {
        before: "최신 파일과 결정, 담당자를 찾느라 사람들에게 다시 묻습니다.",
        after: "에이전트가 지금 필요한 프로젝트 맥락을 먼저 불러옵니다.",
      },
      {
        before: "AI 채팅창에 배경을 길게 붙여넣고 다시 설명합니다.",
        after: "관련 문서, 지난 대화, 결정, 남은 할 일에서 바로 시작합니다.",
      },
      {
        before: "답을 받은 뒤 메모, 메일, 스프레드시트, 덱으로 다시 옮깁니다.",
        after: "업무에 필요한 형식의 결과물을 바로 만듭니다.",
      },
      {
        before: "다음 주에 같은 맥락을 또 복원합니다.",
        after: "출처, 판단 근거, 다음 액션이 남아 다음 작업이 이어집니다.",
      },
    ],
    exampleBadge: "예시 요청",
    exampleTitle: "요청은 채팅 답변이 아니라 업무 결과물로 이어져야 합니다.",
    examplePrompt:
      "Acme 리뷰 미팅 준비해줘. 마지막으로 남은 이슈부터 확인하고, 최신 파일 반영해서 후속 메일 초안까지 만들어줘.",
    exampleReadsTitle: "불러오는 맥락",
    exampleReads: ["계정 메모", "지난 대화", "공유 파일", "열린 결정"],
    exampleReturnsTitle: "만드는 결과",
    exampleReturns: ["미팅 브리프", "리스크·반대 의견", "후속 메일 초안"],
    exampleSavesTitle: "다음에 남는 것",
    exampleSaves: ["변경 사항", "중요한 이유", "담당자·다음 마감"],
    teamsTitle: "같은 설명이 자주 반복되는 업무부터 시작합니다.",
    teamsSubtitle:
      "처음부터 넓게 붙일 필요는 없습니다. 담당자가 분명하고, 맥락이 반복되고, 팀이 이미 만들고 있는 산출물이 있는 업무가 가장 좋습니다.",
    teams: [
      {
        name: "금융·리서치",
        work: "포트폴리오 모니터링, 투자 논리 업데이트, 공시 검토, 투자자 브리핑",
        result: "포지션을 왜 보유하는지, 이번 변화가 기존 판단을 어떻게 바꾸는지 이어서 봅니다.",
      },
      {
        name: "법무·세무·회계",
        work: "사건 준비, 워크페이퍼 맥락, 규정 검토, 고객 이력 정리",
        result: "다음 서면, 메모, 고객 안내가 전체 파일의 맥락에서 시작합니다.",
      },
      {
        name: "운영팀",
        work: "POS 리뷰, 마진 변화, 거래처 이슈, 주간 액션 플랜",
        result: "숫자 변화와 지난주 결정을 한 흐름으로 봅니다.",
      },
      {
        name: "경영진·어시스턴트",
        work: "미팅 준비, 열린 결정, 후속 메일, 보드덱 맥락 정리",
        result: "회의 전 브리핑을 매번 새로 만들지 않아도 됩니다.",
      },
    ],
    rolloutTitle: "처음부터 전사 도입할 필요는 없습니다.",
    rolloutSubtitle:
      "한 팀의 반복 업무 하나에서 시작하면 됩니다. 맥락이 자주 끊기고, 결과물은 계속 필요한 업무일수록 효과가 빨리 보입니다.",
    rollout: [
      {
        label: "01",
        title: "반복되는 업무 하나를 고릅니다",
        body: "일을 시작하기 전 같은 배경 설명이 계속 필요한 업무를 고릅니다.",
      },
      {
        label: "02",
        title: "업무 맥락을 연결합니다",
        body: "결과의 품질을 좌우하는 문서, 노트, 대화, 도구 결과를 연결합니다.",
      },
      {
        label: "03",
        title: "실제 산출물을 만들고 남깁니다",
        body: "에이전트가 결과물을 만들게 하고, 근거와 다음 액션을 다음 작업에 남깁니다.",
      },
    ],
    closeTitle: "팀이 일주일에 두 번 다시 설명하는 업무부터 시작하세요.",
    closeBody:
      "그 업무를 맥락을 기억하고, 산출물을 만들고, 다음 작업을 더 앞에서 시작하게 하는 전용 에이전트로 바꿉니다.",
    closeCta: "데모 열기",
  },
};

export default function ProductIntroClient(): React.JSX.Element {
  const { locale } = useI18n();
  const copy = COPY[locale === "ko" ? "ko" : "en"];

  return (
    <div className="min-h-screen bg-background text-foreground">
      <NavBar />

      <main>
        <section className="relative isolate min-h-[78vh] overflow-hidden px-4 py-20 sm:px-6 sm:py-28">
          <Image
            src="/screenshots/chat.jpg"
            alt=""
            fill
            priority
            sizes="100vw"
            className="absolute inset-0 -z-20 h-full w-full object-cover opacity-30"
          />
          <div className="absolute inset-0 -z-10 bg-[linear-gradient(90deg,rgba(247,245,240,0.98)_0%,rgba(247,245,240,0.92)_42%,rgba(247,245,240,0.72)_100%)]" />
          <div className="mx-auto flex min-h-[58vh] max-w-6xl flex-col justify-center">
            <Badge variant="gradient" className="mb-5 w-fit">
              {copy.badge}
            </Badge>
            <h1 className="max-w-3xl text-4xl font-bold leading-[1.08] tracking-tight [text-wrap:balance] [word-break:keep-all] sm:text-5xl lg:text-6xl">
              {copy.title}
            </h1>
            <p className="mt-6 max-w-2xl text-base leading-7 text-secondary [text-wrap:pretty] [word-break:keep-all] sm:text-lg sm:leading-8">
              {copy.subtitle}
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link href="/demo">
                <Button variant="cta" size="lg">
                  {copy.primaryCta}
                </Button>
              </Link>
              <Link href="/use-cases">
                <Button variant="secondary" size="lg">
                  {copy.secondaryCta}
                </Button>
              </Link>
            </div>
          </div>
        </section>

        <section className="px-4 py-16 sm:px-6 sm:py-24">
          <div className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[0.85fr_1.15fr] lg:items-start">
            <div>
              <h2 className="text-3xl font-bold leading-tight [text-wrap:balance] [word-break:keep-all] sm:text-4xl">{copy.problemTitle}</h2>
              <p className="mt-5 text-base leading-7 text-secondary [text-wrap:pretty] [word-break:keep-all]">{copy.problemBody}</p>
            </div>
            <div className="overflow-hidden rounded-2xl border border-black/[0.08] bg-white/60">
              {copy.repeatedWork.map((item, index) => (
                <div
                  key={item.before}
                  className="grid gap-4 border-b border-black/[0.06] p-5 last:border-b-0 md:grid-cols-[2.5rem_1fr_1fr]"
                >
                  <span className="font-mono text-sm text-primary-light">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <p className="text-sm leading-6 text-secondary [word-break:keep-all]">{item.before}</p>
                  <p className="text-sm font-medium leading-6 text-foreground [word-break:keep-all]">{item.after}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="border-y border-black/[0.06] bg-black/[0.025] px-4 py-16 sm:px-6 sm:py-24">
          <div className="mx-auto max-w-6xl">
            <Badge variant="gradient" className="mb-5">
              {copy.exampleBadge}
            </Badge>
            <h2 className="max-w-3xl text-3xl font-bold leading-tight [text-wrap:balance] [word-break:keep-all] sm:text-4xl">
              {copy.exampleTitle}
            </h2>
            <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_1.2fr]">
              <GlassCard className="p-6">
                <p className="text-xl font-semibold leading-8 [text-wrap:pretty] [word-break:keep-all]">&ldquo;{copy.examplePrompt}&rdquo;</p>
              </GlassCard>
              <div className="grid gap-4 sm:grid-cols-3">
                <OutputColumn title={copy.exampleReadsTitle} items={copy.exampleReads} />
                <OutputColumn title={copy.exampleReturnsTitle} items={copy.exampleReturns} />
                <OutputColumn title={copy.exampleSavesTitle} items={copy.exampleSaves} />
              </div>
            </div>
          </div>
        </section>

        <section className="px-4 py-16 sm:px-6 sm:py-24">
          <div className="mx-auto max-w-6xl">
            <div className="mb-10 max-w-3xl">
              <h2 className="text-3xl font-bold leading-tight [text-wrap:balance] [word-break:keep-all] sm:text-4xl">{copy.teamsTitle}</h2>
              <p className="mt-4 text-base leading-7 text-secondary [text-wrap:pretty] [word-break:keep-all]">{copy.teamsSubtitle}</p>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {copy.teams.map((team) => (
                <GlassCard key={team.name} hover className="p-6">
                  <h3 className="text-xl font-semibold">{team.name}</h3>
                  <p className="mt-3 text-sm leading-6 text-secondary [word-break:keep-all]">{team.work}</p>
                  <p className="mt-4 text-sm font-medium leading-6 text-primary-light [word-break:keep-all]">
                    {team.result}
                  </p>
                </GlassCard>
              ))}
            </div>
          </div>
        </section>

        <section className="border-y border-black/[0.06] bg-white/55 px-4 py-16 sm:px-6 sm:py-24">
          <div className="mx-auto max-w-6xl">
            <div className="mb-10 max-w-3xl">
              <h2 className="text-3xl font-bold leading-tight [text-wrap:balance] [word-break:keep-all] sm:text-4xl">{copy.rolloutTitle}</h2>
              <p className="mt-4 text-base leading-7 text-secondary [text-wrap:pretty] [word-break:keep-all]">{copy.rolloutSubtitle}</p>
            </div>
            <div className="grid gap-4 lg:grid-cols-3">
              {copy.rollout.map((step) => (
                <div key={step.label} className="rounded-2xl border border-black/[0.08] bg-background p-6">
                  <span className="font-mono text-sm text-primary-light">{step.label}</span>
                  <h3 className="mt-5 text-xl font-semibold [word-break:keep-all]">{step.title}</h3>
                  <p className="mt-3 text-sm leading-6 text-secondary [word-break:keep-all]">{step.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="px-4 py-16 sm:px-6 sm:py-24">
          <div className="mx-auto max-w-3xl text-center">
            <h2 className="text-3xl font-bold leading-tight [text-wrap:balance] [word-break:keep-all] sm:text-4xl">{copy.closeTitle}</h2>
            <p className="mt-5 text-base leading-7 text-secondary [text-wrap:pretty] [word-break:keep-all]">{copy.closeBody}</p>
            <div className="mt-8">
              <Link href="/demo">
                <Button variant="cta" size="lg">
                  {copy.closeCta}
                </Button>
              </Link>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

function OutputColumn({ title, items }: { title: string; items: readonly string[] }) {
  return (
    <div className="rounded-2xl border border-black/[0.08] bg-background p-5">
      <h3 className="text-sm font-semibold text-primary-light">{title}</h3>
      <ul className="mt-4 space-y-3">
        {items.map((item) => (
          <li key={item} className="text-sm leading-6 text-secondary [word-break:keep-all]">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
