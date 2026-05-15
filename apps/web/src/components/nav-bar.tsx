"use client";

import { useState, useCallback, type MouseEvent } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { usePrivy } from "@privy-io/react-auth";
import { Logo } from "@/components/ui/logo";
import { Button } from "@/components/ui/button";
import { LanguageSwitcher } from "@/components/ui/language-switcher";
import { useMessages } from "@/lib/i18n";
import { trackAuthClick } from "@/lib/analytics";
import { PUBLIC_BRAND } from "@/lib/brand";

const OnboardingModal = dynamic(
  () => import("@/components/onboarding/onboarding-modal").then((m) => m.OnboardingModal),
  { ssr: false }
);

type NavBarProps = {
  primaryCtaHref?: string;
  primaryCtaLabel?: string;
  englishOnly?: boolean;
};

const ENGLISH_NAV = {
  logIn: "Log in",
  logOut: "Log out",
  dashboard: "Dashboard",
  signUp: "Get started",
  downloadDesktop: "Desktop app",
  alreadyHaveAccount: "Already have an account?",
} as const;

function StaticEnglishLanguage() {
  return (
    <span
      className="flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-secondary"
      aria-label="Language: English"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        className="h-4 w-4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
      </svg>
      <span>English</span>
    </span>
  );
}

export function NavBar({ primaryCtaHref, primaryCtaLabel, englishOnly = false }: NavBarProps = {}) {
  const { ready, authenticated, logout, login } = usePrivy();
  const router = useRouter();
  const t = useMessages();
  const nav = englishOnly ? ENGLISH_NAV : t.nav;
  const isLoggedIn = ready && authenticated;
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const ctaLabel = primaryCtaLabel ?? nav.signUp;

  const handleDesktopNavigation = useCallback((event: MouseEvent<HTMLAnchorElement>) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    window.location.assign("/desktop");
  }, []);

  const handleSignUp = useCallback(() => {
    trackAuthClick("signup", "nav");
    setOnboardingOpen(true);
  }, []);

  const handleDeployComplete = useCallback(() => {
    setOnboardingOpen(false);
    router.push("/dashboard");
  }, [router]);

  return (
    <header className="sticky top-0 z-50 border-b border-black/5 bg-background/80 backdrop-blur-xl">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3.5 flex items-center justify-between">
        <Link href="/" aria-label="Home" className="flex items-center gap-2">
          <Logo />
          <span className="text-[10px] font-bold uppercase tracking-widest text-primary-light bg-primary/10 border border-primary/20 px-1.5 py-0.5 rounded-md">Beta</span>
        </Link>

        {/* Desktop nav */}
        <div className="hidden sm:flex items-center gap-2">
          <a
            href={PUBLIC_BRAND.sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-secondary hover:text-foreground transition-colors px-3 py-1.5"
          >
            Source
          </a>
          <Link href="/docs" className="text-sm text-secondary hover:text-foreground transition-colors px-3 py-1.5">
            Docs
          </Link>
          <Link href="/why-magi" className="text-sm text-secondary hover:text-foreground transition-colors px-3 py-1.5">
            Why Magi
          </Link>
          <Link href="/blog" className="text-sm text-secondary hover:text-foreground transition-colors px-3 py-1.5">
            Blog
          </Link>
          <Link
            href="/desktop"
            className="text-sm text-secondary hover:text-foreground transition-colors px-3 py-1.5"
            onClick={handleDesktopNavigation}
          >
            {nav.downloadDesktop}
          </Link>
          {englishOnly ? <StaticEnglishLanguage /> : <LanguageSwitcher />}
          {isLoggedIn ? (
            <>
              <Button variant="ghost" size="sm" onClick={logout}>
                {nav.logOut}
              </Button>
              <Link href="/dashboard">
                <Button variant="primary" size="sm">
                  {nav.dashboard}
                </Button>
              </Link>
            </>
          ) : (
            <div className="flex items-center gap-3">
              <button
                onClick={() => { trackAuthClick("login", "nav"); login(); }}
                className="text-sm text-secondary hover:text-foreground transition-colors cursor-pointer"
              >
                {nav.logIn}
              </button>
              {primaryCtaHref ? (
                <Link href={primaryCtaHref}>
                  <Button variant="cta" size="sm">
                    {ctaLabel}
                  </Button>
                </Link>
              ) : (
                <Button variant="cta" size="sm" onClick={handleSignUp}>
                  {ctaLabel}
                </Button>
              )}
            </div>
          )}
        </div>

        {/* Mobile hamburger */}
        <button
          className="sm:hidden p-2 text-secondary hover:text-foreground rounded-lg hover:bg-black/5 transition-colors"
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          aria-label="Toggle menu"
          aria-expanded={mobileMenuOpen}
        >
          {mobileMenuOpen ? (
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          ) : (
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
          )}
        </button>
      </div>

      {/* Mobile dropdown menu */}
      {mobileMenuOpen && (
        <div className="sm:hidden border-t border-black/5 px-4 py-4 space-y-3 bg-background/95 backdrop-blur-xl">
          <a
            href={PUBLIC_BRAND.sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="block text-sm text-secondary hover:text-foreground transition-colors py-2"
            onClick={() => setMobileMenuOpen(false)}
          >
            Source
          </a>
          <Link href="/docs" className="block text-sm text-secondary hover:text-foreground transition-colors py-2" onClick={() => setMobileMenuOpen(false)}>
            Docs
          </Link>
          <Link href="/why-magi" className="block text-sm text-secondary hover:text-foreground transition-colors py-2" onClick={() => setMobileMenuOpen(false)}>
            Why Magi
          </Link>
          <Link href="/blog" className="block text-sm text-secondary hover:text-foreground transition-colors py-2" onClick={() => setMobileMenuOpen(false)}>
            Blog
          </Link>
          <Link
            href="/desktop"
            className="block text-sm text-secondary hover:text-foreground transition-colors py-2"
            onClick={(event) => {
              setMobileMenuOpen(false);
              handleDesktopNavigation(event);
            }}
          >
            {nav.downloadDesktop}
          </Link>
          {englishOnly ? <StaticEnglishLanguage /> : <LanguageSwitcher />}
          {isLoggedIn ? (
            <>
              <Button variant="ghost" size="md" className="w-full" onClick={() => { logout(); setMobileMenuOpen(false); }}>
                {nav.logOut}
              </Button>
              <Link href="/dashboard" className="block" onClick={() => setMobileMenuOpen(false)}>
                <Button variant="primary" size="md" className="w-full">
                  {nav.dashboard}
                </Button>
              </Link>
            </>
          ) : (
            <div className="space-y-3">
              {primaryCtaHref ? (
                <Link href={primaryCtaHref} className="block" onClick={() => setMobileMenuOpen(false)}>
                  <Button variant="cta" size="md" className="w-full">
                    {ctaLabel}
                  </Button>
                </Link>
              ) : (
                <Button variant="cta" size="md" className="w-full" onClick={() => { trackAuthClick("signup", "nav_mobile"); setOnboardingOpen(true); setMobileMenuOpen(false); }}>
                  {ctaLabel}
                </Button>
              )}
              <p className="text-center text-sm text-secondary">
                {nav.alreadyHaveAccount}{" "}
                <button
                  onClick={() => { trackAuthClick("login", "nav_mobile"); login(); setMobileMenuOpen(false); }}
                  className="text-primary-light font-medium hover:text-cta transition-colors cursor-pointer"
                >
                  {nav.logIn}
                </button>
              </p>
            </div>
          )}
        </div>
      )}
      {onboardingOpen && (
        <OnboardingModal
          open={onboardingOpen}
          onClose={() => setOnboardingOpen(false)}
          onDeployComplete={handleDeployComplete}
        />
      )}
    </header>
  );
}
