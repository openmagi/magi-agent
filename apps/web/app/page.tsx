"use client";

import dynamic from "next/dynamic";

/**
 * Single-page entry point. The server handles SPA routing by serving
 * this page for all /app/* and /dashboard/* paths.
 *
 * The App component reads window.location.pathname to determine
 * which view (chat, dashboard section) to render, and uses
 * window.history.pushState for client-side navigation.
 *
 * We use dynamic import with ssr:false because App accesses
 * window/localStorage at module scope.
 */
const App = dynamic(() => import("@/App").then((mod) => mod.App), {
  ssr: false,
});

export default function RootPage() {
  return <App />;
}
