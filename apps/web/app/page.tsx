import { Suspense } from "react";
import { HomeClient } from "./home-client";
import { PUBLIC_BRAND } from "@/lib/brand";

const faqJsonLd = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: [
    {
      "@type": "Question",
      name: `What is ${PUBLIC_BRAND.name}?`,
      acceptedAnswer: {
        "@type": "Answer",
        text: `${PUBLIC_BRAND.name} is the programmable agent that complies with your rules: define verification hooks and classifiers, and the runtime enforces them on every response.`,
      },
    },
    {
      "@type": "Question",
      name: "What is Open Magi Cloud?",
      acceptedAnswer: {
        "@type": "Answer",
        text: `${PUBLIC_BRAND.name} Cloud is the hosted version of the open agent runtime. It adds managed accounts, encrypted secrets, isolated workspaces, model credits, monitoring, and support.`,
      },
    },
    {
      "@type": "Question",
      name: "Can I self-host Open Magi?",
      acceptedAnswer: {
        "@type": "Answer",
        text: `Yes. ${PUBLIC_BRAND.name} is open source first. You can self-host the runtime, bring your own model keys, and use ${PUBLIC_BRAND.name} Cloud when you want managed hosting.`,
      },
    },
    {
      "@type": "Question",
      name: "What AI models are supported?",
      acceptedAnswer: {
        "@type": "Answer",
        text: `${PUBLIC_BRAND.name} is designed for Claude, GPT, Gemini, local models, and future providers. The work agent layer should not be tied to one model vendor.`,
      },
    },
    {
      "@type": "Question",
      name: "Do I need to operate infrastructure?",
      acceptedAnswer: {
        "@type": "Answer",
        text: `No. Developers can self-host, but ${PUBLIC_BRAND.name} Cloud handles hosting, accounts, billing, secrets, storage, monitoring, and upgrades for teams that want a managed service.`,
      },
    },
    {
      "@type": "Question",
      name: "Is my data private?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Yes. End-to-end encryption for API keys and bot tokens (AES-256-GCM), isolated Kubernetes containers per user, data stored in EU. Compliant with GDPR, CCPA, and Korea's PIPA.",
      },
    },
    {
      "@type": "Question",
      name: "How much does it cost?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "The open source runtime is free to self-host. Open Magi Cloud plans start with Pro at $14.99/mo and include managed hosting plus LLM credits billed at provider cost plus VAT only.",
      },
    },
    {
      "@type": "Question",
      name: "What can my AI agent do?",
      acceptedAnswer: {
        "@type": "Answer",
        text: `${PUBLIC_BRAND.name} can retrieve team context, draft from source material, call specialist tools, compare files and decisions, prepare briefings, monitor changes, verify outputs, and save useful reasoning or artifacts back into memory for the next task.`,
      },
    },
    {
      "@type": "Question",
      name: "Can I use my own API key?",
      acceptedAnswer: {
        "@type": "Answer",
        text: `Yes. The BYOK plan lets you connect your own Anthropic, OpenAI, or Google API key. You pay API costs directly to the provider; ${PUBLIC_BRAND.name} handles hosting, memory, skills, and everything else.`,
      },
    },
  ],
};

const howToJsonLd = {
  "@context": "https://schema.org",
  "@type": "HowTo",
  name: `How to run an open source work agent with ${PUBLIC_BRAND.name}`,
  description: "Define your own verification rules for autonomous agents, or use hosted Open Magi Cloud when you want managed infrastructure.",
  step: [
    {
      "@type": "HowToStep",
      position: 1,
      name: "Choose source or Cloud",
      text: "Self-host the open runtime when you want control, or start with Open Magi Cloud when you want managed hosting.",
    },
    {
      "@type": "HowToStep",
      position: 2,
      name: "Connect models and tools",
      text: "Bring Claude, GPT, Gemini, local models, and the tools your work agent needs to complete useful tasks.",
    },
    {
      "@type": "HowToStep",
      position: 3,
      name: "Run real work",
      text: "Ask the agent to read context, call tools, create artifacts, and preserve useful memory for the next run.",
    },
  ],
  totalTime: "PT3M",
  tool: {
    "@type": "HowToTool",
    name: "Model API key or Open Magi Cloud",
  },
  supply: {
    "@type": "HowToSupply",
    name: `${PUBLIC_BRAND.name} Cloud account`,
  },
};

export default function Home() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(howToJsonLd) }}
      />
      <Suspense>
        <HomeClient />
      </Suspense>
    </>
  );
}
