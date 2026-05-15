import type { Metadata, Viewport } from "next";
import Script from "next/script";
import { Plus_Jakarta_Sans, Geist_Mono } from "next/font/google";
import { AuthProvider } from "@/lib/privy/provider";
import { PostHogProvider } from "@/lib/posthog/provider";
import { PostHogPageView } from "@/lib/posthog/page-view";
import { I18nProvider } from "@/lib/i18n";
import { PUBLIC_BRAND } from "@/lib/brand";
import { SOCIAL_IMAGE_URL } from "@/lib/social-metadata";
import { CookieConsentBanner } from "@/components/cookie-consent-banner";
import { LegacyDomainRebrandModal } from "@/components/legacy-domain-rebrand-modal";
import "./globals.css";

const plusJakarta = Plus_Jakarta_Sans({
  variable: "--font-geist-sans",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const SITE_URL = PUBLIC_BRAND.siteUrl;

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export const metadata: Metadata = {
  title: {
    default: `${PUBLIC_BRAND.name} — ${PUBLIC_BRAND.tagline}`,
    template: `%s | ${PUBLIC_BRAND.name}`,
  },
  description: PUBLIC_BRAND.description,
  metadataBase: new URL(SITE_URL),
  keywords: [
    "AI agent",
    "AI bot",
    "Telegram bot",
    "Claude",
    "Claude Code",
    "open source AI agent",
    "open source work agent",
    "Open Magi Cloud",
    "self-hosted AI agent",
    "work memory",
    "workplace memory",
    "business context",
    "workflow automation",
    "multi-agent",
    "smart routing",
    "chatbot",
    "AI assistant",
    "deploy agent",
    "no-code bot",
    "Telegram AI",
    "AGI",
    "AI Agent platform",
    "Fireworks AI",
    "Anthropic",
    "Claude Opus",
    "business automation",
    "workflow agent",
    "AI에이전트",
    "Open Magi",
  ],
  authors: [{ name: PUBLIC_BRAND.name }],
  creator: PUBLIC_BRAND.name,
  openGraph: {
    type: "website",
    locale: "en_US",
    url: SITE_URL,
    siteName: PUBLIC_BRAND.name,
    title: `${PUBLIC_BRAND.name} — ${PUBLIC_BRAND.tagline}`,
    description: PUBLIC_BRAND.description,
    images: [
      {
        url: SOCIAL_IMAGE_URL,
        width: 1200,
        height: 630,
        alt: `${PUBLIC_BRAND.name} — ${PUBLIC_BRAND.tagline}`,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: `${PUBLIC_BRAND.name} — ${PUBLIC_BRAND.tagline}`,
    description: PUBLIC_BRAND.description,
    images: [SOCIAL_IMAGE_URL],
  },
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/openmagi-app-icon.png", sizes: "1024x1024", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  manifest: "/site.webmanifest",
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true },
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Google Ads + GA4 (consent mode v2) — loaded after first paint */}
        <Script
          src="https://www.googletagmanager.com/gtag/js?id=AW-17978685462"
          strategy="afterInteractive"
        />
        <Script id="gtag-init" strategy="afterInteractive">
          {[
            `window.dataLayer=window.dataLayer||[];`,
            `function gtag(){dataLayer.push(arguments);}`,
            `var _eu=document.cookie.indexOf('clawy_geo=eu')!==-1;`,
            `if(_eu){`,
            `gtag('consent','default',{analytics_storage:'denied',ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied'});`,
            `gtag('set','url_passthrough',true);`,
            `}else{`,
            `gtag('consent','default',{analytics_storage:'granted',ad_storage:'granted',ad_user_data:'granted',ad_personalization:'granted'});`,
            `}`,
            `gtag('js',new Date());`,
            `gtag('config','AW-17978685462');`,
            `gtag('config','G-02BPR52L8L');`,
            `try{if(localStorage.getItem('clawy_cookie_consent')==='accepted'){gtag('consent','update',{analytics_storage:'granted',ad_storage:'granted',ad_user_data:'granted',ad_personalization:'granted'});}}catch(e){}`,
          ].join("")}
        </Script>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "SoftwareApplication",
              name: PUBLIC_BRAND.name,
              alternateName: [PUBLIC_BRAND.legacyName, "openmagi.ai"],
              applicationCategory: "BusinessApplication",
              applicationSubCategory: "AI Agent Platform",
              operatingSystem: "Web",
              description: PUBLIC_BRAND.description,
              url: SITE_URL,
              author: {
                "@type": "Organization",
                name: PUBLIC_BRAND.name,
                url: SITE_URL,
              },
              offers: [
                {
                  "@type": "Offer",
                  name: "BYOK Plan",
                  price: "7.99",
                  priceCurrency: "USD",
                  description: "Bring your own API key. 200 Brave searches/mo + 2GB storage included.",
                },
                {
                  "@type": "Offer",
                  name: "Pro Plan",
                  price: "14.99",
                  priceCurrency: "USD",
                  description: "No API key needed. Managed hosting plus $5/mo LLM credits billed at provider cost plus VAT only.",
                },
                {
                  "@type": "Offer",
                  name: "Pro+ Plan",
                  price: "89.99",
                  priceCurrency: "USD",
                  description: "Everything in Pro. Managed hosting plus $80/mo LLM credits, 1,000 Brave searches/mo, and priority support.",
                },
                {
                  "@type": "Offer",
                  name: "MAX Plan",
                  price: "399",
                  priceCurrency: "USD",
                  description: "Everything in Pro+. Dedicated node, up to 5 bots, and $350/mo LLM credits billed with 0% LLM markup.",
                },
                {
                  "@type": "Offer",
                  name: "FLEX Plan",
                  price: "1999",
                  priceCurrency: "USD",
                  description: "Everything in MAX. Dedicated node, up to 10 bots, and $1,900/mo LLM credits billed with 0% LLM markup.",
                },
              ],
              featureList: [
                "Open source AI work agent",
                "Self-hostable runtime",
                "Open Magi Cloud hosting",
                "Provider-neutral model routing",
                "Claude, GPT, Gemini, and local model support",
                "Persistent work memory",
                "Agentic drafting and analysis",
                "BYOK and Platform Credits modes",
                "Encrypted secrets and isolated runtimes",
                "7-day free trial",
              ],
            }),
          }}
        />
      </head>
      <body
        className={`${plusJakarta.variable} ${geistMono.variable} antialiased`}
      >
        <AuthProvider>
          <PostHogProvider>
            <PostHogPageView />
            <I18nProvider>
              {children}
              <LegacyDomainRebrandModal />
              <CookieConsentBanner />
            </I18nProvider>
          </PostHogProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
