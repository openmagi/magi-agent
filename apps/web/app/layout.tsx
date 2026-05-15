import type { Metadata, Viewport } from "next";
import { Plus_Jakarta_Sans, Geist_Mono } from "next/font/google";
import { I18nProvider } from "@/lib/i18n";
import { PUBLIC_BRAND } from "@/lib/brand";
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
    "open source AI agent",
    "work agent",
    "magi-agent",
    "Claude",
    "GPT",
    "Gemini",
    "self-hosted AI agent",
    "workflow automation",
  ],
  authors: [{ name: PUBLIC_BRAND.name }],
  creator: PUBLIC_BRAND.name,
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${plusJakarta.variable} ${geistMono.variable} antialiased`}
      >
        <I18nProvider>
          {children}
        </I18nProvider>
      </body>
    </html>
  );
}
