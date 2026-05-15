"use client";

import { useRef, useEffect } from "react";
import { prepareWithSegments, layoutWithLines } from "@chenglou/pretext";
import { useCanvas, THEME } from "./use-canvas";

export type TerminalSection = "hero" | "capabilities" | "usecases" | "comparison" | "privacy" | "day1";

interface StickyTerminalProps {
  activeSection: TerminalSection;
  locale?: string;
  activeUseCase?: number;
}

interface TerminalLine {
  text: string;
  color: string;
  prefix?: string;
  prefixColor?: string;
  delay?: number;
}

// Terminal-internal colors (dark terminal on light page)
const T = {
  fg: "#E2E8F0",
  secondary: "#94A3B8",
  muted: "#64748B",
  primaryLight: "#A78BFA",
  cta: "#F43F5E",
} as const;

const USECASES_BY_LOCALE: Record<string, TerminalLine[]> = {
  en: [
    { text: "Schedule a team standup every morning at 9am", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "Done. Recurring standup created for 9:00 AM KST,", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 500 },
    { text: "Mon-Fri. Reminder 5 min before each session.", color: T.secondary, delay: 500 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "Research competitors in the AI agent space", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 1500 },
    { text: "", color: T.muted, delay: 1700 },
    { text: "Deep research initiated. Analyzing 47 companies", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 2000 },
    { text: "across 3 verticals. ETA: 12 min.", color: T.secondary, delay: 2000 },
    { text: "Report will be sent to Telegram when done.", color: T.secondary, delay: 2000 },
  ],
  ko: [
    { text: "\uB9E4\uC77C \uC544\uCE68 9\uC2DC\uC5D0 \uD300 \uC2A4\uD0E0\uB4DC\uC5C5 \uC7A1\uC544\uC918", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "\uC644\uB8CC. \uC6D4-\uAE08 \uC624\uC804 9\uC2DC KST \uBC18\uBCF5 \uC2A4\uD0E0\uB4DC\uC5C5 \uC0DD\uC131.", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 500 },
    { text: "5\uBD84 \uC804 \uB9AC\uB9C8\uC778\uB354 \uBCF4\uB0B4\uB4DC\uB9B4\uAC8C\uC694.", color: T.secondary, delay: 500 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "AI \uC5D0\uC774\uC804\uD2B8 \uACBD\uC7C1\uC0AC \uC870\uC0AC\uD574\uC918", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 1500 },
    { text: "", color: T.muted, delay: 1700 },
    { text: "\uB525\uB9AC\uC11C\uCE58 \uC2DC\uC791. 3\uAC1C \uBD84\uC57C 47\uAC1C \uAE30\uC5C5 \uBD84\uC11D \uC911.", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 2000 },
    { text: "\uC608\uC0C1 12\uBD84. \uC644\uB8CC\uB418\uBA74 \uD154\uB808\uADF8\uB7A8\uC73C\uB85C \uBCF4\uACE0\uC11C \uC804\uC1A1.", color: T.secondary, delay: 2000 },
  ],
  ja: [
    { text: "\u6BCE\u671D9\u6642\u306B\u30C1\u30FC\u30E0\u306E\u30B9\u30BF\u30F3\u30C9\u30A2\u30C3\u30D7\u3092\u8A2D\u5B9A\u3057\u3066", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "\u5B8C\u4E86\u3002\u6708\u301C\u91D1\u306E\u5348\u524D9\u6642\u306B\u30B9\u30BF\u30F3\u30C9\u30A2\u30C3\u30D7\u3092\u4F5C\u6210\u3002", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 500 },
    { text: "5\u5206\u524D\u306B\u30EA\u30DE\u30A4\u30F3\u30C0\u30FC\u3092\u9001\u308A\u307E\u3059\u3002", color: T.secondary, delay: 500 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "AI\u30A8\u30FC\u30B8\u30A7\u30F3\u30C8\u5206\u91CE\u306E\u7AF6\u5408\u8ABF\u67FB\u3092\u304A\u9858\u3044", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 1500 },
    { text: "", color: T.muted, delay: 1700 },
    { text: "\u30C7\u30A3\u30FC\u30D7\u30EA\u30B5\u30FC\u30C1\u958B\u59CB\u30023\u5206\u91CE47\u793E\u3092\u5206\u6790\u4E2D\u3002", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 2000 },
    { text: "\u63A8\u5B9A12\u5206\u3002\u5B8C\u4E86\u5F8CTelegram\u306B\u30EC\u30DD\u30FC\u30C8\u9001\u4FE1\u3002", color: T.secondary, delay: 2000 },
  ],
  zh: [
    { text: "\u6BCF\u5929\u65E9\u4E0A9\u70B9\u5B89\u6392\u56E2\u961F\u7AD9\u4F1A", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "\u5B8C\u6210\u3002\u5DF2\u521B\u5EFA\u5468\u4E00\u81F3\u5468\u4E94\u4E0A\u5348 9\u70B9\u7684\u5B9A\u671F\u7AD9\u4F1A\u3002", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 500 },
    { text: "\u4F1A\u524D5\u5206\u949F\u53D1\u9001\u63D0\u9192\u3002", color: T.secondary, delay: 500 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "\u8C03\u7814AI Agent\u9886\u57DF\u7684\u7ADE\u4E89\u5BF9\u624B", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 1500 },
    { text: "", color: T.muted, delay: 1700 },
    { text: "\u6DF1\u5EA6\u8C03\u7814\u5DF2\u542F\u52A8\u3002\u6B63\u5728\u5206\u67903\u4E2A\u5782\u76F4\u9886\u57DF47\u5BB6\u516C\u53F8\u3002", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 2000 },
    { text: "\u9884\u8BA112\u5206\u949F\u3002\u5B8C\u6210\u540E\u901A\u8FC7Telegram\u53D1\u9001\u62A5\u544A\u3002", color: T.secondary, delay: 2000 },
  ],
  es: [
    { text: "Programa una reuni\u00F3n diaria a las 9am", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "Listo. Reuni\u00F3n recurrente lun-vie a las 9:00 AM.", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 500 },
    { text: "Recordatorio 5 min antes de cada sesi\u00F3n.", color: T.secondary, delay: 500 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "Investiga competidores en el espacio de agentes IA", prefix: "> you", prefixColor: T.primaryLight, color: T.fg, delay: 1500 },
    { text: "", color: T.muted, delay: 1700 },
    { text: "Investigaci\u00F3n iniciada. Analizando 47 empresas", prefix: "> agent", prefixColor: T.cta, color: T.secondary, delay: 2000 },
    { text: "en 3 verticales. ETA: 12 min. Informe por Telegram.", color: T.secondary, delay: 2000 },
  ],
};

const COMPARISON_BY_LOCALE: Record<string, TerminalLine[]> = {
  en: [
    { text: "\u2500\u2500 chatbot (GPT/Claude chat) \u2500\u2500", color: T.muted, delay: 0 },
    { text: "memory: none (resets every chat)", color: T.secondary, delay: 200 },
    { text: "actions: wait for your prompt", color: T.secondary, delay: 400 },
    { text: "scheduling: \u2717", color: "#FF5F57", delay: 600 },
    { text: "payments: \u2717", color: "#FF5F57", delay: 800 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "\u2500\u2500 openmagi agent \u2500\u2500", color: T.primaryLight, delay: 1200 },
    { text: "memory: 3-tier persistent (daily\u2192weekly\u2192root)", color: "#28C840", delay: 1400 },
    { text: "actions: autonomous (heartbeat every 15m)", color: "#28C840", delay: 1600 },
    { text: "scheduling: cron + event-driven triggers", color: "#28C840", delay: 1800 },
    { text: "payments: USDC wallet (self-renewing)", color: "#28C840", delay: 2000 },
    { text: "skills: 18+ x402 APIs, web search, firecrawl", color: "#28C840", delay: 2200 },
  ],
  ko: [
    { text: "\u2500\u2500 \uCC57\uBD07 (GPT/Claude chat) \u2500\u2500", color: T.muted, delay: 0 },
    { text: "\uBA54\uBAA8\uB9AC: \uC5C6\uC74C (\uB9E4\uBC88 \uCD08\uAE30\uD654)", color: T.secondary, delay: 200 },
    { text: "\uD589\uB3D9: \uD504\uB86C\uD504\uD2B8 \uB300\uAE30", color: T.secondary, delay: 400 },
    { text: "\uC2A4\uCF00\uC904\uB9C1: \u2717", color: "#FF5F57", delay: 600 },
    { text: "\uACB0\uC81C: \u2717", color: "#FF5F57", delay: 800 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "\u2500\u2500 openmagi agent \u2500\u2500", color: T.primaryLight, delay: 1200 },
    { text: "\uBA54\uBAA8\uB9AC: 3\uB2E8\uACC4 \uC601\uAD6C \uC800\uC7A5 (daily\u2192weekly\u2192root)", color: "#28C840", delay: 1400 },
    { text: "\uD589\uB3D9: \uC790\uC728 \uC2E4\uD589 (heartbeat 15\uBD84)", color: "#28C840", delay: 1600 },
    { text: "\uC2A4\uCF00\uC904\uB9C1: cron + \uC774\uBCA4\uD2B8 \uD2B8\uB9AC\uAC70", color: "#28C840", delay: 1800 },
    { text: "\uACB0\uC81C: USDC \uC9C0\uAC11 (\uC790\uB3D9 \uAC31\uC2E0)", color: "#28C840", delay: 2000 },
    { text: "\uC2A4\uD0AC: 18+ x402 API, \uC6F9 \uAC80\uC0C9, firecrawl", color: "#28C840", delay: 2200 },
  ],
  ja: [
    { text: "\u2500\u2500 \u30C1\u30E3\u30C3\u30C8\u30DC\u30C3\u30C8 (GPT/Claude chat) \u2500\u2500", color: T.muted, delay: 0 },
    { text: "\u30E1\u30E2\u30EA: \u306A\u3057\uFF08\u6BCE\u56DE\u30EA\u30BB\u30C3\u30C8\uFF09", color: T.secondary, delay: 200 },
    { text: "\u884C\u52D5: \u30D7\u30ED\u30F3\u30D7\u30C8\u5F85\u3061", color: T.secondary, delay: 400 },
    { text: "\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB: \u2717", color: "#FF5F57", delay: 600 },
    { text: "\u6C7A\u6E08: \u2717", color: "#FF5F57", delay: 800 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "\u2500\u2500 openmagi agent \u2500\u2500", color: T.primaryLight, delay: 1200 },
    { text: "\u30E1\u30E2\u30EA: 3\u968E\u5C64\u6C38\u7D9A (daily\u2192weekly\u2192root)", color: "#28C840", delay: 1400 },
    { text: "\u884C\u52D5: \u81EA\u5F8B\u5B9F\u884C (heartbeat 15\u5206)", color: "#28C840", delay: 1600 },
    { text: "\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB: cron + \u30A4\u30D9\u30F3\u30C8\u30C8\u30EA\u30AC\u30FC", color: "#28C840", delay: 1800 },
    { text: "\u6C7A\u6E08: USDC\u30A6\u30A9\u30EC\u30C3\u30C8\uFF08\u81EA\u52D5\u66F4\u65B0\uFF09", color: "#28C840", delay: 2000 },
    { text: "\u30B9\u30AD\u30EB: 18+ x402 API, Web\u691C\u7D22, firecrawl", color: "#28C840", delay: 2200 },
  ],
  zh: [
    { text: "\u2500\u2500 \u804A\u5929\u673A\u5668\u4EBA (GPT/Claude chat) \u2500\u2500", color: T.muted, delay: 0 },
    { text: "\u8BB0\u5FC6: \u65E0\uFF08\u6BCF\u6B21\u91CD\u7F6E\uFF09", color: T.secondary, delay: 200 },
    { text: "\u884C\u52A8: \u7B49\u5F85\u4F60\u7684\u63D0\u793A", color: T.secondary, delay: 400 },
    { text: "\u5B9A\u65F6\u4EFB\u52A1: \u2717", color: "#FF5F57", delay: 600 },
    { text: "\u652F\u4ED8: \u2717", color: "#FF5F57", delay: 800 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "\u2500\u2500 openmagi agent \u2500\u2500", color: T.primaryLight, delay: 1200 },
    { text: "\u8BB0\u5FC6: 3\u5C42\u6301\u4E45\u5316 (daily\u2192weekly\u2192root)", color: "#28C840", delay: 1400 },
    { text: "\u884C\u52A8: \u81EA\u4E3B\u6267\u884C (heartbeat 15\u5206)", color: "#28C840", delay: 1600 },
    { text: "\u5B9A\u65F6\u4EFB\u52A1: cron + \u4E8B\u4EF6\u89E6\u53D1", color: "#28C840", delay: 1800 },
    { text: "\u652F\u4ED8: USDC\u94B1\u5305\uFF08\u81EA\u52A8\u7EED\u8D39\uFF09", color: "#28C840", delay: 2000 },
    { text: "\u6280\u80FD: 18+ x402 API, \u7F51\u9875\u641C\u7D22, firecrawl", color: "#28C840", delay: 2200 },
  ],
  es: [
    { text: "\u2500\u2500 chatbot (GPT/Claude chat) \u2500\u2500", color: T.muted, delay: 0 },
    { text: "memoria: ninguna (se reinicia cada chat)", color: T.secondary, delay: 200 },
    { text: "acciones: esperar tu prompt", color: T.secondary, delay: 400 },
    { text: "programaci\u00F3n: \u2717", color: "#FF5F57", delay: 600 },
    { text: "pagos: \u2717", color: "#FF5F57", delay: 800 },
    { text: "", color: T.muted, delay: 1000 },
    { text: "\u2500\u2500 openmagi agent \u2500\u2500", color: T.primaryLight, delay: 1200 },
    { text: "memoria: 3 niveles persistente (daily\u2192weekly\u2192root)", color: "#28C840", delay: 1400 },
    { text: "acciones: aut\u00F3nomo (heartbeat cada 15m)", color: "#28C840", delay: 1600 },
    { text: "programaci\u00F3n: cron + triggers por eventos", color: "#28C840", delay: 1800 },
    { text: "pagos: billetera USDC (auto-renovaci\u00F3n)", color: "#28C840", delay: 2000 },
    { text: "skills: 18+ x402 APIs, b\u00FAsqueda web, firecrawl", color: "#28C840", delay: 2200 },
  ],
};

// Per-use-case setup simulations (index matches tabs in home-client.tsx)
const USECASE_SETUPS: TerminalLine[][] = [
  // 0: Marketing Automation
  [
    { text: "openmagi setup --template marketing", prefix: "$", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "installing skills:", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 400 },
    { text: "  google-ads, meta-ads, ad-copywriter", color: T.secondary, delay: 600 },
    { text: "  ad-optimizer, audience-research", color: T.secondary, delay: 800 },
    { text: "  marketing-report, creative-analyzer", color: T.secondary, delay: 1000 },
    { text: "configuring heartbeat: daily 9:00 AM", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1300 },
    { text: "connecting Google Ads API...", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1600 },
    { text: "connecting Meta Business Suite...", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1900 },
    { text: "", color: T.muted, delay: 2100 },
    { text: "marketing agent ready.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2300 },
    { text: "daily campaign reports start tomorrow 9 AM.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2600 },
  ],
  // 1: Language Mastery
  [
    { text: "openmagi setup --template language-tutor", prefix: "$", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "target: JLPT N3 (Japanese)", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 400 },
    { text: "installing skills: deep-research, web-search", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 700 },
    { text: "building 6-month study roadmap...", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1000 },
    { text: "configuring heartbeat: daily 8:00 AM", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1300 },
    { text: "  \u2192 grammar quiz + vocab drill", color: T.secondary, delay: 1500 },
    { text: "  \u2192 adaptive difficulty based on mistakes", color: T.secondary, delay: 1700 },
    { text: "configuring weekly review: Sunday 10 AM", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 2000 },
    { text: "", color: T.muted, delay: 2200 },
    { text: "language tutor ready. first lesson tomorrow.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2400 },
  ],
  // 2: Work Automation
  [
    { text: "openmagi setup --template work-assistant", prefix: "$", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "installing skills: web-search, deep-research", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 400 },
    { text: "connecting Notion workspace...", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 700 },
    { text: "connecting Slack channels...", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1000 },
    { text: "configuring heartbeat: weekdays 8:30 AM", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1300 },
    { text: "  \u2192 inbox briefing + priority sort", color: T.secondary, delay: 1500 },
    { text: "configuring weekly: Friday 5 PM", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1800 },
    { text: "  \u2192 auto-compile weekly report", color: T.secondary, delay: 2000 },
    { text: "", color: T.muted, delay: 2200 },
    { text: "work assistant ready.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2400 },
  ],
  // 3: Personal Assistant
  [
    { text: "openmagi setup --template personal", prefix: "$", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "installing skills: web-search, firecrawl", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 400 },
    { text: "installing skills: restaurant, travel", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 700 },
    { text: "connecting Google Calendar...", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1000 },
    { text: "configuring heartbeat: daily 7:30 AM", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1300 },
    { text: "  \u2192 weather + schedule + reminders", color: T.secondary, delay: 1500 },
    { text: "enabling proactive mode:", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1800 },
    { text: "  \u2192 traffic alerts, price drops, deadlines", color: T.secondary, delay: 2000 },
    { text: "", color: T.muted, delay: 2200 },
    { text: "personal assistant ready.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2400 },
  ],
  // 4: Research Agent
  [
    { text: "openmagi setup --template researcher", prefix: "$", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "installing skills: deep-research, firecrawl", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 400 },
    { text: "installing skills: web-search, document-reader", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 700 },
    { text: "model: claude-opus-4.7 (high complexity)", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 1000 },
    { text: "configuring 6-phase pipeline:", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1300 },
    { text: "  SCOPE \u2192 SEARCH \u2192 FILTER \u2192 ANALYZE \u2192 SYNTHESIZE \u2192 DELIVER", color: "#28C840", delay: 1500 },
    { text: "max depth: 3 rounds, 50 sources/round", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1800 },
    { text: "", color: T.muted, delay: 2000 },
    { text: "research agent ready.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2200 },
    { text: "send a topic to start deep research.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2500 },
  ],
  // 5: Seoul Life (useCaseTab8)
  [
    { text: "openmagi setup --template seoul-life", prefix: "$", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "installing skills: restaurant, court-auction", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 400 },
    { text: "installing skills: korean-law, tax-regulation", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 700 },
    { text: "installing skills: web-search, firecrawl", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 1000 },
    { text: "region: Seoul, language: ko", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1300 },
    { text: "configuring heartbeat: daily 8:00 AM KST", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1600 },
    { text: "  \u2192 court auction alerts, restaurant deals", color: T.secondary, delay: 1800 },
    { text: "", color: T.muted, delay: 2000 },
    { text: "seoul life agent ready.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2200 },
  ],
];

function getContent(section: TerminalSection, locale: string, useCaseIdx?: number): TerminalLine[] {
  if (section === "usecases" && useCaseIdx != null && USECASE_SETUPS[useCaseIdx]) {
    return USECASE_SETUPS[useCaseIdx];
  }
  if (section === "comparison") return COMPARISON_BY_LOCALE[locale] ?? COMPARISON_BY_LOCALE.en;
  return BASE_CONTENT[section];
}

const BASE_CONTENT: Record<string, TerminalLine[]> = {
  hero: [
    { text: "openmagi agent runtime v2.4.0", color: T.muted, delay: 0 },
    { text: "", color: T.muted, delay: 100 },
    { text: "booting autonomous agent...", prefix: "$", prefixColor: T.primaryLight, color: T.fg, delay: 200 },
    { text: "loading 3-tier memory (ROOT \u2192 weekly \u2192 daily)", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 500 },
    { text: "mounting skills: web-search, firecrawl, deep-research", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 800 },
    { text: "connecting Telegram channel", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1100 },
    { text: "smart router online (8 sectors, 3 providers)", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1400 },
    { text: "heartbeat: every 15m", prefix: "  \u25cf", prefixColor: "#28C840", color: T.secondary, delay: 1700 },
    { text: "", color: T.muted, delay: 1900 },
    { text: "agent is live. awaiting first message.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2100 },
  ],
  capabilities: [
    { text: "incoming message from @kevin", prefix: "[router]", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "classifying intent... sector: KNOWLEDGE", prefix: "[router]", prefixColor: T.primaryLight, color: T.secondary, delay: 300 },
    { text: "selected: claude-sonnet-4.6 (cost: $0.003/1K)", prefix: "[router]", prefixColor: T.primaryLight, color: T.secondary, delay: 600 },
    { text: "", color: T.muted, delay: 800 },
    { text: "incoming message from @kevin", prefix: "[router]", prefixColor: T.primaryLight, color: T.fg, delay: 1200 },
    { text: "classifying intent... sector: CODE", prefix: "[router]", prefixColor: T.primaryLight, color: T.secondary, delay: 1500 },
    { text: "selected: claude-opus-4.7 (cost: $0.015/1K)", prefix: "[router]", prefixColor: T.primaryLight, color: T.secondary, delay: 1800 },
    { text: "", color: T.muted, delay: 2000 },
    { text: "incoming message from @kevin", prefix: "[router]", prefixColor: T.primaryLight, color: T.fg, delay: 2400 },
    { text: "classifying intent... sector: CASUAL", prefix: "[router]", prefixColor: T.primaryLight, color: T.secondary, delay: 2700 },
    { text: "selected: gemini-3.1-pro (cost: $0.001/1K)", prefix: "[router]", prefixColor: T.primaryLight, color: T.secondary, delay: 3000 },
    { text: "", color: T.muted, delay: 3200 },
    { text: "session cost: $0.019 | saved 68% vs opus-only", prefix: "[billing]", prefixColor: T.cta, color: "#28C840", delay: 3500 },
  ],
  privacy: [
    { text: "encrypting bot token...", prefix: "[vault]", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "algorithm: AES-256-GCM", prefix: "  \u2192", prefixColor: T.muted, color: T.secondary, delay: 300 },
    { text: "key derivation: HKDF-SHA256 + salt-v2", prefix: "  \u2192", prefixColor: T.muted, color: T.secondary, delay: 600 },
    { text: "stored: k8s Secret (encrypted at rest)", prefix: "  \u2192", prefixColor: T.muted, color: T.secondary, delay: 900 },
    { text: "", color: T.muted, delay: 1100 },
    { text: "applying NetworkPolicy...", prefix: "[k8s]", prefixColor: T.primaryLight, color: T.fg, delay: 1400 },
    { text: "deny-all ingress between pods \u2713", prefix: "  \u2192", prefixColor: "#28C840", color: "#28C840", delay: 1700 },
    { text: "egress: allowed only to API providers \u2713", prefix: "  \u2192", prefixColor: "#28C840", color: "#28C840", delay: 2000 },
    { text: "", color: T.muted, delay: 2200 },
    { text: "container: runAsNonRoot, readOnlyRootFilesystem", prefix: "[pod]", prefixColor: T.primaryLight, color: T.secondary, delay: 2500 },
    { text: "compliance: GDPR \u2713  CCPA \u2713  PIPA \u2713", prefix: "[audit]", prefixColor: "#28C840", color: "#28C840", delay: 2800 },
  ],
  day1: [
    { text: "openmagi deploy --bot \"my-agent\"", prefix: "$", prefixColor: T.primaryLight, color: T.fg, delay: 0 },
    { text: "", color: T.muted, delay: 200 },
    { text: "creating namespace openmagi-my-agent...", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 500 },
    { text: "provisioning PVC (16GB, Longhorn)...", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 900 },
    { text: "injecting skills: web-search, firecrawl, deep-research", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 1300 },
    { text: "mounting smart router (sector-based FLEX)...", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 1700 },
    { text: "applying NetworkPolicy (deny-all)...", prefix: "  \u25cf", prefixColor: T.primaryLight, color: T.secondary, delay: 2100 },
    { text: "health check: passed", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 2500 },
    { text: "", color: T.muted, delay: 2700 },
    { text: "agent deployed in 47s", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 3000 },
    { text: "Telegram connected. Awaiting first message.", prefix: "  \u2713", prefixColor: "#28C840", color: "#28C840", delay: 3300 },
  ],
};

const HEADER_H = 36;
const LINE_HEIGHT = 18;
const PADDING = 16;
const MONO_FONT = '"Geist Mono", ui-monospace, monospace';
const FONT = `400 12.5px ${MONO_FONT}`;
const PREFIX_FONT = `600 11px ${MONO_FONT}`;

export function StickyTerminal({ activeSection, locale, activeUseCase }: StickyTerminalProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const size = useCanvas(canvasRef, 520);
  const rafRef = useRef<number>(0);
  const sectionStartRef = useRef(0);
  const prevSectionRef = useRef<TerminalSection>(activeSection);
  const prevUseCaseRef = useRef<number | undefined>(activeUseCase);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width === 0) return;

    if (prevSectionRef.current !== activeSection || prevUseCaseRef.current !== activeUseCase) {
      sectionStartRef.current = 0;
      prevSectionRef.current = activeSection;
      prevUseCaseRef.current = activeUseCase;
    }

    const lines = getContent(activeSection, locale ?? "en", activeUseCase);

    function draw(time: number): void {
      const cvs = canvasRef.current;
      if (!cvs || size.width === 0) return;
      const ctx = cvs.getContext("2d");
      if (!ctx) return;

      if (!sectionStartRef.current) sectionStartRef.current = time;
      const elapsed = time - sectionStartRef.current;

      const w = size.width;
      const h = 520;

      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "rgba(15, 15, 35, 1)";
      ctx.beginPath();
      ctx.roundRect(0, 0, w, h, 10);
      ctx.fill();

      ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(0.5, 0.5, w - 1, h - 1, 10);
      ctx.stroke();

      // Header
      ctx.fillStyle = "rgba(255, 255, 255, 0.03)";
      ctx.fillRect(0, 0, w, HEADER_H);

      const dots = ["#FF5F57", "#FFBD2E", "#28C840"];
      for (let i = 0; i < dots.length; i++) {
        ctx.beginPath();
        ctx.arc(PADDING + i * 16, HEADER_H / 2, 4.5, 0, Math.PI * 2);
        ctx.fillStyle = dots[i];
        ctx.fill();
      }

      ctx.font = `500 11px ${THEME.font}`;
      ctx.fillStyle = T.muted;
      ctx.textAlign = "center";
      ctx.fillText("openmagi agent runtime", w / 2, HEADER_H / 2 + 4);
      ctx.textAlign = "left";

      // Lines
      let y = HEADER_H + PADDING + LINE_HEIGHT;

      for (const line of lines) {
        const lineDelay = line.delay ?? 0;
        const lineElapsed = elapsed - lineDelay;
        if (lineElapsed < 0) break;

        if (!line.text) {
          y += LINE_HEIGHT * 0.5;
          continue;
        }

        let x = PADDING;

        if (line.prefix) {
          ctx.font = PREFIX_FONT;
          ctx.fillStyle = line.prefixColor ?? T.muted;
          ctx.fillText(line.prefix, x, y);
          x += ctx.measureText(line.prefix).width + 6;
        }

        const charsToShow = Math.min(line.text.length, Math.floor(lineElapsed / 15));
        const displayText = line.text.slice(0, charsToShow);

        ctx.font = FONT;
        ctx.fillStyle = line.color;

        const maxW = w - x - PADDING;
        const prepared = prepareWithSegments(displayText, FONT);
        const laid = layoutWithLines(prepared, maxW, LINE_HEIGHT);

        for (const layoutLine of laid.lines) {
          if (y > h - PADDING) break;
          ctx.fillText(layoutLine.text, x, y);
          y += LINE_HEIGHT;
        }

        // Cursor
        if (charsToShow < line.text.length && laid.lines.length > 0) {
          const lastLine = laid.lines[laid.lines.length - 1];
          if (Math.sin(elapsed / 200) > 0) {
            ctx.fillStyle = T.primaryLight;
            ctx.fillRect(x + lastLine.width + 2, y - LINE_HEIGHT * 1.7, 1.5, LINE_HEIGHT * 0.9);
          }
          break;
        }
      }

      rafRef.current = requestAnimationFrame(draw);
    }

    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  }, [size, activeSection, locale, activeUseCase]);

  return (
    <div className="w-full">
      <canvas ref={canvasRef} className="w-full rounded-xl" />
    </div>
  );
}
