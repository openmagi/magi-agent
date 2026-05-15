export const PUBLIC_BRAND = {
  name: "Open Magi",
  legacyName: "Clawy",
  domain: "openmagi.ai",
  siteUrl: process.env.NEXT_PUBLIC_APP_URL || "https://openmagi.ai",
  sourceUrl: "https://github.com/openmagi/magi-agent",
  supportEmail: "support@openmagi.ai",
  tagline: "The programmable agent that complies with your rules",
  description:
    "Stop praying your agent follows the prompts. Enforce them.",
} as const;
