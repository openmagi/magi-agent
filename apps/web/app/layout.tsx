import type { Metadata } from "next";
import "../src/styles.css";

export const metadata: Metadata = {
  title: "Magi Agent",
  description: "Autonomous task runtime with agentic interaction",
  icons: {
    icon: "/app/icon.svg",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <meta name="theme-color" content="#7C3AED" />
        <link rel="manifest" href="/app/manifest.webmanifest" />
      </head>
      <body className="bg-background text-foreground font-sans">
        {children}
      </body>
    </html>
  );
}
