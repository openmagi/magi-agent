"use client";

import { use } from "react";
import Link from "next/link";

interface CloudCliPageProps {
  params: Promise<{ botId: string }>;
}

function CodeBlock({ title, lines }: { title: string; lines: readonly string[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-gray-900 bg-gray-950">
      <div className="border-b border-white/10 px-4 py-3">
        <p className="font-mono text-xs font-medium text-white/55">{title}</p>
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-words p-4 text-sm leading-7 text-emerald-100">
        <code>{lines.join("\n")}</code>
      </pre>
    </div>
  );
}

function Section({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm sm:p-6">
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500">{eyebrow}</p>
      <h2 className="mt-2 text-lg font-semibold text-gray-950">{title}</h2>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

export default function CloudCliPage({ params }: CloudCliPageProps) {
  const { botId } = use(params);
  const setupLines = [
    `export OPEN_MAGI_BOT_ID="${botId}"`,
    'export OPEN_MAGI_CHANNEL="general"',
    'npx openmagi@latest cloud login --bot "$OPEN_MAGI_BOT_ID"',
  ];
  const cliLines = [
    'npx openmagi@latest cloud chat --bot "$OPEN_MAGI_BOT_ID" --channel "$OPEN_MAGI_CHANNEL"',
    'npx openmagi@latest cloud run --bot "$OPEN_MAGI_BOT_ID" --channel "$OPEN_MAGI_CHANNEL" "Draft a client-ready status update from the latest context."',
    'cat notes.md | npx openmagi@latest cloud run --bot "$OPEN_MAGI_BOT_ID" --channel "$OPEN_MAGI_CHANNEL" --stdin',
  ];
  const httpLines = [
    'curl -N "https://openmagi.ai/api/cli/chat/$OPEN_MAGI_BOT_ID/completions" \\',
    '  -H "Authorization: Bearer <cloud-cli-access-token>" \\',
    '  -H "Content-Type: application/json" \\',
    '  -H "Accept: text/event-stream" \\',
    '  -H "x-openmagi-channel: $OPEN_MAGI_CHANNEL" \\',
    '  --data \'{"model":"auto","stream":true,"messages":[{"role":"user","content":"Summarize the current work state."}]}\'',
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="border-b border-gray-200 pb-6">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500">
          Hosted core-agent
        </p>
        <div className="mt-3 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <h1 className="text-3xl font-bold tracking-normal text-gray-950">Cloud CLI</h1>
            <p className="mt-3 text-sm leading-6 text-gray-600 sm:text-base">
              Use the terminal as another client for this Open Magi cloud bot.
            </p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Link
              href={`/dashboard/${botId}/chat`}
              className="inline-flex min-h-11 items-center justify-center rounded-lg bg-gray-950 px-4 text-sm font-semibold text-white transition-colors hover:bg-gray-800"
            >
              Open chat
            </Link>
          </div>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
        <div className="space-y-6">
          <Section eyebrow="Setup" title="Point your terminal at this bot">
            <CodeBlock title="Terminal" lines={setupLines} />
          </Section>
          <Section eyebrow="Common commands" title="Chat, run, and pipe context">
            <CodeBlock title="Terminal" lines={cliLines} />
          </Section>
          <Section eyebrow="Protocol" title="Direct HTTP/SSE recipe">
            <CodeBlock title="curl" lines={httpLines} />
          </Section>
        </div>
        <aside className="space-y-6">
          <Section eyebrow="Identity" title="What stays shared">
            <ul className="space-y-3 text-sm leading-6 text-gray-600">
              <li>Bot id: <span className="font-mono text-gray-950">{botId}</span></li>
            </ul>
          </Section>
        </aside>
      </div>
    </div>
  );
}
