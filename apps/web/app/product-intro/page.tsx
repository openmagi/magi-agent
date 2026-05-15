import type { Metadata } from "next";
import { SOCIAL_IMAGE_URL } from "@/lib/social-metadata";
import { PUBLIC_BRAND } from "@/lib/brand";

import ProductIntroClient from "./product-intro-client";

const SITE_URL = PUBLIC_BRAND.siteUrl;

export const metadata: Metadata = {
  title: "Product Intro — AI Agents That Carry Work Forward",
  description:
    "See how Open Magi helps teams stop re-explaining context, produce real work from documents and decisions, and preserve the reasoning for the next task.",
  openGraph: {
    title: "Product Intro — AI Agents That Carry Work Forward",
    description:
      "Open Magi reads documents, conversations, decisions, and follow-ups, then produces drafts, analysis, reports, and next actions that carry work forward.",
    url: `${SITE_URL}/product-intro`,
    type: "website",
    images: [
      {
        url: SOCIAL_IMAGE_URL,
        width: 1200,
        height: 630,
        alt: "Open Magi product introduction",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Product Intro — AI Agents That Carry Work Forward",
    description:
      "Stop re-explaining context. Deploy a private agent that reads, acts, and preserves work state.",
    images: [SOCIAL_IMAGE_URL],
  },
  alternates: {
    canonical: `${SITE_URL}/product-intro`,
  },
};

export default function ProductIntroPage(): React.JSX.Element {
  return <ProductIntroClient />;
}
